#!/usr/bin/env python3
"""Off-air WAV framing/rate/size gates; no radio access."""
from pathlib import Path
import tempfile
import unittest
import wave
import json
import hashlib
import subprocess
from unittest.mock import patch
from array import array
import usb_rf_evidence as subject


class WavTests(unittest.TestCase):
    def test_stereo_downmix_preserves_level_and_frame_count(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sdr.wav'
            with wave.open(str(path),'wb') as w:
                w.setparams((2,2,48000,0,'NONE','not compressed'))
                w.writeframes(array('h',[1000,3000,-1000,-3000]).tobytes())
            pcm,info=subject.read_sdr(path)
            self.assertEqual(list(array('h',pcm)),[2000,-2000])
            self.assertEqual(info['frames'],2)
            self.assertEqual(info['rms'],2000)

    def test_unsupported_recordings_rejected_before_decode(self):
        for channels,width,rate,seconds in [(2,4,48000,1),(1,2,22050,1),(1,2,48000,31),(4,2,48000,1)]:
            with self.subTest(channels=channels,width=width,rate=rate,seconds=seconds),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'bad.wav'
                with wave.open(str(path),'wb') as w:
                    w.setparams((channels,width,rate,0,'NONE','not compressed'))
                    w.writeframes(b'\0'*(channels*width*rate*seconds))
                with self.assertRaises(ValueError):subject.read_sdr(path)


class CollectionTests(unittest.TestCase):
    def collect_fixture(self, root, fault=1, rate=None, decoder_failure=False):
        traces=root/'traces';traces.mkdir()
        (root/'proc').mkdir()
        (traces/'freedv-usb-fixture.jsonl').write_text(json.dumps(dict(state=5 if fault>1 else 6,fault=fault,rf_tx=False))+'\n')
        (traces/'freedv-usb-fixture-mic.raw').write_bytes(b'\0\0'*480)
        runner=root/'runner';runner.write_bytes(b'fixture runner, never executed')
        library=root/'library';library.write_bytes(b'fixture codec, never loaded')
        manifest=dict(gui_sha256='fixture',runner_sha256=subject.sha256(runner),rade_sha256=subject.sha256(library))
        mapped={'/tmp':traces,'/proc':root/'proc','/opt/freedv-benchmark/bin/freedv_voice_runner':runner,
                '/opt/freedv-benchmark/lib/librade.so.0.1':library}
        sdr=None
        if rate:
            sdr=root/'input.wav'
            with wave.open(str(sdr),'wb') as w:
                w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(b'\0\0'*rate)
        output=root/'results';output.mkdir()
        with patch.object(subject,'Path',side_effect=lambda p:mapped.get(str(p),Path(p))), \
             patch.object(subject,'collect',return_value={'status':'USB_DUPLEX_CANDIDATE'}), \
             patch('usb_half_duplex.stream_process',side_effect=subprocess.CalledProcessError(1,['fixture decoder'])) as decoder:
            result=subject.collect_evidence(output,manifest,sdr)
            self.assertEqual(decoder.call_count,1 if decoder_failure else 0)
        for line in (output/'SHA256SUMS').read_text().splitlines():
            expected,name=line.split('  ',1)
            self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(),expected)
        return result,output,sdr

    def test_invalid_wav_preserved_and_blocked_after_trace_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,sdr=self.collect_fixture(Path(temp),rate=22050)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertIn('22050',result['reason'])
            self.assertEqual((output/'sdr-usb-audio.wav').read_bytes(),sdr.read_bytes())
            self.assertTrue((output/'mic.wav').exists())

    def test_decoder_failure_cannot_retain_success_status(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.collect_fixture(Path(temp),rate=48000,decoder_failure=True)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertFalse(result['rf_recording_decoded'])
            self.assertTrue((output/'usb-session.jsonl').exists())

    def test_gui_backlog_is_reported_even_without_sdr(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.collect_fixture(Path(temp),fault=6)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['gui_session_fault'],6)
            self.assertTrue((output/'usb-session.jsonl').exists())

    def test_clean_collection_without_sdr_remains_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as temp:
            result,_,_=self.collect_fixture(Path(temp))
            self.assertEqual(result['status'],'COLLECTED_NO_SDR_RECORDING')
            self.assertFalse(result['rf_recording_decoded'])


if __name__=='__main__':unittest.main()
