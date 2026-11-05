#!/usr/bin/env python3
"""Half-duplex pacing/resource gates and evidence preservation; no hardware."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import usb_half_duplex as subject
from test_usb_headset_validate import inventory


def report():
    return dict(schema='x6200.usb-half-duplex.v1', ok=True, client_rate=48000,
                turns_completed=3, eoo_count=3, stream_creations=2, rade_creations=1,
                encoder_creations=1, decoder_creations=1, worker_creations=2,
                failures=0, synthesis_blocks=20, rf_tested=False, ptt_tested=False,
                holes=0, capture_overflows=0, timing_samples=10, read_rate_hz=48000,
                render_gap_callbacks=0, pa_underflows=0)


class HalfDuplexTests(unittest.TestCase):
    def test_retained_resources_and_pacing_required(self):
        subject.check(report())
        for field, value in [('rade_creations', 3), ('stream_creations', 6), ('turns_completed', 2),
                             ('eoo_count', 2), ('failures', 1), ('holes', 1), ('synthesis_blocks', 0),
                             ('rf_tested', True), ('read_rate_hz', 44100), ('read_rate_hz', 52244),
                             ('timing_samples', 0)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                subject.check(dict(report(), **{field: value}))

    def test_blocked_preflight_does_not_run_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'half-duplex-manifest.json').write_text('{"helper_sha256":"pinned"}')
            with patch.object(subject, 'sha256', return_value='wrong'):
                result = subject.validate(out, out / 'usb_half_duplex', run=lambda *args: self.fail('audio opened'))
            self.assertFalse(result['live_codec_tested'])
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertTrue((out / 'validation.json').exists())
            self.assertTrue((out / 'SHA256SUMS').exists())

    def test_completion_gaps_and_partial_session_are_distinct(self):
        for mode in ('success', 'gaps', 'lost_device'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)
                (out / 'half-duplex-manifest.json').write_text('{"helper_sha256":"pinned"}')
                def run(args, output):
                    session = report()
                    if mode == 'gaps': session['render_gap_callbacks'] = 2
                    if mode == 'lost_device': session.update(ok=False, turns_completed=1, fault='USB stream failed')
                    subject.save(out / 'session.json', session)
                    (out / 'turn-1-microphone.raw').write_bytes(b'\x00\x01' * 48000)
                    if mode == 'lost_device': raise ValueError('Synthetic stream failure')
                with patch.object(subject, 'sha256', return_value='pinned'), \
                     patch.object(subject, 'RADE_SHA', 'pinned'), \
                     patch.object(subject, 'collect', return_value=copy.deepcopy(inventory())):
                    result = subject.validate(out, out / 'usb_half_duplex', run=run)
                self.assertEqual(result['status'], {'success':'COMPLETED_LISTENING_REVIEW_REQUIRED',
                    'gaps':'COMPLETED_WITH_PLAYBACK_GAPS', 'lost_device':'BLOCKED'}[mode])
                self.assertTrue(result['live_codec_tested'])
                self.assertFalse(result['rf_tested'])
                self.assertEqual(result['listening'], 'UNASSESSED')
                self.assertTrue((out / 'turn-1-microphone.wav').exists())

    def test_live_log_is_preserved_when_helper_exits(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with self.assertRaises(ValueError):
                subject.stream_process([sys.executable, '-u', '-c', "print('GO - synthetic prompt');raise SystemExit(1)"], out, timeout=2)
            self.assertIn('GO - synthetic prompt', (out / 'session.log').read_text())


if __name__ == '__main__': unittest.main()
