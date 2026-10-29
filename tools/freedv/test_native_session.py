#!/usr/bin/env python3
"""Evidence integrity and false-pass regression checks; no radio access."""
import copy
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import zipfile

import native_session as session


def valid_case():
    return {'name': 'Manual Stop test', 'restoration_complete': True, 'readback_match': 1,
        'run_started': True, 'preparation_failed': False, 'seen_packet_tx': True,
        'first_stop_reason': 1, 'audio_failures': 0, 'radio_before': {'mic': 2},
        'radio_after': {'mic': 2}, 'alsa_before': {'ADC': [160, 160]},
        'alsa_after': {'ADC': [160, 160]}, 'transport_failures': 0, 'restoration_failures': 0,
        'command_clear_packet_sequence': 10,
        'radio_at_result_export': {'physical_packet': {'known': True, 'fresh': True, 'tx': False, 'sequence': 11}},
        'audio': {'capture_dropped_samples': 0, 'capture_hole_samples': 0, 'capture_errors': 0,
                  'playback_write_errors': 0, 'playback_flush_acknowledged': 1}}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_recovery_needs_fresh_subsequent_rx_and_exact_restoration(self):
        good = valid_case()
        self.assertEqual(session.recovery(good), 'PASS')
        for field, value in [('fresh', False), ('tx', True), ('sequence', 10), ('known', False)]:
            bad = copy.deepcopy(good)
            bad['radio_at_result_export']['physical_packet'][field] = value
            self.assertEqual(session.recovery(bad), 'FAIL', field)
        for path in [('radio_after', {'mic': 0}), ('alsa_after', {}), ('first_stop_reason', 3),
                     ('run_started', False), ('preparation_failed', True), ('transport_failures', 1)]:
            bad = copy.deepcopy(good); bad[path[0]] = path[1]
            self.assertEqual(session.recovery(bad), 'FAIL', path)
        bad = copy.deepcopy(good); del bad['audio']['playback_flush_acknowledged']
        self.assertEqual(session.recovery(bad), 'INCONCLUSIVE')

    def test_intentional_failure_is_required_only_for_failure_case(self):
        case = valid_case(); case.update(name='Audio failure test', first_stop_reason=7, audio_failures=1)
        self.assertEqual(session.recovery(case), 'PASS')
        case['audio_failures'] = 0
        self.assertEqual(session.recovery(case), 'FAIL')
        case = valid_case(); case['audio']['capture_dropped_samples'] = 1
        self.assertEqual(session.recovery(case), 'FAIL')

    def test_zip_normalizes_windows_and_rejects_escapes_and_duplicates(self):
        for number, names in enumerate([['..\\escape'], ['/escape'], ['C:\\escape'], ['A', 'a']]):
            archive = self.root / f'bad{number}.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for name in names: z.writestr(name, 'test')
            with self.assertRaises(ValueError): session.extract(archive, self.root / f'out{number}')
        archive = self.root / 'good.zip'
        with zipfile.ZipFile(archive, 'w') as z: z.writestr('session\\case-001\\coordinator.json', '{}')
        out = self.root / 'good'; session.extract(archive, out)
        self.assertTrue((out / 'session/case-001/coordinator.json').is_file())

    def test_zip_and_metadata_symlinks_are_rejected(self):
        archive = self.root / 'link.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            info = zipfile.ZipInfo('link'); info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '/etc/passwd')
        with self.assertRaises(ValueError): session.extract(archive, self.root / 'out')
        (self.root / 'link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): session.inside(self.root, 'link')
        with self.assertRaises(ValueError): session.inside(self.root, '../secret')

    def test_timing_rejects_nan_negative_and_does_not_fail_on_display_delay(self):
        evidence = self.root / 'video'; evidence.write_bytes(b'synthetic video placeholder for argument checks only')
        for n, value in enumerate([float('nan'), float('inf'), -1]):
            with self.assertRaises(ValueError): session.timing(value, 10, evidence, self.root / f'bad{n}')
        session.timing(450, 60, evidence, self.root / 'uncertain.json')
        result = session.load(self.root / 'uncertain.json')
        self.assertEqual(result['result'], 'INCONCLUSIVE')
        self.assertEqual(result['conservative_upper_bound_ms'], 510)
        session.timing(400, 50, evidence, self.root / 'bounded.json')
        self.assertEqual(session.load(self.root / 'bounded.json')['result'], 'PASS')

    def test_analysis_does_not_infer_rf_or_intelligibility_from_recovery(self):
        folder = self.root / 'case-001'; folder.mkdir()
        session.write_json(folder / 'coordinator.json', valid_case())
        result = session.analyze(self.root)
        self.assertEqual(result['cases'][0]['selected_state_and_recovery'], 'PASS')
        self.assertTrue(result['rf_cutoff_500ms'].startswith('INCONCLUSIVE'))
        self.assertTrue(result['live_transmitted_speech'].startswith('INCONCLUSIVE'))

    def test_prepare_never_overwrites_and_does_not_arm(self):
        target = self.root / 'prepared'; session.prepare(target)
        self.assertEqual(len(session.load(target / 'recordings.json')['recordings']), 10)
        with self.assertRaises(FileExistsError): session.prepare(target)

    def test_decode_refuses_changed_provenance_before_running_commands(self):
        source = self.root / 'capture.wav'; source.write_bytes(b'not opened before provenance validation')
        binary = self.root / 'decoder'; binary.write_bytes(b'not executed')
        records = self.root / 'recordings.json'
        session.write_json(records, {'recordings': [{'wav': 'capture.wav'}]})
        reference = self.root / 'reference.json'
        session.write_json(reference, {'rade_source_commit': session.RADE_COMMIT,
            'model_sha256': session.MODEL_SHA, 'decoder_sha256': 'wrong', 'fixture_check_passed': True})
        with self.assertRaisesRegex(ValueError, 'provenance'):
            session.decode(records, binary, binary, self.root / 'decode', reference)
        self.assertFalse((self.root / 'decode').exists())

    def test_failed_decode_preserves_later_recording_results(self):
        binary = self.root / 'decoder'; binary.write_bytes(b'not executed')
        for name in ['one.wav', 'two.wav']: (self.root / name).write_bytes(b'synthetic input')
        records = self.root / 'recordings.json'
        session.write_json(records, {'recordings': [{'wav': 'one.wav'}, {'wav': 'two.wav'}]})
        reference = self.root / 'reference.json'
        session.write_json(reference, {'rade_source_commit': session.RADE_COMMIT,
            'model_sha256': session.MODEL_SHA, 'decoder_sha256': session.sha(binary),
            'fixture_check_passed': True, 'runtime_library': str(binary), 'runtime_library_sha256': session.sha(binary)})
        executions = [subprocess.CompletedProcess([], code, b'', b'') for code in [1, 0, 0]]
        pcm = {'frames': 16000, 'channel_statistics': [{'nonzero_samples': 15000}]}
        with patch.object(session.subprocess, 'run', side_effect=executions), patch.object(session, 'pcm_summary', return_value=pcm):
            self.assertFalse(session.decode(records, binary, binary, self.root / 'decode', reference))
        results = session.load(self.root / 'decode/decode-results.json')['recordings']
        self.assertIn('error', results[0])
        self.assertNotIn('error', results[1])
        self.assertTrue(results[1]['transmitted_speech_gate'].startswith('INCONCLUSIVE'))


if __name__ == '__main__':
    unittest.main()
