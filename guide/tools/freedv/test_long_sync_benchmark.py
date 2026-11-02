#!/usr/bin/env python3
"""Test external-fixture boundaries, provenance, memory refusal and failure retention."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_long_sync_benchmark as helper


def result(complete=True):
    return {'execution_complete': complete, 'failure_reason': None if complete else 'queue_full',
            'fixture_preparation_ms': 120000,
            'full': {'eoo_blocks': 0, 'audio_ns': 1_830_000_000_000},
            'measured': {'frames': 15000, 'sync_blocks': 15000, 'feature_blocks': 15000, 'eoo_blocks': 0},
            'timing': {'full_path_input_ratio': {'p99': 1.5},
                       'full_path_ms': {'p99': 180}, 'nominal_release_to_completion_ms': {'p99': 180},
                       'output_completion_period_ms': {'p99': 121},
                       'ingress_late_more_than_one_interval': 0,
                       'maximum_virtual_input_backlog_ns_including_warmup': 1000000},
            'queue': {'max_depth': 1, 'dropped_blocks': 0, 'abandoned_blocks': 0, 'full_events': 0}}


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.raw = cls.root / 'fixture.raw'
        with cls.raw.open('wb') as stream:
            stream.write(b'\1\0')
            stream.truncate((15500 * 960 + 1152) * 2)
        cls.raw_sha = helper.digest(cls.raw)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.native = result()
        native_text = json.dumps(self.native)
        (self.root / 'native-validation.json').write_text(native_text)
        self.manifest = {
            'schema_version': 1, 'kind': 'rade-v1-continuous-fixture', 'revision': 'rade-v1-long-sync-1',
            'radio_io': False, 'radio_qualified': False, 'cross_host_bit_reproducible': False,
            'fixture': {'path': 'fixture.raw', 'sha256': self.raw_sha,
                        'bytes': self.raw.stat().st_size, 'samples': 15500 * 960 + 1152,
                        'sample_rate_hz': 8000, 'channels': 1, 'sample_format': 'signed-16-bit-little-endian',
                        'first_eoo_sample': 15500 * 960, 'normal_modem_frames': 15500,
                        'modem_samples_per_frame': 960, 'terminal_eoo_samples': 1152},
            'sources': dict(helper.SOURCE_PINS),
            'generation': {'producer_source_sha256': 'a' * 64,
                           'generator_source_sha256': helper.GENERATOR_SOURCE_SHA256,
                           'native_host_only': True, 'one_transmitter_session': True,
                           'intermediate_eoo_frames': 0, 'speech_seconds': 1860,
                           'speech_rate_hz': 16000, 'speech_samples': 29_760_000,
                           'code_commit': 'b' * 40, 'compiler_version': 'test compiler'},
            'native_validation': {'passed': True, 'radio_timing_qualified': False, 'seconds': 1800,
                                  'warmup_seconds': 30, 'execution': 'serial', 'pacing': 'unpaced',
                                  'result_file': 'native-validation.json',
                                  'result_sha256': hashlib.sha256(native_text.encode()).hexdigest(),
                                  'measured_frames': 15000, 'measured_sync_blocks': 15000,
                                  'measured_feature_blocks': 15000, 'eoo_blocks': 0}}
        for name in ('repeated_speech_wav_sha256', 'generator_binary_sha256', 'generator_library_sha256',
                     'opus_config_sha256', 'host_package_manifest_sha256'):
            self.manifest['generation'][name] = 'c' * 64

    def verify(self, manifest=None, native_validator=None):
        path = self.root / 'fixture-manifest.json'
        path.write_text(json.dumps(manifest or self.manifest))
        calls = []
        def validate(data, **kwargs):
            calls.append(kwargs)
            if native_validator: native_validator(data, **kwargs)
        with patch.object(helper, 'PRODUCER_SOURCE_SHA256', 'a' * 64):
            verified = helper.verify_fixture(self.raw, path, validate)
        self.assertTrue(calls[0]['require_complete'])
        self.assertEqual(calls[0]['seconds'], 1800)
        return verified

    def test_complete_external_data_contract(self):
        verified = self.verify()
        self.assertEqual(verified['fixture_sha256'], self.raw_sha)
        self.assertEqual(verified['raw_bytes'], 29_762_304)

    def test_model_or_producer_drift_rejected(self):
        for scope, key, value in [('sources', 'opus_model_sha256', 'd' * 64),
                                   ('generation', 'producer_source_sha256', 'd' * 64),
                                   ('generation', 'one_transmitter_session', False)]:
            with self.subTest(key=key):
                manifest = copy.deepcopy(self.manifest); manifest[scope][key] = value
                with self.assertRaises(ValueError): self.verify(manifest)

    def test_raw_hash_and_boundary_are_checked(self):
        for key, value in [('sha256', '0' * 64), ('first_eoo_sample', 1800 * 8000),
                           ('samples', 100), ('sample_rate_hz', 16000)]:
            with self.subTest(key=key):
                manifest = copy.deepcopy(self.manifest); manifest['fixture'][key] = value
                with self.assertRaises(ValueError): self.verify(manifest)

    def test_native_result_must_be_hashed_and_independently_validated(self):
        (self.root / 'native-validation.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'result checksum'):
            self.verify()

    def test_native_summary_cannot_hide_lost_sync(self):
        data = result(); data['measured']['sync_blocks'] -= 1
        text = json.dumps(data); (self.root/'native-validation.json').write_text(text)
        self.manifest['native_validation']['result_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'did not remain synchronized'):
            self.verify()

    def test_native_validator_rejection_is_not_ignored(self):
        def reject(*args, **kwargs): raise ValueError('native duration incomplete')
        with self.assertRaisesRegex(ValueError, 'duration incomplete'):
            self.verify(native_validator=reject)

    def test_nonobject_manifest_sections_are_rejected(self):
        for section in ('fixture', 'sources', 'generation', 'native_validation'):
            with self.subTest(section=section):
                manifest = copy.deepcopy(self.manifest); manifest[section] = []
                with self.assertRaisesRegex(ValueError, 'JSON object required'):
                    self.verify(manifest)

    def test_oversized_or_short_raw_rejected_before_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'fixture.raw'
            for size in (100, helper.MAX_FIXTURE_BYTES):
                with path.open('wb') as stream: stream.truncate(size)
                with patch.object(helper, 'digest') as digest:
                    with self.assertRaisesRegex(ValueError, 'at least 1840 seconds'):
                        helper.verify_fixture(path, self.root/'fixture-manifest.json', None)
                    digest.assert_not_called()


class MemoryAndEvidenceTests(unittest.TestCase):
    def test_memory_threshold_is_after_transfer_headroom_not_total_ram(self):
        self.assertFalse(helper.memory_snapshot('MemTotal: 1000000 kB\nMemAvailable: 614399 kB')['sufficient'])
        self.assertTrue(helper.memory_snapshot('MemAvailable: 614400 kB')['sufficient'])
        with self.assertRaisesRegex(ValueError, 'MemAvailable'):
            helper.memory_snapshot('MemFree: 999999 kB')

    def test_completed_run_requires_every_measured_sync_and_feature(self):
        for scope, key in [('measured', 'sync_blocks'), ('measured', 'feature_blocks'), ('full', 'eoo_blocks')]:
            data = result(); data[scope][key] += 1 if key == 'eoo_blocks' else -1
            with self.assertRaises(ValueError): helper.require_steady_result(data, 15500 * 960)
        helper.require_steady_result(result(False), 15500 * 960)

    def test_pipeline_latency_is_recorded_without_a_pass_label(self):
        observation = helper.timing_observation(result(), True)
        self.assertEqual(observation['status'], 'RECORDED')
        self.assertEqual(observation['p99_full_path_input_ratio'], 1.5)
        self.assertEqual(observation['original_serial_70_percent_gate'], 'unchanged')
        self.assertNotIn('hardware_qualification', observation)
        self.assertEqual(helper.timing_observation(result(False), False)['status'], 'INCONCLUSIVE')

    def test_nonfinite_or_duplicate_json_rejected(self):
        for text in ['{"frames":NaN}', '{"frames":1,"frames":2}']:
            with self.assertRaises(ValueError): helper.strict_json(text)


class CollectionTests(unittest.TestCase):
    def collect(self, data, return_code=0, memory=(700000, 700000), case_name='endurance'):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)/'package'; root.mkdir()
        output = Path(temporary.name)/'results'; raw = Path(temporary.name)/'fixture.raw'
        fixture = {'manifest_text': '{}', 'native_result_text': '{}', 'fixture_sha256': 'a' * 64,
                   'manifest_sha256': 'b' * 64, 'native_result_sha256': 'c' * 64,
                   'raw_bytes': 29_762_304, 'manifest': {'fixture': {'first_eoo_sample': 15500 * 960}}}
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            destination = Path(command[command.index('--output') + 1])
            if 'inventory.py' in command[1]:
                destination.write_text('{}'); return subprocess.CompletedProcess(command, 0)
            destination.mkdir()
            (destination/'result.json').write_text(json.dumps(data))
            (destination/'benchmark.stderr').write_text('retained')
            return subprocess.CompletedProcess(command, return_code)
        def validate(data, **kwargs):
            self.assertEqual(kwargs['workers'], 3)
            self.assertFalse(kwargs['require_complete'])
            return {'execution_validated': data['execution_complete']}
        samples = [helper.memory_snapshot('MemAvailable: %d kB' % available) for available in memory]
        with patch.object(helper.subprocess, 'run', side_effect=run), \
             patch.object(helper, 'memory_snapshot', side_effect=samples):
            code = helper.collect(root, raw, output, '{}', 'checksums', fixture, validate, case_name)
        return code, commands, output, json.loads((output/'summary.json').read_text())

    def test_low_memory_preserves_preflight_without_launch(self):
        code, commands, output, summary = self.collect(result(), memory=(614399,))
        self.assertEqual(code, 1); self.assertEqual(commands, [])
        self.assertIn('600 MiB', summary['error'])
        self.assertTrue((output/'fixture-manifest.json').is_file())

    def test_memory_is_rechecked_immediately_before_launch(self):
        code, commands, output, summary = self.collect(result(), memory=(700000, 614399))
        self.assertEqual(code, 1); self.assertEqual(len(commands), 1)
        self.assertIn('fell below', summary['error'])

    def test_external_fixture_and_fixed_schedule_are_used(self):
        code, commands, output, summary = self.collect(result())
        self.assertEqual(code, 0); self.assertEqual(len(commands), 2)
        for flag, value in [('--seconds','1800'),('--warmup','30'),('--acquisition-workers','3'),('--pacing','paced')]:
            self.assertEqual(commands[-1][commands[-1].index(flag)+1], value)
        self.assertEqual(summary['hardware_qualification'], 'INCONCLUSIVE')
        self.assertFalse(summary['fixture_copied_to_results'])
        self.assertEqual(summary['fixture_preparation_ms'], 120000)

    def test_failed_pipeline_retains_result_without_retry(self):
        code, commands, output, summary = self.collect(result(False), 1)
        self.assertEqual(code, 1); self.assertEqual(len(commands), 2)
        self.assertEqual(summary['failure_reason'], 'queue_full')
        self.assertTrue((output/'sync-acq-3-1800s/result.json').is_file())
        self.assertTrue((output/'sync-acq-3-1800s/benchmark.stderr').is_file())

    def test_smoke_uses_same_fixture_and_workers_with_short_schedule(self):
        code, commands, output, summary = self.collect(result(), case_name='smoke')
        self.assertEqual(code, 0)
        for flag, value in [('--seconds','5'),('--warmup','3'),('--acquisition-workers','3')]:
            self.assertEqual(commands[-1][commands[-1].index(flag)+1], value)
        self.assertEqual(summary['case'], 'smoke')
        self.assertEqual(summary['seconds_requested'], 5)
        self.assertEqual(summary['timing_observation']['scope'], 'three-worker synchronized RX smoke')

    def test_completed_process_with_sync_loss_is_not_validated(self):
        data = result(); data['measured']['feature_blocks'] -= 1
        code, commands, output, summary = self.collect(data)
        self.assertEqual(code, 1); self.assertFalse(summary['execution_validated'])
        self.assertIn('did not remain synchronized', summary['error'])


if __name__ == '__main__':
    unittest.main()
