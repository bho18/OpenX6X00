#!/usr/bin/env python3
"""Pure refinement evidence gates; no audio or radio."""
import copy
import unittest
from usb_refinement_contract import (validate_refinement_contract,validate_volume_readbacks,
    cue_reference_pcm,local_cue_waveform,BUDGETS,COUNTERS,TIMESTAMPS)


class RefinementContractTests(unittest.TestCase):
    def fixture(self):
        identity=dict(session_id='freedv-usb-unit',build_id='unit',source_commit='a'*40)
        marker=dict(usb_refinement_contract_version=1,**identity)
        sample=dict(kind='sample',state=1,ms=1000,sampled_ms=1000,rx_sync_changed_ms=900,rx_audio_last_ms=950,
            mic_health=0,mic_health_changed_ms=900,mic_recovery_epoch=1,mic_recovery_started_ms=800,
            mic_recovery_attempts=1,mic_retry_available=False,mic_health_reason='fresh-capture',armed=True,
            headset_volume_initialized=True,headset_volume_pending=False,headset_volume_ready=True,
            headset_volume_percent=37,headset_muted=False,headset_volume_requested_percent=37,
            headset_volume_requested_muted=False,headset_volume_requested_revision=1,
            headset_volume_confirmed_revision=1,headset_volume_failed_revision=0,
            cue_available=True,cue_active=False,cue_requests=0,cue_completed=0,cue_canceled=0,cue_failures=0,
            **marker)
        completion=dict(sample,kind='completion',state=6,armed=False,sampled_ms=1100)
        return [dict(kind='session',operational=True,usb_startup_contract_version=4,**BUDGETS,**marker),sample],completion,identity

    def test_bound_version_and_identity(self):
        rows,completion,identity=self.fixture()
        result=validate_refinement_contract(rows,completion,expected_identity=identity)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['source_commit'],identity['source_commit'])

    def test_historical_sessions_do_not_qualify_refinement(self):
        self.assertEqual(validate_refinement_contract([],None,0)['status'],'DIAGNOSTIC_ONLY')

    def test_missing_or_wrong_marker_or_identity_cannot_be_accepted(self):
        for destination in ('header','completion'):
            for field,value in (('usb_refinement_contract_version',None),('usb_refinement_contract_version',True),
                                ('usb_refinement_contract_version',2),('session_id','other'),('build_id','other'),('source_commit','other')):
                rows,completion,identity=self.fixture()
                record=rows[0] if destination=='header' else completion
                if value is None:record.pop(field)
                else:record[field]=value
                with self.subTest(destination=destination,field=field,value=value),self.assertRaises(ValueError):
                    validate_refinement_contract(rows,completion,expected_identity=identity)

    def test_budgets_and_typed_observations_cannot_be_omitted_or_relaxed(self):
        for key in BUDGETS:
            rows,completion,_=self.fixture();rows[0][key]+=1
            with self.subTest(field=key),self.assertRaises(ValueError):validate_refinement_contract(rows,completion)
        for key in (*COUNTERS,*TIMESTAMPS):
            rows,completion,_=self.fixture();rows[1][key]=True
            with self.subTest(field=key),self.assertRaises(ValueError):validate_refinement_contract(rows,completion)

    def test_nonmonotonic_counters_and_future_transitions_are_rejected(self):
        for field in ('mic_recovery_attempts','headset_volume_confirmed_revision','sampled_ms'):
            rows,completion,_=self.fixture();completion[field]=0
            with self.subTest(field=field),self.assertRaises(ValueError):validate_refinement_contract(rows,completion)
        rows,completion,_=self.fixture();rows[1]['mic_health_changed_ms']=1001
        with self.assertRaises(ValueError):validate_refinement_contract(rows,completion)

    def test_pending_volume_retains_the_last_confirmed_value(self):
        rows,completion,_=self.fixture()
        rows[1].update(headset_volume_pending=True,headset_volume_requested_revision=2,headset_volume_requested_percent=48)
        completion.update(headset_volume_requested_revision=2,headset_volume_confirmed_revision=2,
                          headset_volume_percent=48,headset_volume_requested_percent=48)
        self.assertEqual(validate_refinement_contract(rows,completion)['status'],'PASS')

    def test_microphone_unavailable_and_tx_cue_cannot_claim_admission(self):
        for change in (dict(mic_health=1),dict(mic_health=2),dict(mic_retry_available=True),dict(cue_active=True,state=3)):
            rows,completion,_=self.fixture();rows[1].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_refinement_contract(rows,completion)


    def volume_fixture(self):
        initial={'alsa_output.usb-fixture':dict(mute=True,volume=[26214,19660]),
                 'radio_sink_fixture':dict(mute=False,volume=[65536,65536]),
                 'unrelated_sink':dict(mute=False,volume=[15073,12452])}
        phases=[]
        for name,level,actual in (('volume-initial',37,[24248,18185]),('volume-pending',37,[31457,23591]),
                ('volume-confirmed',48,[31457,23591]),('volume-failed',48,[31457,23591]),('volume-recovered',55,[36044,27030])):
            pulse=copy.deepcopy(initial);pulse['alsa_output.usb-fixture']=dict(mute=False,volume=actual)
            phases.append(dict(snapshot=dict(phase=name,headset_volume_percent=level),pulse=pulse))
        return dict(schema='x6200.usb.refinement-volume-readbacks.v1',status='PASS',initial_sinks=initial,phases=phases)

    def test_asymmetric_confirmed_master_preserves_balance_and_rounding(self):
        self.assertEqual(validate_volume_readbacks(self.volume_fixture())['status'],'PASS')

    def test_changed_balance_or_another_master_cannot_pass_readback(self):
        for change in ('balance','other-master','optimistic-confirmed'):
            proof=self.volume_fixture()
            if change=='balance':proof['phases'][2]['pulse']['alsa_output.usb-fixture']['volume'][1]+=200
            elif change=='other-master':proof['phases'][2]['pulse']['radio_sink_fixture']['volume'][0]-=1
            else:proof['phases'][2]['snapshot']['headset_volume_percent']=52
            with self.subTest(change=change),self.assertRaises(ValueError):validate_volume_readbacks(proof)

    def test_complete_quiet_cue_is_detected_in_the_independent_monitor(self):
        from array import array
        reference=array('h');reference.frombytes(cue_reference_pcm())
        quiet=array('h',(round(sample*.07) for sample in reference)).tobytes()
        proof=local_cue_waveform(b'\0'*960+quiet+b'\0'*960)
        self.assertEqual(proof['status'],'PASS')
        self.assertEqual(proof['frames'],2880)
        self.assertGreater(proof['correlation'],.999)

    def test_missing_or_incomplete_local_cue_does_not_pass(self):
        reference=cue_reference_pcm()
        for pcm in (b'\0'*9600,b'\0'*960+reference[:2000]+b'\0'*6000):
            with self.assertRaises(ValueError):local_cue_waveform(pcm)


if __name__=='__main__':unittest.main()
