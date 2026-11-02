#!/usr/bin/env python3
"""Evidence-validator corruption checks; no radio devices or codec model required."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from pipeline_evidence import strict_json, validate_pipeline_result, compare_pipeline_pair


def example():
    counters = dict(frames=25, audio_ns=3_000_000_000, output_samples=0, resampled_output_samples=0,
                    sync_blocks=0, feature_blocks=0, nonzero_output_samples=0,
                    interrupted_input_samples=0, sync_blocks_after_first_interrupt=0, eoo_blocks=0,
                    feature_hash='0000000000000000', native_float_hash='0000000000000000',
                    pcm_s16_hash='0000000000000000', metadata_hash='0000000000000000')
    timing = dict(wall_ns_including_drain=4_000_000, summed_service_ns=3_000_000,
                  a_service_ns=2_000_000, b_service_ns=1_000_000, a_cpu_ns=1_000_000, b_cpu_ns=500_000,
                  fullpath_one_input_interval_misses=0, nominal_release_one_interval_misses=None,
                  ingress_late_more_than_one_interval=0, ingress_late_max_ns=0,
                  maximum_virtual_input_backlog_ns_including_warmup=0)
    for name in ('a_service_ms', 'b_service_ms', 'full_path_ms', 'queue_residence_ms',
                 'a_service_input_ratio', 'full_path_input_ratio', 'input_resampling_ms',
                 'rade_rx_ms', 'fargan_ms', 'output_resampling_ms'):
        timing[name] = dict(count=25, mean=0.1, p99=0.2, max=0.2)
    for name in ('b_service_feature_ratio', 'nominal_release_to_completion_ms', 'output_completion_period_ms'):
        timing[name] = dict(count=0, mean=None, p99=None, max=None)
    return dict(schema_version=1, experiment='rade-rx-two-worker', execution='serial', pacing='unpaced',
                workload='continuous', radio_io=False, full_pipeline_qualified=False, workers_joined=True,
                original_70_percent_assessment='unchanged_serial_reference_required', test_hooks_used=False,
                checksum_algorithm='fnv1a64-noncryptographic', checksum_cost_in_service_timing=True,
                release_semantics='last_input_sample_available_fixed_cumulative_nin',
                queue_policy='block-unpaced-only', execution_complete=True, failure_reason=None,
                attempted_blocks=25, processed_input_blocks=25, submitted_blocks=25, completed_blocks=25,
                warmup_audio_ns=0, full=copy.deepcopy(counters), measured=counters, timing=timing,
                queue=dict(capacity=2, capacity_excludes_b_private_record=True, max_depth=0, full_events=0,
                           producer_wait_ns=0, consumer_wait_ns=0, dropped_blocks=0, abandoned_blocks=0))


def validate(value, **kwargs):
    return validate_pipeline_result(value, execution='serial', pacing='unpaced', workload='continuous',
                                    seconds=3, warmup=0, fixture_name='silence', **kwargs)


class EvidenceTests(unittest.TestCase):
    def test_execution_does_not_qualify_capacity(self):
        self.assertEqual(validate(example())['capacity_qualification'], 'INCONCLUSIVE')

    def test_reject_corrupt_counters_and_populations(self):
        mutations = [lambda d: d.update(workers_joined=False),
                     lambda d: d.update(execution='parallel'),
                     lambda d: d.update(test_hooks_used=True),
                     lambda d: d.update(queue_policy='fail-on-full'),
                     lambda d: d.update(processed_input_blocks=26),
                     lambda d: d['queue'].update(dropped_blocks=1),
                     lambda d: d['queue'].update(abandoned_blocks=1),
                     lambda d: d['measured'].update(audio_ns=4_000_000_000),
                     lambda d: d['measured'].update(sync_blocks_after_first_interrupt=1),
                     lambda d: d['measured'].update(feature_hash='invalid'),
                     lambda d: d['timing']['rade_rx_ms'].update(count=24),
                     lambda d: d['timing']['fargan_ms'].update(count=0),
                     lambda d: d['timing']['b_service_feature_ratio'].update(count=25),
                     lambda d: d['timing']['output_completion_period_ms'].update(count=1),
                     lambda d: d['timing']['full_path_ms'].update(p99=float('nan')),
                     lambda d: d['timing'].update(summed_service_ns=1),
                     lambda d: d['timing'].update(nominal_release_one_interval_misses=0)]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                value = example(); mutate(value)
                with self.assertRaises(ValueError): validate(value)

    def test_incomplete_stays_unvalidated(self):
        value = example(); value.update(execution_complete=False, failure_reason='signal')
        self.assertFalse(validate(value, require_complete=False)['execution_validated'])
        with self.assertRaises(ValueError): validate(value)

    def test_pair_compares_hashes_and_counters(self):
        serial, parallel = example(), example()
        parallel['execution'] = 'parallel'
        self.assertEqual(compare_pipeline_pair(serial, parallel)['status'], 'MATCH')
        parallel['measured']['pcm_s16_hash'] = '1111111111111111'
        self.assertEqual(compare_pipeline_pair(serial, parallel)['status'], 'MISMATCH')
        parallel['execution_complete'] = False
        self.assertEqual(compare_pipeline_pair(serial, parallel)['status'], 'INCONCLUSIVE')

    def test_nonfinite_json_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            with self.assertRaises(ValueError): strict_json('{"v":' + value + '}')


class CollectorTests(unittest.TestCase):
    def collect(self, mode, direction, execution=None, return_code=0):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        scripts = root / 'tools/freedv'; scripts.mkdir(parents=True)
        shutil.copy2(Path(__file__).with_name('collect_benchmark.py'), scripts / 'collect_benchmark.py')
        (scripts / 'sources.json').write_text('{}')
        fixture = root / 'silence.raw'; fixture.write_bytes(bytes(32))
        executable = root / 'fake-benchmark'
        identity = dict(radio_io=False, full_pipeline_qualified=False, pacing='unpaced')
        if execution:
            identity.update(experiment='rade-rx-two-worker', execution=execution, workload='continuous')
        else:
            identity.update(mode=mode, direction=direction)
        executable.write_text('#!' + sys.executable + '\nimport sys,json\n'
                              + 'result=' + repr(identity) + '\n'
                              + 'result["test_argv"]=sys.argv[1:]\nprint(json.dumps(result))\n'
                              + 'sys.exit(' + str(return_code) + ')\n')
        executable.chmod(0o755)
        output = root / 'result'
        command = [sys.executable, str(scripts / 'collect_benchmark.py'), '--mode', mode,
                   '--direction', direction, '--benchmark', str(executable), '--fixture', str(fixture),
                   '--seconds', '3', '--warmup', '1', '--pacing', 'unpaced', '--output', str(output)]
        if execution: command += ['--execution', execution]
        done = subprocess.run(command, capture_output=True, text=True)
        return done, output, fixture

    def test_original_700e_cli_preserved(self):
        done, output, fixture = self.collect('700E', 'tx')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads((output / 'result.json').read_text())['test_argv'],
                         ['tx', str(fixture), '3', 'unpaced', 'resample', '1'])
        self.assertNotIn('execution', json.loads((output / 'manifest.json').read_text()))

    def test_original_rade_cli_preserved(self):
        done, output, fixture = self.collect('RADE-V1', 'rx')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads((output / 'result.json').read_text())['test_argv'],
                         ['rx', str(fixture), '3', 'unpaced', '1', 'continuous'])

    def test_pipeline_failure_json_retained(self):
        done, output, fixture = self.collect('RADE-V1', 'rx', 'parallel', return_code=3)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(json.loads((output / 'result.json').read_text())['test_argv'],
                         ['parallel', str(fixture), '3', 'unpaced', '1', 'continuous'])
        self.assertEqual(json.loads((output / 'events.json').read_text())[-1]['return_code'], 3)

    def test_pipeline_rejects_other_modes(self):
        for mode, direction in [('700E', 'rx'), ('RADE-V1', 'tx')]:
            with self.subTest(mode=mode, direction=direction):
                done, output, _ = self.collect(mode, direction, 'parallel')
                self.assertEqual(done.returncode, 2)
                self.assertFalse(output.exists())


class OriginalSerialComparisonTests(unittest.TestCase):
    def traces(self):
        from test_validate_rade_stage import example as stage_example
        original = stage_example('rx')
        original.update(schema_version=1, pacing='unpaced', workload='continuous', radio_io=False,
                        full_pipeline_qualified=False, resampling_included=True, deadline_misses=None,
                        warmup_audio_seconds=1.08, resampled_output_samples=5292, sync_blocks=1,
                        nonzero_output_samples=5000, interrupted_input_samples=0,
                        sync_blocks_after_first_interrupt=0)
        keys = ('frames', 'output_samples', 'resampled_output_samples', 'sync_blocks', 'feature_blocks',
                'nonzero_output_samples', 'interrupted_input_samples', 'sync_blocks_after_first_interrupt')
        serial = {'measured': {key: original[key] for key in keys}, 'warmup_audio_ns': 1_080_000_000}
        serial['measured']['audio_ns'] = 240_000_000
        return serial, original

    def test_original_counters_durations_and_stages_match(self):
        from validate_rade_pipeline_sdk import compare_original_stage
        serial, original = self.traces()
        result = compare_original_stage(serial, original, workload='continuous')
        self.assertEqual(result['status'], 'MATCH')
        self.assertTrue(result['original_stage_accounting']['accounting_checked'])

    def test_original_counter_duration_stage_corruption_rejected(self):
        from validate_rade_pipeline_sdk import compare_original_stage
        mutations = [lambda d: d.update(pacing='paced'),
                     lambda d: d.update(nonzero_output_samples=4999),
                     lambda d: d.update(sync_blocks=True),
                     lambda d: d.update(warmup_audio_seconds=1.2),
                     lambda d: d.update(audio_seconds=0.36),
                     lambda d: d['stage_timing'].update(total_processing_ns=1)]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                serial, original = self.traces(); mutate(original)
                with self.assertRaises(ValueError):
                    compare_original_stage(serial, original, workload='continuous')


if __name__ == '__main__':
    unittest.main()
