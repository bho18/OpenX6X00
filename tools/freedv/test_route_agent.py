"""Retired hardware trial controls must not be revived by host automation."""
import json
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

import route_agent as a
import build_native_test_package


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = patch.object(a, 'CONTROL', self.root)
        self.control.start()
        self.addCleanup(self.control.stop)
        self.status()

    def status(self, **values):
        state = {'state': 1, 'monotonic_ms': time.monotonic() * 1000}
        state.update(values)
        (self.root / 'status.json').write_text(json.dumps(state))

    def test_all_legacy_stages_reject_before_control_or_ack_access(self):
        cases = ('record-hand-zero', 'record-hand-saved', 'record-ordinary-zero',
                 'record-ordinary-saved', 'modem-waveform', 'modem-prekey-gain',
                 'record-then-modem', 'live-speech', 'receive', None)
        with patch.object(a, 'atomic') as write, patch.object(a, 'read_status') as read:
            for case in cases:
                for repeat in (False, True):
                    with self.subTest(case=case, repeat=repeat):
                        with self.assertRaisesRegex(ValueError, 'withdrawn'):
                            a.stage(case, repeat, {'firmware_commit': 'old-build'})
            write.assert_not_called()
            read.assert_not_called()
        self.assertEqual([p.name for p in self.root.iterdir()], ['status.json'])

    def test_cli_stage_rejects_before_manifest_or_device_access(self):
        script = Path(a.__file__)
        result = subprocess.run([sys.executable, str(script), 'stage', '--manifest',
                                 str(self.root / 'absent.json'), '--case', 'record-then-modem',
                                 '--repeat-five'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('withdrawn', report['reason'])
        self.assertNotIn('No such file', report['reason'])
        help_result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
        self.assertNotIn('record-then-modem', help_result.stdout)
        self.assertNotIn('modem-prekey-gain', help_result.stdout)

    def test_preflight_is_read_only_inventory_and_never_ready_to_transmit(self):
        identity = {'firmware_commit': 'candidate', 'gui_sha256': 'a' * 64}
        installed = self.root / 'build-manifest.json'
        installed.write_text(json.dumps(identity))
        calls = []
        def inventory(args):
            calls.append(args)
            return {'exit': 0, 'stdout': 'synthetic read-only inventory'}
        def mapped_path(value):
            if str(value).endswith('build-manifest.json'):
                return installed
            return Path(value)
        with patch.object(a, 'Path', side_effect=mapped_path), patch.object(a, 'sha', return_value='a' * 64), \
                patch.object(a, 'command', side_effect=inventory), patch.object(a, 'atomic') as write:
            report = a.preflight(identity)
        self.assertEqual(report['status'], 'INVENTORIED')
        self.assertTrue(report['inventory_complete'])
        self.assertEqual(report['missing_observations'], [])
        self.assertFalse(report['hardware_qualified'])
        self.assertFalse(report['native_voice_available'])
        self.assertFalse(report['route_requested'])
        self.assertEqual(report['effective_route_state'], 'UNKNOWN')
        self.assertEqual(report['legacy_route_staging'], 'WITHDRAWN')
        self.assertIn(['pactl', 'list', 'sources'], calls)
        self.assertIn(['pactl', 'list', 'sinks'], calls)
        self.assertIn(['cat', '/proc/bus/input/devices'], calls)
        self.assertTrue(all(args[0] in ('uname', 'pactl', 'amixer', 'cat', 'ps') for args in calls))
        write.assert_not_called()

    def test_inventory_cannot_hide_missing_audio(self):
        identity = {'firmware_commit': 'candidate', 'gui_sha256': 'a' * 64}
        installed = self.root / 'build-manifest.json'
        installed.write_text(json.dumps(identity))
        with patch.object(a, 'Path', side_effect=lambda value: installed if str(value).endswith('build-manifest.json') else Path(value)), \
                patch.object(a, 'sha', return_value='a' * 64), patch.object(a, 'command', return_value={'exit': 1}):
            report = a.preflight(identity)
        self.assertEqual(report['status'], 'INVENTORY_INCOMPLETE')
        self.assertFalse(report['inventory_complete'])
        self.assertIn('audio', report['missing_observations'])
        self.assertIn('playback', report['missing_observations'])
        self.assertIn('inputs', report['missing_observations'])
        self.assertIn('sound', report['missing_observations'])

    def test_individual_sink_input_and_pcm_failures_remain_visible(self):
        identity = {'firmware_commit': 'candidate', 'gui_sha256': 'a' * 64}
        installed = self.root / 'build-manifest.json'
        installed.write_text(json.dumps(identity))
        for name, unavailable in (('playback', ['pactl', 'list', 'sinks']),
                                  ('inputs', ['cat', '/proc/bus/input/devices']),
                                  ('sound', ['cat', '/proc/asound/pcm'])):
            with self.subTest(name=name):
                def command(args):
                    return {'exit': 1 if args == unavailable else 0, 'stdout': 'synthetic inventory'}
                with patch.object(a, 'Path', side_effect=lambda value: installed if str(value).endswith('build-manifest.json') else Path(value)), \
                        patch.object(a, 'sha', return_value='a' * 64), patch.object(a, 'command', side_effect=command):
                    report = a.preflight(identity)
                self.assertEqual(report['status'], 'INVENTORY_INCOMPLETE')
                self.assertEqual(report['missing_observations'], [name])
                self.assertFalse(report['native_voice_available'])

    def test_status_remains_a_fresh_observation_without_staging(self):
        self.assertEqual(a.read_status()['state'], 1)
        self.status(monotonic_ms=time.monotonic() * 1000 - 3000)
        with self.assertRaisesRegex(ValueError, 'stale'):
            a.read_status()
        (self.root / 'status.json').write_text('{"state":1}')
        with self.assertRaisesRegex(ValueError, 'stale'):
            a.read_status()

    def test_historical_assessment_never_recommends_retired_trial(self):
        empty = a.next_case({})
        self.assertEqual(empty['status'], 'BLOCKED')
        self.assertIn('receive-only', empty['next'])
        self.assertEqual(a.next_case({'issues': ['missing capture']})['status'], 'INCONCLUSIVE')
        failed = a.next_case({'cases': [{'voice_assessment': {'input_activity': {'status': 'NEAR_CONSTANT'}}}]})
        self.assertEqual(failed['status'], 'FAIL')
        self.assertIn('no new route/gain trial', failed['next'])
        self.assertEqual(a.next_case({'cases': [{}]})['status'], 'INCONCLUSIVE')

    def test_packaged_helpers_run_without_checkout_and_do_not_revive_staging(self):
        archive = self.root / 'kit.zip'
        with contextlib.redirect_stdout(io.StringIO()):
            build_native_test_package.build(archive)
        extracted = self.root / 'standalone-kit'
        with zipfile.ZipFile(archive) as source:
            source.extractall(extracted)
        blocked = subprocess.run([sys.executable, str(extracted / 'route_agent.py'),
                                  'stage', '--case', 'record-then-modem'], cwd=extracted,
                                 capture_output=True, text=True)
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        self.assertIn('withdrawn', json.loads(blocked.stdout)['reason'])
        report = extracted / 'unknown-report.json'
        report.write_text('{}')
        assessed = subprocess.run([sys.executable, str(extracted / 'assess_native_conversation.py'),
                                   str(report)], cwd=extracted, capture_output=True, text=True)
        self.assertEqual(assessed.returncode, 2, assessed.stderr)
        self.assertEqual(json.loads(assessed.stdout)['status'], 'BLOCKED')
        self.assertTrue((extracted / 'native-half-duplex-qualification.md').is_file())


if __name__ == '__main__':
    unittest.main()
