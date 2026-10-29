#!/usr/bin/env python3
"""Check long-fixture source construction and reject incomplete native evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import wave

import make_long_sync_fixture as producer


def native_result():
    return {'execution_complete': True,
            'full': {'eoo_blocks': 0, 'audio_ns': 1830_000_000_000},
            'measured': {'frames': 15000, 'audio_ns': 1800_000_000_000,
                         'sync_blocks': 15000, 'feature_blocks': 15000,
                         'nonzero_output_samples': 1}}


class NativeGateTests(unittest.TestCase):
    def test_complete_speech_does_not_qualify_radio(self):
        summary = producer.steady_summary(native_result())
        self.assertTrue(summary['passed'])
        self.assertFalse(summary['radio_timing_qualified'])
        self.assertEqual(summary['seconds'], 1800)

    def test_rejects_early_end_and_missed_speech(self):
        for field, value in [('sync_blocks', 14999), ('feature_blocks', 14999),
                             ('frames', 14999), ('audio_ns', 1799_880_000_000),
                             ('nonzero_output_samples', 0)]:
            result = native_result(); result['measured'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                producer.steady_summary(result)

    def test_rejects_eoo_in_measurement_or_missing_warmup(self):
        for field, value in [('eoo_blocks', 1), ('audio_ns', 1800_000_000_000)]:
            result = native_result(); result['full'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                producer.steady_summary(result)

    def test_rejects_incomplete_execution_even_with_good_counts(self):
        result = native_result(); result['execution_complete'] = False
        with self.assertRaises(ValueError):
            producer.steady_summary(result)

    def test_accepts_complete_block_rounding_without_shortening_measurement(self):
        result = native_result()
        result['full']['audio_ns'] += 240_000_000
        for name in ('frames', 'sync_blocks', 'feature_blocks'):
            result['measured'][name] += 1
        result['measured']['audio_ns'] += 120_000_000
        self.assertTrue(producer.steady_summary(result)['passed'])


class SpeechTests(unittest.TestCase):
    def test_repeats_pcm_exactly_and_truncates_only_final_repetition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'source.wav'; output = root/'long.wav'
            payload = b'\x01\x00\xff\xff\x02\x00'
            with wave.open(str(source), 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(payload)
            count = producer.repeat_speech(source, output, producer.sha(source), seconds=1)
            with wave.open(str(output), 'rb') as w:
                self.assertEqual(w.getnframes(), 16000)
                self.assertEqual(w.readframes(16000), (payload * 5334)[:32000])
            self.assertEqual(count, 16000)

    def test_rejects_unpinned_speech_before_writing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source=root/'source.wav'; output=root/'long.wav'
            source.write_bytes(b'unexpected speech')
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                producer.repeat_speech(source, output, '0'*64)
            self.assertFalse(output.exists())


class ModemBoundaryTests(unittest.TestCase):
    def test_rejects_missing_or_wrong_generator_dimensions_before_reading_payload(self):
        for log in ['', 'Modem frames: 15500 + EOO\n',
                    'rade_open: V1 n_features_in=432 Nmf=960 Neoo=1152 n_eoo_bits=180\nModem frames: 15499 + EOO\n']:
            with self.subTest(log=log), self.assertRaises(ValueError):
                producer.extract_modem(Path('/nonexistent.wav'), Path('/unused.raw'), log)

    def test_rejects_short_payload_despite_plausible_generator_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source=root/'modem.wav'; output=root/'fixture.raw'
            with wave.open(str(source),'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
                w.writeframes(bytes(16000))
            log='rade_open: V1 n_features_in=432 Nmf=960 Neoo=1152 n_eoo_bits=180\nModem frames: 15500 + EOO\n'
            with self.assertRaisesRegex(ValueError, 'length or EOO placement'):
                producer.extract_modem(source, output, log)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
