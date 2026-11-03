import json
from pathlib import Path
import tempfile
import unittest
import wave
from radio_retest import assess_collected


class CollectionChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.session=self.root/'radio/tmp/freedv-native-example';self.case=self.session/'case-001';self.case.mkdir(parents=True)
        (self.session/'build-manifest.json').write_text(json.dumps({'firmware_commit':'build-one'}))
        (self.case/'coordinator.json').write_text(json.dumps({'restoration_complete':True,'audio':{'kind':6}}))
        for name in ['capture.wav','output.wav']:
            with wave.open(str(self.case/name),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(b'\0\0'*100)
    def tearDown(self): self.temp.cleanup()
    def test_complete_is_still_not_listening_acceptance(self):
        r=assess_collected(self.root);self.assertEqual(r['issues'],[]);self.assertEqual(r['cases'][0]['listening'],'UNASSESSED')
    def test_missing_recording(self):
        (self.case/'output.wav').unlink();self.assertTrue(assess_collected(self.root)['issues'])
    def test_incomplete_run(self):
        (self.case/'coordinator.json').unlink();self.assertTrue(assess_collected(self.root)['issues'])
    def test_mixed_build(self):
        other=self.session.parent/'freedv-native-other';other.mkdir()
        (other/'build-manifest.json').write_text(json.dumps({'firmware_commit':'build-two'}))
        self.assertTrue(assess_collected(self.root)['issues'])
    def test_wrong_format(self):
        with wave.open(str(self.case/'output.wav'),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);w.writeframes(b'\0\0'*100)
        self.assertTrue(assess_collected(self.root)['issues'])


if __name__=='__main__':unittest.main()
