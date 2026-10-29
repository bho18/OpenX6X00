#!/usr/bin/env python3
"""Failure and integrity checks for the fixed short radio suite."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_short_validation as suite


def frame_result(mode='RADE-V1', direction='tx', fixture='rade-speech', ratio=.8):
    return {'mode': mode, 'direction': direction, 'pacing': 'paced', 'frames': 1000,
            'radio_io': False, 'full_pipeline_qualified': False, 'resampling_included': True,
            'sync_frames': 0 if fixture == 'silence' else 900,
            'resampled_output_samples': 0 if fixture == 'silence' else 44100,
            'p99_frame_ratio': ratio, 'deadline_misses': 0, 'audio_seconds': 120,
            'warmup_audio_seconds': 5}


class ShortValidationTest(unittest.TestCase):
    def test_noise_is_reproducible_nonzero_pcm(self):
        pcm = suite.noise_pcm()
        self.assertEqual(len(pcm), 32000)
        self.assertEqual(hashlib.sha256(pcm).hexdigest(), '2218159f25b72c1b6986b3fe56c7806c5437e1884b1971728a274678168a4af9')
        self.assertNotEqual(pcm, bytes(len(pcm)))

    def test_commands_use_short_fixed_durations_and_parallel_noise(self):
        for _, mode, direction, fixture in suite.CASES:
            cmd = suite.command(Path('/package'), Path('/output'), mode, direction, fixture)
            self.assertEqual(cmd[cmd.index('--seconds')+1], '120')
            self.assertEqual(cmd[cmd.index('--warmup')+1], '5')
            if fixture == 'receiver-noise':
                self.assertEqual(cmd[cmd.index('--acquisition-workers')+1], '3')
                self.assertEqual(cmd[cmd.index('--fixture')+1], '/output/receiver-noise.raw')
            else:
                self.assertNotIn('--execution', cmd)

    def test_margin_failure_does_not_hide_valid_execution(self):
        out = suite.assess(frame_result(ratio=.700001), 'RADE-V1', 'tx', 'rade-speech')
        self.assertEqual(out['original_70_percent_margin'], 'FAIL')
        self.assertFalse(out['full_qualification'])

    def test_rejects_invalid_or_incomplete_evidence(self):
        for field, value in [('audio_seconds', 1), ('audio_seconds', True), ('radio_io', True),
                             ('resampling_included', False), ('p99_frame_ratio', float('nan')),
                             ('p99_frame_ratio', True), ('deadline_misses', None), ('frames', 0),
                             ('resampled_output_samples', 0), ('direction', 'rx')]:
            result = frame_result(); result[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                suite.assess(result, 'RADE-V1', 'tx', 'rade-speech')

    def test_noise_and_signal_sync_expectations(self):
        result = frame_result(mode='700E', direction='rx', fixture='silence')
        suite.assess(result, '700E', 'rx', 'silence')
        result['sync_frames'] = 1
        with self.assertRaises(ValueError): suite.assess(result, '700E', 'rx', 'silence')
        result['sync_frames'] = 0
        with self.assertRaises(ValueError): suite.assess(result, '700E', 'rx', '700e-modem')

    def test_tampered_package_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'SHA256SUMS').write_text('tampered')
            with patch.object(suite.importlib, 'import_module') as imported:
                with self.assertRaises(ValueError): suite.load_package(root)
                imported.assert_not_called()

    def test_strict_json(self):
        for text in ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}']:
            with self.assertRaises(ValueError): suite.strict_json(text)

    def exercise(self, output, fail_first=False, interrupt=False, cases=None):
        def collector(cmd):
            if interrupt: raise KeyboardInterrupt
            name = Path(cmd[-1]).name
            case = next(c for c in suite.CASES if c[0] == name)
            if fail_first and name == suite.CASES[0][0]: return SimpleNamespace(returncode=1)
            dest = Path(cmd[-1]); dest.mkdir()
            value = frame_result(*case[1:])
            if name == 'rade-noise':
                value = {'timing': {'full_path_input_ratio': {'p99': .6},
                                    'ingress_late_more_than_one_interval': 0},
                         'queue': {'dropped_blocks': 0, 'full_events': 0}}
            (dest/'result.json').write_text(json.dumps(value))
            return SimpleNamespace(returncode=0)
        with patch.object(suite, 'preflight'), patch.object(suite.subprocess, 'run', side_effect=collector):
            return suite.run_suite(Path('/verified-package'), output, lambda *a, **k: None, cases)

    def test_missing_execute_bits_caught_before_running_cases(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'bin').mkdir()
            for name in ['rade-frame-bench', 'codec2-frame-bench', 'rade-pipeline-bench']:
                p = root/'bin'/name; p.write_bytes(b'test'); p.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'execute permission missing'):
                suite.verify_executables(root, suite.CASES)
            for p in (root/'bin').iterdir(): p.chmod(0o700)
            suite.verify_executables(root, suite.CASES)

    def test_retry_four_cases_does_not_repeat_noise_or_claim_full_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            out = self.exercise(Path(d), cases=suite.CASES[:4])
            self.assertTrue(out['complete'])
            self.assertTrue(out['selected_coverage_complete'])
            self.assertFalse(out['coverage_complete'])
            self.assertNotIn('rade-noise', out['selected_cases'])
            self.assertEqual(len(out['cases']), 4)

    def test_main_missing_permissions_exits_before_inventory_or_output_creation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'package'; (root/'bin').mkdir(parents=True)
            for name in ['rade-frame-bench', 'codec2-frame-bench', 'rade-pipeline-bench']:
                p = root/'bin'/name; p.write_bytes(b'test'); p.chmod(0o600)
            output = Path(d)/'results'
            with patch.object(suite.platform, 'machine', return_value='armv7l'), \
                 patch.object(suite, 'load_package', return_value=('{}', '', None)), \
                 patch.object(suite.sys, 'argv', ['runner', '--package', str(root), '--output', str(output)]), \
                 patch.object(suite.subprocess, 'run') as run:
                with self.assertRaises(SystemExit) as exc:
                    suite.main()
                self.assertEqual(exc.exception.code, 2)
                run.assert_not_called()
                self.assertFalse(output.exists())

    def test_failure_preserved_and_remaining_cases_execute(self):
        with tempfile.TemporaryDirectory() as d:
            out = self.exercise(Path(d), fail_first=True)
            self.assertFalse(out['complete'])
            self.assertTrue(out['coverage_complete'])
            self.assertEqual(len(out['cases']), 5)
            self.assertIn('error', out['cases'][0])
            self.assertTrue(all(c['execution_validated'] for c in out['cases'][1:]))
            self.assertEqual(json.loads((Path(d)/'summary.json').read_text()), out)

    def test_timing_failure_is_reported_without_aborting_suite(self):
        with tempfile.TemporaryDirectory() as d:
            out = self.exercise(Path(d))
            self.assertTrue(out['complete'])
            self.assertEqual(out['cases'][0]['timing']['original_70_percent_margin'], 'FAIL')
            self.assertEqual(out['hardware_qualification'], 'INCONCLUSIVE')

    def test_interrupt_preserves_partial_summary_and_propagates(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyboardInterrupt): self.exercise(Path(d), interrupt=True)
            out = json.loads((Path(d)/'summary.json').read_text())
            self.assertFalse(out['complete'])
            self.assertFalse(out['coverage_complete'])
            self.assertEqual(len(out['cases']), 1)


if __name__ == '__main__': unittest.main()
