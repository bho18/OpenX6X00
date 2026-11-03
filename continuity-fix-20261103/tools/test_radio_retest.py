import json
import math
from array import array
from pathlib import Path
import tempfile
import unittest
import wave
from radio_retest import assess_collected, input_activity, assess_voice_case


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

    def capture(self, values):
        with wave.open(str(self.case/'capture.wav'),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100)
            w.writeframes(array('h',values).tobytes())

    def test_dc_and_startup_impulse_cannot_pass_voice(self):
        self.capture([4000]*8820+[149+(i%3)-1 for i in range(44100)])
        r=assess_voice_case(self.case,{'audio':{'kind':6}})
        self.assertEqual(r['input_activity']['status'],'NEAR_CONSTANT')
        self.assertEqual(r['status'],'FAIL')

    def test_tone_is_activity_but_not_intelligibility(self):
        self.capture([int(2000*math.sin(i*.1)) for i in range(44100)])
        r=assess_voice_case(self.case,{'audio':{'kind':6}})
        self.assertEqual(r['input_activity']['status'],'ACTIVITY_PRESENT')
        self.assertEqual(r['status'],'INCONCLUSIVE')
        self.assertEqual(r['listening'],'UNASSESSED')

    def test_short_capture_remains_unassessed(self):
        self.assertEqual(input_activity(self.case/'capture.wav')['status'],'UNASSESSED')

    def test_truncated_pcm_rejected(self):
        self.capture([149]*44100)
        path=self.case/'capture.wav';path.write_bytes(path.read_bytes()[:-30])
        with self.assertRaises(ValueError):input_activity(path)

    def test_only_current_generation_before_drain_counts(self):
        old={'generation':1,'drain_requested':False,'cancellation_requested':False}
        end={'generation':2,'drain_requested':True,'cancellation_requested':False}
        a={'kind':6,'generation':2,'playback_underflow_events':[old,end]}
        self.assertEqual(assess_voice_case(self.case,{'audio':a})['pre_drain_underflows'],[])
        a['playback_underflow_events'].append(dict(old,generation=2))
        self.assertEqual(assess_voice_case(self.case,{'audio':a})['status'],'FAIL')


if __name__=='__main__':unittest.main()
