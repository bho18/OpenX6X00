#!/usr/bin/env python3
"""Reject corrupt stage evidence; optionally verify real host result files."""
import argparse
import json
from pathlib import Path
import unittest

from validate_rade_acq_sdk import validate_rade_stage_timing


def example(direction='tx', speech=True):
    # Independent two-block trace in nanoseconds; all p99 values equal maxima.
    tx = direction == 'tx'
    traces = {'input_resampling': [100000, 100000],
              'tx_speech_processing': [1000000, 1200000] if tx else [0, 0],
              'rade_tx': [3000000, 4000000] if tx else [0, 0],
              'rade_rx': [0, 0] if tx else [3000000, 4000000],
              'rx_speech_processing': [0, 1200000] if not tx and speech else [0, 0],
              'output_resampling': [200000, 200000] if tx else ([0, 200000] if speech else [0, 0]),
              'other': [100000, 100000]}
    blocks = [sum(values[i] for values in traces.values()) for i in (0, 1)]
    total = sum(blocks)
    return {'mode': 'RADE-V1', 'direction': direction, 'frames': 2,
            'feature_blocks': int(not tx and speech), 'output_samples': 1920 if tx or speech else 0,
            'fixture_preparation_ms': 12.3, 'audio_seconds': 0.24,
            'processing_realtime_factor': float(f'{total / 1e9 / 0.24:.6f}'),
            'max_block_ms': max(blocks) / 1e6, 'p99_block_ms': max(blocks) / 1e6,
            'stage_timing': {'schema_version': 1, 'unit': 'nanoseconds',
                             'other_is_residual': True,
                             'calls_semantics': 'timed_regions_other_counts_residual_evaluations',
                             'percentile_population': 'measured_blocks_including_inactive_zeros',
                             'total_processing_ns': total,
                             'stages': {name: {'calls': sum(value > 0 for value in values),
                                               'active_blocks': sum(value > 0 for value in values),
                                               'sum_ns': sum(values), 'p99_block_ns': max(values),
                                               'max_block_ns': max(values)}
                                        for name, values in traces.items()}}}


def mutate(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    if replacement is DELETE:
        del value[path[-1]]
    else:
        value[path[-1]] = replacement


DELETE = object()


class StageValidationTests(unittest.TestCase):
    def test_accepts_tx_rx_and_no_sync_traces(self):
        for direction, speech in [('tx', True), ('rx', True), ('rx', False)]:
            with self.subTest(direction=direction, speech=speech):
                result = validate_rade_stage_timing(example(direction, speech))
                self.assertTrue(result['accounting_checked'])
                self.assertEqual(result['measured_frames'], 2)

    def test_rejects_missing_schema_and_invalid_numeric_fields(self):
        cases = [
            (('stage_timing',), DELETE),
            (('stage_timing', 'schema_version'), True),
            (('stage_timing', 'unit'), 'milliseconds'),
            (('stage_timing', 'other_is_residual'), False),
            (('stage_timing', 'percentile_population'), 'active_blocks_only'),
            (('stage_timing', 'calls_semantics'), 'neural_layers'),
            (('stage_timing', 'stages', 'rade_tx'), DELETE),
            (('stage_timing', 'stages', 'unexpected'), {}),
            (('stage_timing', 'stages', 'input_resampling', 'calls'), True),
            (('stage_timing', 'stages', 'input_resampling', 'sum_ns'), -1),
            (('stage_timing', 'stages', 'input_resampling', 'max_block_ns'), 100000.0),
            (('stage_timing', 'total_processing_ns'), False),
            (('stage_timing', 'total_processing_ns'), 1 << 100),
            (('frames',), 2.0),
            (('fixture_preparation_ms',), DELETE),
            (('fixture_preparation_ms',), float('nan')),
            (('fixture_preparation_ms',), float('inf')),
            (('processing_realtime_factor',), float('inf')),
            (('processing_realtime_factor',), 1e308),
        ]
        for path, replacement in cases:
            with self.subTest(path=path, replacement=replacement):
                result = example()
                mutate(result, path, replacement)
                with self.assertRaises(ValueError):
                    validate_rade_stage_timing(result)

    def test_rejects_timer_and_percentile_corruption(self):
        cases = [
            (('stage_timing', 'total_processing_ns'), 9999999),
            (('processing_realtime_factor',), 0.25),
            (('max_block_ms',), 0.001),
            (('stage_timing', 'stages', 'rade_tx', 'p99_block_ns'), 4000001),
            (('stage_timing', 'stages', 'rade_tx', 'sum_ns'), 3999999),
            (('stage_timing', 'stages', 'rade_tx', 'sum_ns'), 8000001),
        ]
        for path, replacement in cases:
            with self.subTest(path=path):
                result = example()
                mutate(result, path, replacement)
                with self.assertRaises(ValueError):
                    validate_rade_stage_timing(result)

    def test_rejects_warmup_and_direction_activation_corruption(self):
        cases = [
            (example(), ('stage_timing', 'stages', 'input_resampling', 'calls'), 3),
            (example(), ('stage_timing', 'stages', 'other', 'active_blocks'), 1),
            (example(), ('stage_timing', 'stages', 'rade_rx', 'sum_ns'), 1),
            (example('rx'), ('feature_blocks',), 2),
            (example('rx'), ('output_samples',), 0),
            (example('rx', False), ('stage_timing', 'stages', 'rx_speech_processing', 'calls'), 1),
        ]
        for result, path, replacement in cases:
            with self.subTest(path=path):
                mutate(result, path, replacement)
                with self.assertRaises(ValueError):
                    validate_rade_stage_timing(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-results', type=Path, help='directory containing real *-candidate.json host results')
    args = parser.parse_args()
    run = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StageValidationTests))
    if not run.wasSuccessful():
        raise SystemExit(1)
    if args.host_results:
        paths = sorted(args.host_results.glob('*-candidate.json'))
        if not paths:
            raise SystemExit('No real candidate result files found')
        for path in paths:
            validate_rade_stage_timing(json.loads(path.read_text()))
        print(f'PASS: SDK stage validator accepted {len(paths)} real host workload reports')
