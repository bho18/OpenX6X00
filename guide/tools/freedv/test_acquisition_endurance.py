#!/usr/bin/env python3
"""Check follow-up collection limits and preservation of failed evidence."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_acquisition_endurance as helper


def result(complete=True):
    return {'execution_complete': complete, 'failure_reason': None if complete else 'queue_full',
            'full': {'eoo_blocks': 0, 'audio_ns': 8_040_000_000},
            'timing': {'full_path_input_ratio': {'p99': .60},
                       'ingress_late_more_than_one_interval': 0,
                       'fullpath_one_input_interval_misses': 0,
                       'nominal_release_one_interval_misses': 0},
            'queue': {'dropped_blocks': 0, 'abandoned_blocks': 0, 'full_events': 0}}


class ObservationTests(unittest.TestCase):
    def test_good_no_sync_screen_does_not_grant_hardware_qualification(self):
        r = helper.timing_observations(result(), True, 'no-sync-endurance')
        self.assertEqual(r['status'], 'MET')
        self.assertNotIn('hardware_qualification', r)

    def test_incomplete_or_unvalidated_is_inconclusive(self):
        for data, validated in [(result(False), True), (result(), False)]:
            self.assertEqual(helper.timing_observations(data, validated, 'no-sync-endurance')['status'],
                             'INCONCLUSIVE')

    def test_slow_tail_or_lost_blocks_cannot_meet_screen(self):
        for scope, field, value in [('timing', 'ingress_late_more_than_one_interval', 1),
                                    ('queue', 'dropped_blocks', 1), ('queue', 'full_events', 1)]:
            data = result(); data[scope][field] = value
            self.assertEqual(helper.timing_observations(data, True, 'no-sync-endurance')['status'], 'NOT_MET')
        data = result(); data['timing']['full_path_input_ratio']['p99'] = .71
        self.assertEqual(helper.timing_observations(data, True, 'no-sync-endurance')['status'], 'NOT_MET')

    def test_short_steady_control_does_not_apply_whole_path_screen(self):
        data = result(); data['timing']['full_path_input_ratio']['p99'] = 1.5
        observed = helper.timing_observations(data, True, 'steady-sync')
        self.assertEqual(observed['status'], 'RECORDED')
        self.assertNotIn('p99_full_path_input_ratio_limit', observed)


class PackageTests(unittest.TestCase):
    def test_wrong_package_rejected_before_loading_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'SHA256SUMS').write_text('wrong package\n')
            with patch.object(helper.importlib, 'import_module') as imports:
                with self.assertRaisesRegex(ValueError, 'unchanged acquisition package'):
                    helper.load_package(root)
                imports.assert_not_called()


class CollectionTests(unittest.TestCase):
    def run_case(self, data, return_code, case_name='no-sync-endurance', validation_error=None):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'package'; root.mkdir()
        output = Path(temporary.name) / 'results'
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            destination = Path(command[command.index('--output') + 1])
            if 'inventory.py' in command[1]:
                destination.write_text('{}')
                return subprocess.CompletedProcess(command, 0)
            destination.mkdir()
            (destination/'result.json').write_text(json.dumps(data))
            (destination/'benchmark.stderr').write_text('retained diagnostic stderr')
            return subprocess.CompletedProcess(command, return_code)
        def validate(data, **kwargs):
            self.assertEqual(kwargs['workers'], 3)
            self.assertFalse(kwargs['require_complete'])
            if validation_error: raise ValueError(validation_error)
            return {'execution_validated': data['execution_complete']}
        with patch.object(helper.subprocess, 'run', side_effect=run):
            status = helper.collect(root, output, '{}', 'checksums', validate, case_name)
        return status, output, commands, json.loads((output/'summary.json').read_text())

    def test_fixed_endurance_command_and_no_automatic_qualification(self):
        code, output, commands, summary = self.run_case(result(), 0)
        self.assertEqual(code, 0)
        self.assertEqual(len(commands), 2)
        command = commands[-1]
        for flag, value in [('--seconds','1800'),('--warmup','30'),('--acquisition-workers','3'),('--pacing','paced')]:
            self.assertEqual(command[command.index(flag)+1], value)
        self.assertTrue(command[command.index('--fixture')+1].endswith('/fixtures/silence.raw'))
        self.assertEqual(summary['hardware_qualification'], 'INCONCLUSIVE')
        self.assertEqual(summary['capacity_qualification'], 'INCONCLUSIVE')
        self.assertFalse(summary['automatic_upload'])

    def test_failed_collector_retains_evidence_and_does_not_retry(self):
        code, output, commands, summary = self.run_case(result(False), 1)
        self.assertEqual(code, 1)
        self.assertEqual(len(commands), 2)
        self.assertEqual(summary['failure_reason'], 'queue_full')
        self.assertEqual(summary['timing_observation']['status'], 'INCONCLUSIVE')
        self.assertTrue((output/'silence-acq-3-1800s/result.json').is_file())
        self.assertTrue((output/'silence-acq-3-1800s/benchmark.stderr').is_file())

    def test_invalid_effective_workers_are_not_downgraded_or_retried(self):
        code, output, commands, summary = self.run_case(result(), 0, validation_error='requested/effective workers differ')
        self.assertEqual(code, 1)
        self.assertEqual(len(commands), 2)
        self.assertFalse(summary['execution_validated'])
        self.assertIn('workers differ', summary['error'])
        self.assertTrue((output/'silence-acq-3-1800s/result.json').is_file())

    def test_success_exit_cannot_hide_incomplete_execution(self):
        code, output, commands, summary = self.run_case(result(False), 0)
        self.assertEqual(code, 1)
        self.assertIn('exit status disagrees', summary['error'])

    def test_steady_control_rejects_fixture_ending(self):
        data=result(); data['full']['eoo_blocks']=1
        code, output, commands, summary = self.run_case(data, 0, 'steady-sync')
        self.assertEqual(code, 1)
        self.assertIn('fixture ending', summary['error'])
        self.assertEqual(commands[-1][commands[-1].index('--seconds')+1], '5')
        self.assertEqual(commands[-1][commands[-1].index('--warmup')+1], '3')


if __name__ == '__main__':
    unittest.main()
