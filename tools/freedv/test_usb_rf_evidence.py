#!/usr/bin/env python3
"""Off-air WAV framing/rate/size gates; no radio access."""
from pathlib import Path
import tempfile
import unittest
import wave
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


if __name__=='__main__':unittest.main()
