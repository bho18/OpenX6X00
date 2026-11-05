#!/usr/bin/env python3
"""Guards and partial-evidence regression; synthetic inventory, no radio."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import usb_headset_validate as subject


def inventory():
    def endpoint(name):
        return dict(name=name, physical_alsa=True, monitor=False, alsa_card=1, mute='no')
    return {'status': 'USB_DUPLEX_CANDIDATE', 'usb_audio_cards': [
        dict(status='USB_DUPLEX_CANDIDATE', number=1,
             usb=dict(id_vendor='3511', id_product='2b1e'),
             pulse_capture_sources=[endpoint('alsa_input.usb-test')],
             pulse_playback_sinks=[endpoint('alsa_output.usb-test')]) ]}


class ValidationTest(unittest.TestCase):
    def test_selection_is_physical_unique_and_unmuted(self):
        self.assertEqual(subject.endpoints(inventory()), ('alsa_input.usb-test', 'alsa_output.usb-test'))
        for change in ('monitor', 'virtual', 'radio', 'mute', 'different_card', 'wrong_usb', 'multiple'):
            value = copy.deepcopy(inventory())
            card = value['usb_audio_cards'][0]
            mic = card['pulse_capture_sources'][0]
            if change == 'monitor': mic['monitor'] = True
            if change == 'virtual': mic['physical_alsa'] = False
            if change == 'radio': mic['name'] = 'alsa_input.platform-sound.stereo-fallback'
            if change == 'mute': mic['mute'] = 'yes'
            if change == 'different_card': mic['alsa_card'] = 2
            if change == 'wrong_usb': card['usb']['id_product'] = '0000'
            if change == 'multiple': value['usb_audio_cards'].append(copy.deepcopy(card))
            with self.subTest(change=change), self.assertRaises(ValueError): subject.endpoints(value)

    def test_codec_requires_real_work(self):
        good = dict(failures=0, eoo_count=1, speech_frames=3, modem_blocks=3,
                    synchronized_blocks=3, synthesis_blocks=3)
        subject.check_engine(good)
        for field in ('speech_frames', 'modem_blocks', 'synchronized_blocks', 'synthesis_blocks'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                subject.check_engine(dict(good, **{field: 0}))

    def test_rebound_or_incomplete_pcm_is_rejected(self):
        good = dict(schema='x6200.usb-pcm.v1', ok=True, dont_move=True, source='mic', sink='ear',
                    captured_bytes=88200, drained=True, holes=0, underflows=0, overflows=0)
        subject.check_pcm(good, 'mic', 'ear', 88200)
        for field, value in [('sink', 'radio'), ('source', 'monitor'), ('dont_move', False),
                             ('holes', 1), ('drained', False), ('captured_bytes', 0)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                subject.check_pcm(dict(good, **{field: value}), 'mic', 'ear', 88200)

    def test_failed_preflight_retains_report_without_opening_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'validation-manifest.json').write_text('{"pcm_helper_sha256":"expected"}')
            with patch.object(subject, 'sha256', return_value='wrong'), patch.object(subject, 'run_logged') as run:
                report = subject.validate(out, out / 'usb_headset_pcm')
            run.assert_not_called()
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertEqual(report['stages_completed'], [])
            self.assertFalse(report['rf_tested'])
            self.assertEqual(json.loads((out / 'validation.json').read_text()), report)
            self.assertTrue((out / 'SHA256SUMS').exists())

    def test_campaign_completion_and_midstage_failure(self):
        # Exercise campaign orchestration and evidence, not a substitute for PCM/codec tests.
        for fail_stage in (None, 'decoded-playback'):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)
                helper = out / 'usb_headset_pcm'
                (out / 'validation-manifest.json').write_text('{"pcm_helper_sha256":"pinned"}')
                called = []
                def run(args, directory, stage, timeout):
                    called.append(stage)
                    if stage == fail_stage:
                        raise ValueError('Synthetic midstage device loss')
                    if stage == 'codec':
                        codec = out / 'codec'
                        subject.save(codec / 'engine-stats.json', dict(failures=0, eoo_count=1,
                            speech_frames=1, modem_blocks=1, synchronized_blocks=1, synthesis_blocks=1))
                        (codec / 'turn-000-decoded-s16le.raw').write_bytes(b'\x00\x10' * subject.RATE)
                    else:
                        count = subject.SECONDS * subject.RATE * 2 if stage == 'duplex' else 0
                        if count: Path(args[3]).write_bytes(b'\x00\x10' * (count // 2))
                        subject.save(Path(args[5]), dict(schema='x6200.usb-pcm.v1', ok=True,
                            dont_move=True, source=args[1], sink=args[2], captured_bytes=count,
                            drained=True, holes=0, overflows=0, underflows=0))
                with patch.object(subject, 'sha256', return_value='pinned'), \
                     patch.object(subject, 'RUNNER_SHA', 'pinned'), patch.object(subject, 'RADE_SHA', 'pinned'), \
                     patch.object(subject, 'collect', return_value=inventory()), \
                     patch.object(subject.time, 'sleep'), patch.object(subject, 'run_logged', side_effect=run):
                    report = subject.validate(out, helper)
                self.assertEqual(called, ['duplex', 'original-playback', 'codec', 'decoded-playback'])
                self.assertEqual(report['status'], 'BLOCKED' if fail_stage else 'COMPLETED_LISTENING_REVIEW_REQUIRED')
                self.assertFalse(report['live_codec_tested'])
                self.assertEqual(report['intelligibility'], 'UNASSESSED')
                self.assertTrue((out / 'microphone-s16le.wav').exists())
                self.assertTrue((out / 'codec/turn-000-decoded-s16le.wav').exists())
                self.assertTrue((out / 'validation.json').exists())


if __name__ == '__main__': unittest.main()
