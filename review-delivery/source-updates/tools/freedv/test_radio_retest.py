import json
import math
from array import array
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch
from types import SimpleNamespace
from radio_retest import assess_collected, input_activity, assess_voice_case, copy_evidence, assess_control_trace


class CollectionChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.session=self.root/'radio/tmp/freedv-native-example';self.case=self.session/'case-001';self.case.mkdir(parents=True)
        (self.session/'build-manifest.json').write_text(json.dumps({'firmware_commit':'build-one','gui_sha256':'gui-one'}))
        (self.case/'coordinator.json').write_text(json.dumps({'restoration_complete':True,'audio':{'kind':6}}))
        self.trace([{'kind':'control_write','phase':'command','transport_success':True}])
        for name in ['capture.wav','output.wav']:
            with wave.open(str(self.case/name),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(b'\0\0'*100)
    def tearDown(self): self.temp.cleanup()
    def trace(self, rows, complete=True, discarded=0):
        rows=rows+[{'trace_complete':complete,'discarded':discarded,'physical_readback':False}]
        (self.case/'control-trace.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def test_individual_transport_failure_fails_even_without_bulk_attempts(self):
        self.trace([{'kind':'control_write','phase':'command','transport_success':False}])
        r=assess_collected(self.root)
        self.assertEqual(r['cases'][0]['control_trace']['status'],'FAIL')
        self.assertEqual(r['cases'][0]['voice_assessment']['status'],'FAIL')
        self.assertTrue(any('transport failed' in i for i in r['issues']))

    def test_phase_markers_null_transport_are_not_failures(self):
        self.trace([{'kind':'route_phase','phase':'record_start','transport_success':None}])
        self.assertEqual(assess_control_trace(self.case,{})['status'],'COMPLETE')

    def test_trace_loss_and_missing_footer_are_inconclusive(self):
        self.trace([],False,1)
        self.assertEqual(assess_control_trace(self.case,{})['status'],'INCONCLUSIVE')
        (self.case/'control-trace.jsonl').write_text('')
        self.assertEqual(assess_control_trace(self.case,{})['status'],'INCONCLUSIVE')

    def test_recorder_recovery_cannot_establish_exact_baseline(self):
        self.assertEqual(assess_control_trace(self.case,{'selected_case':25,'record_stop_exact':False})['status'],'INCONCLUSIVE')
        self.assertEqual(assess_control_trace(self.case,{'selected_case':25,'record_stop_exact':True})['status'],'COMPLETE')

    def test_resume_reuses_hash_match_even_with_low_free_space(self):
        source=self.root/'source';target=self.root/'target';source.write_bytes(b'evidence');target.write_bytes(b'evidence')
        with patch('radio_retest.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
            self.assertTrue(copy_evidence(source,target,self.root))
            target.write_bytes(b'old')
            self.assertFalse(copy_evidence(source,target,self.root))
        self.assertEqual(target.read_bytes(),b'old')

    def test_changing_source_never_replaces_verified_copy(self):
        source=self.root/'source';target=self.root/'target';source.write_bytes(b'new');target.write_bytes(b'verified-old')
        def changing_copy(src,dst):
            dst.write_bytes(src.read_bytes());src.write_bytes(b'changed-mid-copy')
        with patch('radio_retest.shutil.copyfile',side_effect=changing_copy),self.assertRaisesRegex(ValueError,'Source changed'):
            copy_evidence(source,target,self.root)
        self.assertEqual(target.read_bytes(),b'verified-old')

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
    def test_no_cases_is_not_completion(self):
        (self.case/'coordinator.json').unlink()
        self.assertTrue(any('No completed' in i for i in assess_collected(self.root)['issues']))

    def test_selected_candidate_must_match(self):
        r=assess_collected(self.root,{'firmware_commit':'build-two','gui_sha256':'gui-two'})
        self.assertTrue(any('differs from selected' in i for i in r['issues']))

    def test_same_source_different_binary_is_mixed(self):
        other=self.session.parent/'freedv-native-other';other.mkdir()
        (other/'build-manifest.json').write_text(json.dumps({'firmware_commit':'build-one','gui_sha256':'gui-other'}))
        self.assertTrue(any('Mixed' in i for i in assess_collected(self.root)['issues']))

    def test_mismatched_staged_case_is_rejected(self):
        stages=self.root/'radio/tmp/freedv-agent-stages';stages.mkdir()
        (stages/'123.json').write_text(json.dumps({'firmware_commit':'build-one','gui_sha256':'gui-one',
            'selected':{'case':25,'route':4,'microphone_index':0,'capture_channel':0,'mixer_choice':0}}))
        (self.case/'coordinator.json').write_text(json.dumps({'stage_request_id':123,'selected_case':23,
            'matrix_case':4,'selected_microphone_index':0,'capture_channel':0,'mixer_choice':0,
            'restoration_complete':True,'audio':{'kind':6}}))
        self.assertTrue(any('differ from acknowledged' in i for i in assess_collected(self.root)['issues']))

    def test_unfinished_source_recording_is_inconclusive(self):
        watch=self.root/'radio/tmp/freedv-capture-source-watch-123';watch.mkdir()
        self.assertTrue(any('source recording incomplete' in i for i in assess_collected(self.root)['issues']))

    def test_truncated_recording_is_inconclusive(self):
        p=self.case/'output.wav';p.write_bytes(p.read_bytes()[:-2])
        self.assertTrue(any('truncated PCM' in i for i in assess_collected(self.root)['issues']))

    def test_modem_case_requires_complete_source_coverage(self):
        case={'case_number':1,'selected_case':28,'restoration_complete':True,'audio':{'kind':4},
              'record_baseline_started_monotonic_ms':1000,'modem_started_monotonic_ms':3000,
              'restoration_completed_monotonic_ms':12000}
        (self.case/'coordinator.json').write_text(json.dumps(case))
        (self.session/'events.jsonl').write_text(json.dumps({'event':'record_modem_unkeyed_baseline_start','case':1,'monotonic_ms':1000})+'\n')
        self.assertTrue(any('lacks a completed matching' in i for i in assess_collected(self.root)['issues']))
        watch=self.root/'radio/tmp/freedv-capture-source-watch-123';watch.mkdir()
        (watch/'capture-0001.wav').write_bytes((self.case/'capture.wav').read_bytes())
        (watch/'identity.json').write_text(json.dumps({'gui_sha256':'gui-one'}))
        complete={'status':'COLLECTED','first_read_monotonic_ns':500000000,'last_read_monotonic_ns':13000000000,'chunks':1,'samples':100,'channels':1}
        (watch/'complete.json').write_text(json.dumps(complete))
        self.assertEqual(assess_collected(self.root)['issues'],[])
        complete['chunks']=2
        (watch/'complete.json').write_text(json.dumps(complete))
        self.assertTrue(any('missing chunks' in i for i in assess_collected(self.root)['issues']))
        complete['chunks']=1
        complete['first_read_monotonic_ns']=2000000000
        (watch/'complete.json').write_text(json.dumps(complete))
        self.assertTrue(any('lacks a completed matching' in i for i in assess_collected(self.root)['issues']))

    def test_ordinary_recorder_name_receives_activity_assessment(self):
        self.capture([149]*44100)
        r=assess_voice_case(self.case,{'selected_case':25,'name':'Ordinary recorder sequence / internal zero','audio':{'kind':0}})
        self.assertEqual(r['input_activity']['status'],'NEAR_CONSTANT')

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

    def test_late_full_scale_spike_is_not_sustained_mic_input(self):
        pcm = [149+(i%3)-1 for i in range(8*44100)]
        pcm[3*44100:3*44100+441] = [25000,-25000]*220+[25000]
        self.capture(pcm)
        r=assess_voice_case(self.case,{'audio':{'kind':6}})
        self.assertEqual(r['input_activity']['status'],'BRIEF_TRANSIENTS_ONLY')
        self.assertGreater(r['input_activity']['steady_ac_rms'],100)
        self.assertEqual(r['status'],'FAIL')
        self.assertEqual(r['listening'],'UNASSESSED')

    def test_small_late_glitch_is_not_sustained_mic_input(self):
        pcm = [149+(i%3)-1 for i in range(8*44100)]
        pcm[6*44100]=306
        self.capture(pcm)
        r=input_activity(self.case/'capture.wav')
        self.assertEqual(r['status'],'BRIEF_TRANSIENTS_ONLY')

    def test_quiet_continuous_signal_remains_activity_unassessed(self):
        self.capture([149+int(10*math.sin(i*.1)) for i in range(44100)])
        r=input_activity(self.case/'capture.wav')
        self.assertEqual(r['status'],'ACTIVITY_PRESENT')
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
