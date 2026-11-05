#!/usr/bin/env python3
"""Differential execution and stage-accounting checks; no target timing claims."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import unittest

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--baseline', required=True, type=Path)
PARSER.add_argument('--candidate', required=True, type=Path)
PARSER.add_argument('--fixtures', required=True, type=Path)
PARSER.add_argument('--output', required=True, type=Path)
ARGS = PARSER.parse_args()

CASES = (
    ('tx-warm', 'tx', 'rade-speech.raw', 3, 'unpaced', 1, 'continuous'),
    ('rx-sync-warm', 'rx', 'rade-modem.raw', 3, 'unpaced', 1, 'continuous'),
    ('rx-silence-warm', 'rx', 'silence.raw', 3, 'unpaced', 1, 'continuous'),
    ('rx-interrupted', 'rx', 'rade-modem.raw', 14, 'unpaced', 1, 'interrupt'),
    ('rx-no-warmup', 'rx', 'rade-modem.raw', 3, 'unpaced', 0, 'continuous'),
    ('tx-paced', 'tx', 'rade-speech.raw', 1, 'paced', 1, 'continuous'),
)
TIMING_KEYS = {
    'initialization_ms', 'processing_realtime_factor', 'p99_frame_ratio',
    'max_frame_ratio', 'p99_block_ms', 'max_block_ms', 'deadline_misses',
    'peak_rss_kib_linux',
}
STAGES = {
    'input_resampling', 'tx_speech_processing', 'rade_tx', 'rade_rx',
    'rx_speech_processing', 'output_resampling', 'other',
}


def strict_json(text):
    def reject(value):
        raise ValueError('nonfinite JSON token: ' + value)
    return json.loads(text, parse_constant=reject)


class StageTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ARGS.output.mkdir(parents=True, exist_ok=False)
        cls.results = {}
        for name, direction, fixture, seconds, pacing, warmup, workload in CASES:
            result = {}
            for label, binary in [('baseline', ARGS.baseline), ('candidate', ARGS.candidate)]:
                command = [str(binary.resolve()), direction, str((ARGS.fixtures / fixture).resolve()),
                           str(seconds), pacing, str(warmup), workload]
                run = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
                (ARGS.output / f'{name}-{label}.json').write_text(run.stdout)
                (ARGS.output / f'{name}-{label}.stderr').write_text(run.stderr)
                if run.returncode:
                    raise RuntimeError(f'{name} {label}: exit {run.returncode}: {run.stderr}')
                result[label] = strict_json(run.stdout)
            cls.results[name] = result

    def test_original_functional_fields_preserved(self):
        for name, result in self.results.items():
            with self.subTest(case=name):
                original, candidate = result['baseline'], result['candidate']
                expected = {k: v for k, v in original.items() if k not in TIMING_KEYS}
                self.assertEqual(expected, {k: candidate[k] for k in expected})
                self.assertEqual(set(candidate) - set(original), {'fixture_preparation_ms', 'stage_timing'})

    def test_stage_duration_accounting_and_bounds(self):
        for name, result in self.results.items():
            with self.subTest(case=name):
                value = result['candidate']
                timing = value['stage_timing']
                self.assertEqual(timing['schema_version'], 1)
                self.assertEqual(timing['unit'], 'nanoseconds')
                self.assertTrue(timing['other_is_residual'])
                self.assertEqual(timing['percentile_population'], 'measured_blocks_including_inactive_zeros')
                self.assertEqual(set(timing['stages']), STAGES)
                self.assertEqual(sum(s['sum_ns'] for s in timing['stages'].values()), timing['total_processing_ns'])
                self.assertGreater(timing['total_processing_ns'], 0)
                # Original total is rounded to six decimal places in its RTF.
                self.assertAlmostEqual(timing['total_processing_ns'] / 1e9 / value['audio_seconds'],
                                       value['processing_realtime_factor'], delta=0.000001)
                for stage, stats in timing['stages'].items():
                    with self.subTest(stage=stage):
                        self.assertEqual(set(stats), {'calls', 'active_blocks', 'sum_ns', 'p99_block_ns', 'max_block_ns'})
                        for key, number in stats.items():
                            self.assertIs(type(number), int)
                            self.assertGreaterEqual(number, 0)
                        self.assertEqual(stats['calls'], stats['active_blocks'])
                        self.assertLessEqual(stats['active_blocks'], value['frames'])
                        self.assertLessEqual(stats['p99_block_ns'], stats['max_block_ns'])
                        self.assertLessEqual(stats['max_block_ns'], stats['sum_ns'])
                        # Per-block stages are non-overlapping subintervals of
                        # each total block. Allow formatting's <=500ns rounding.
                        self.assertLessEqual(stats['max_block_ns'], value['max_block_ms'] * 1e6 + 1000)
                        self.assertLessEqual(stats['p99_block_ns'], value['p99_block_ms'] * 1e6 + 1000)
                        if stats['calls'] == 0:
                            self.assertEqual(stats, dict.fromkeys(stats, 0))

    def test_stage_activation_and_warmup_exclusion(self):
        for name, result in self.results.items():
            with self.subTest(case=name):
                value = result['candidate']
                stages = value['stage_timing']['stages']
                frames = value['frames']
                self.assertEqual(stages['input_resampling']['calls'], frames)
                self.assertEqual(stages['other']['calls'], frames)
                if value['direction'] == 'tx':
                    self.assertEqual(stages['rade_tx']['calls'], frames)
                    self.assertEqual(stages['tx_speech_processing']['calls'], frames)
                    self.assertEqual(stages['output_resampling']['calls'], frames)
                    self.assertEqual(stages['rade_rx']['calls'], 0)
                    self.assertEqual(stages['rx_speech_processing']['calls'], 0)
                else:
                    self.assertEqual(stages['rade_rx']['calls'], frames)
                    self.assertEqual(stages['rx_speech_processing']['calls'], value['feature_blocks'])
                    self.assertEqual(stages['rade_tx']['calls'], 0)
                    self.assertEqual(stages['tx_speech_processing']['calls'], 0)
                    self.assertLessEqual(stages['output_resampling']['calls'], value['feature_blocks'])
                if value['output_samples'] == 0:
                    self.assertEqual(stages['output_resampling']['calls'], 0)
                if name == 'rx-silence-warm':
                    self.assertEqual(value['feature_blocks'], 0)
                    self.assertEqual(stages['rx_speech_processing']['calls'], 0)
                # Warm-up is exercised and excluded from stage calls rather
                # than only from the original frame counter.
                if name == 'rx-no-warmup':
                    self.assertEqual(value['warmup_audio_seconds'], 0)
                else:
                    self.assertGreaterEqual(value['warmup_audio_seconds'], 1)

    def test_startup_and_existing_timing_fields_finite(self):
        for name, result in self.results.items():
            with self.subTest(case=name):
                value = result['candidate']
                for field in ('fixture_preparation_ms', 'initialization_ms',
                              'processing_realtime_factor', 'p99_frame_ratio',
                              'max_frame_ratio', 'p99_block_ms', 'max_block_ms'):
                    self.assertTrue(math.isfinite(value[field]))
                    self.assertGreater(value[field], 0)
                self.assertFalse(value['radio_io'])
                self.assertFalse(value['full_pipeline_qualified'])


if __name__ == '__main__':
    unittest.main(argv=['test_stage_timing.py'], verbosity=2)
