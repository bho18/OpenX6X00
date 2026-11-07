#!/usr/bin/env python3
"""Pure refinement evidence gates; no audio or radio."""
import copy
import unittest
from usb_refinement_contract import (validate_refinement_contract,validate_volume_readbacks,
    cue_reference_pcm,local_cue_waveform,BUDGETS,BUDGETS_V2,COUNTERS,TIMESTAMPS,
    HARDENING_BOOLEAN_FIELDS,HARDENING_UNSIGNED_FIELDS,hardening_exercise)


class RefinementContractTests(unittest.TestCase):
    def fixture(self,version=1):
        identity=dict(session_id='freedv-usb-unit',build_id='unit',source_commit='a'*40)
        marker=dict(usb_refinement_contract_version=version,**identity)
        sample=dict(kind='sample',state=1,ms=1000,sampled_ms=1000,rx_sync_changed_ms=900,rx_audio_last_ms=950,
            mic_health=0,mic_health_changed_ms=900,mic_recovery_epoch=1,mic_recovery_started_ms=800,
            mic_recovery_attempts=1,mic_retry_available=False,mic_health_reason='fresh-capture',armed=True,
            headset_volume_initialized=True,headset_volume_pending=False,headset_volume_ready=True,
            headset_volume_percent=37,headset_muted=False,headset_volume_requested_percent=37,
            headset_volume_requested_muted=False,headset_volume_requested_revision=1,
            headset_volume_confirmed_revision=1,headset_volume_failed_revision=0,
            cue_available=True,cue_active=False,cue_requests=0,cue_completed=0,cue_canceled=0,cue_failures=0,
            **marker)
        if version==2:
            sample.update({name:False for name in HARDENING_BOOLEAN_FIELDS})
            sample['armed']=True
            sample.update({name:0 for name in HARDENING_UNSIGNED_FIELDS})
            sample['headset_volume_channel_values']=[24248,18186]
        completion=dict(sample,kind='completion',state=6,armed=False,sampled_ms=1100)
        return [dict(kind='session',operational=True,usb_startup_contract_version=4,
                     **(BUDGETS_V2 if version==2 else BUDGETS),**marker),sample],completion,identity

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

    def test_hardening_version_binds_exact_budgets_and_health(self):
        rows,completion,identity=self.fixture(2)
        result=validate_refinement_contract(rows,completion,2,identity)
        self.assertEqual(result['schema'],'x6200.usb.refinement-contract.v2')
        self.assertEqual(result['status'],'PASS')
        for field in BUDGETS_V2:
            altered=copy.deepcopy(rows);altered[0][field]+=1
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate_refinement_contract(altered,completion,2,identity)
        for field in (*HARDENING_BOOLEAN_FIELDS,*HARDENING_UNSIGNED_FIELDS):
            altered=copy.deepcopy(rows);del altered[1][field]
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate_refinement_contract(altered,completion,2,identity)

    def test_hardening_recovery_cannot_hide_admission_or_exceed_retry_limits(self):
        for change in (dict(radio_recovering=True),dict(cue_unavailable=True,cue_available=True),
                       dict(headset_volume_unavailable=True),dict(headset_volume_retries=3),
                       dict(cue_retries=3),dict(radio_recovery_started_ms=1001),
                       dict(headset_volume_operation=4),dict(mic_overload=1),
                       dict(headset_volume_channel_values=[]),dict(headset_volume_channel_values=[True]),
                       dict(headset_volume_channel_values=[0]*(33))):
            rows,completion,_=self.fixture(2);rows[1].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_refinement_contract(rows,completion,2)
        rows,completion,_=self.fixture(2)
        rows[1].update(radio_recovering=True,armed=False,radio_recovery_attempts=1,radio_recovery_started_ms=950)
        completion.update(radio_recovery_attempts=1,radio_recovery_started_ms=950)
        self.assertEqual(validate_refinement_contract(rows,completion,2)['status'],'PASS')

    def test_hardening_does_not_accept_legacy_or_boolean_version(self):
        for version in (0,1,True,3):
            rows,completion,_=self.fixture(2);rows[0]['usb_refinement_contract_version']=version
            with self.subTest(version=version),self.assertRaises(ValueError):
                validate_refinement_contract(rows,completion,2)

    def test_hardening_armed_admission_must_be_present_and_typed_in_every_observation(self):
        for location in ('sample','completion'):
            for invalid in (None,0,1,'false'):
                rows,completion,_=self.fixture(2)
                record=rows[1] if location=='sample' else completion
                if invalid is None:record.pop('armed')
                else:record['armed']=invalid
                with self.subTest(location=location,invalid=invalid),self.assertRaisesRegex(ValueError,'missing or untyped'):
                    validate_refinement_contract(rows,completion,2)

    def exercise_fixture(self,suffix):
        rows,completion,_=self.fixture(2)
        initial=dict(rows[1],phase='initial',keys=0,submitted_mic=0,turns=0,
                     rade_creations=1,encoder_creations=1,decoder_creations=1)
        harness=dict(fault=0,turns=1,eoo=1,keys=1,physical_release_sequence=1,
                     rade_creations=1,encoder_creations=1,decoder_creations=1)
        phases=[initial]
        if suffix=='volume-paced-rounded':
            for index,(name,target) in enumerate((('low0',0),('up1',1),('up5',5),('up9',9),
                    ('up10',10),('up15',15),('up100',100),('down9',9),('final15',15)),1):
                phases.append(dict(initial,phase=name,ms=1000+index*100,
                    headset_volume_requested_percent=target,headset_volume_percent=((target+2)//5)*5))
        elif suffix=='cue-final-gesture':
            phases.extend((dict(initial,phase='earlier-gesture-start',ms=1050),
                dict(initial,phase='earlier-preview',ms=1700,cue_completed=1,
                     headset_volume_percent=50,headset_volume_requested_percent=50),
                dict(initial,phase='gesture-start',ms=2100,cue_completed=1),
                dict(initial,phase='active-preview-edit',ms=2250,cue_completed=1,cue_active=True),
                dict(initial,phase='final-gesture-start',ms=3800,cue_completed=2),
                dict(initial,phase='final-preview',ms=4650,cue_completed=3,headset_volume_percent=100,
                     cue_io_pauses=1,cue_io_pause_ms=50)))
            phases.extend(dict(initial,phase=name,ms=at,cue_completed=3) for name,at in (
                ('decoded-before-edit',4800),('decoded-after-edit',5000),('decoded-settled',7600)))
            harness['rx_synthesis_blocks']=4
        else:raise AssertionError('Unimplemented pure exercise fixture')
        return rows,completion,harness,phases,'operational-hardening-'+suffix

    def test_paced_rounded_exercise_rejects_lost_intent_and_cold_codec(self):
        fixture=self.exercise_fixture('volume-paced-rounded')
        self.assertEqual(hardening_exercise(*fixture)['status'],'PASS')
        for field,value in (('headset_volume_requested_percent',0),('decoder_creations',2),
                            ('headset_volume_pending',True),('headset_volume_percent',25)):
            altered=copy.deepcopy(fixture);altered[3][2][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):hardening_exercise(*altered)

    def test_preview_exercise_rejects_earlier_completion_as_final_gesture(self):
        fixture=self.exercise_fixture('cue-final-gesture')
        self.assertEqual(hardening_exercise(*fixture)['status'],'PASS')
        for field,value in (('cue_completed',2),('ms',3600),('headset_volume_percent',70),
                            ('headset_volume_confirmed_revision',0),('submitted_mic',1)):
            altered=copy.deepcopy(fixture)
            next(row for row in altered[3] if row['phase']=='final-preview')[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):hardening_exercise(*altered)


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

    def test_final_hardening_gesture_requires_its_own_complete_half_second(self):
        from array import array
        reference=array('h');reference.frombytes(cue_reference_pcm(2))
        earlier=array('h',(round(sample*.2) for sample in reference)).tobytes()
        final=reference.tobytes()
        silence=b'\0'*48000
        observed=earlier+silence+final+b'\0'*960
        start=(len(earlier)+len(silence))//2
        proof=local_cue_waveform(observed,2,frame_start=start,frame_end=len(observed)//2)
        self.assertEqual(proof['duration_ms'],500)
        self.assertEqual(proof['frames'],24000)
        self.assertGreaterEqual(proof['monitor_frame_start'],start)
        self.assertAlmostEqual(proof['measured_gain'],1.,places=3)
        for truncated in (silence,b'\0'*960+final[:24000]+b'\0'*48000):
            pcm=earlier+silence+truncated
            with self.subTest(bytes=len(truncated)),self.assertRaises(ValueError):
                local_cue_waveform(pcm,2,frame_start=start,frame_end=len(pcm)//2)
        with self.assertRaises(ValueError):
            local_cue_waveform(earlier,2,frame_start=0,frame_end=len(earlier)//2)

    def test_hardening_preview_requires_explicit_valid_gesture_bounds(self):
        pcm=cue_reference_pcm(2)
        for bounds in ({},{'frame_start':True,'frame_end':24000},
                       {'frame_start':0,'frame_end':24001},{'frame_start':24000,'frame_end':0}):
            with self.subTest(bounds=bounds),self.assertRaises(ValueError):
                local_cue_waveform(pcm,2,**bounds)

    def test_hardening_preview_level_matches_its_volume_and_rejects_constant_loudness(self):
        from array import array
        high=cue_reference_pcm(2)
        reference=array('h');reference.frombytes(high)
        low=array('h',(round(sample*.125) for sample in reference)).tobytes()
        low_proof=local_cue_waveform(low,2,frame_start=0,frame_end=24000,expected_gain=.125)
        high_proof=local_cue_waveform(high,2,frame_start=0,frame_end=24000,expected_gain=1.)
        self.assertAlmostEqual(low_proof['expected_gain'],.125)
        self.assertEqual(low_proof['allowed_relative_error'],.03)
        self.assertAlmostEqual(high_proof['measured_gain']/low_proof['measured_gain'],8.,places=2)
        for pcm,expected in ((high,.125),(low,1.)):
            with self.subTest(expected_gain=expected),self.assertRaises(ValueError):
                local_cue_waveform(pcm,2,frame_start=0,frame_end=24000,expected_gain=expected)
        for expected in (0,True,float('nan'),float('inf'),1.1,-.125):
            with self.subTest(expected_gain=expected),self.assertRaises(ValueError):
                local_cue_waveform(high,2,frame_start=0,frame_end=24000,expected_gain=expected)

    def test_independent_readback_cannot_self_consistently_claim_another_controller_setting(self):
        import json
        import tempfile
        from pathlib import Path
        from test_usb_campaign import capture_case_fixture
        from usb_test_campaign import validate_hardening_run,sha
        case='operational-hardening-volume-retry'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=capture_case_fixture(case,root);run['case_record_path']=str(root/'record.json')
            self.assertEqual(validate_hardening_run(run,case)['status'],'PASS')
            entry=run['hardening_volume_readbacks']['phases'][0]
            entry['snapshot'].update(headset_volume_percent=54,headset_volume_requested_percent=54)
            entry['pulse']['alsa_output.usb-fixture']['volume']=[round(65536*.54),round(65536*.54*.75)]
            for kind in ('snapshot','pulse'):
                path=Path(entry[kind+'_path']);path.write_text(json.dumps(entry[kind])+'\n')
                entry[kind+'_sha256']=sha(path)
            with self.assertRaisesRegex(ValueError,'canonical controller phase'):
                validate_hardening_run(run,case)

    def test_dual_level_preview_recomputes_lower_waveform_and_binds_its_readback(self):
        import tempfile
        from pathlib import Path
        from test_usb_campaign import capture_case_fixture
        from usb_test_campaign import validate_hardening_run,sha
        case='operational-hardening-cue-final-gesture'
        for mutation in ('constant-loudness','wrong-gesture','wrong-gain-metadata','unbound-readback'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);run=capture_case_fixture(case,root);run['case_record_path']=str(root/'record.json')
                self.assertEqual(validate_hardening_run(run,case)['status'],'PASS')
                cue=run['local_cue_output']
                if mutation=='constant-loudness':
                    monitor=Path(cue['monitor']['path']);pcm=bytearray(monitor.read_bytes())
                    low=cue['low_level_window'];reference=cue_reference_pcm(2)
                    pcm[low['frame_start']*2:low['frame_start']*2+len(reference)]=reference
                    monitor.write_bytes(pcm);cue['monitor']['sha256']=sha(monitor)
                elif mutation=='wrong-gesture':cue['low_level_window']=dict(cue['gesture_window'])
                elif mutation=='wrong-gain-metadata':cue['gain_mapping']['linear_gain']=1.
                else:cue['low_level_readbacks']['phases'][0]['snapshot']['headset_volume_requested_revision']+=1
                with self.assertRaises(ValueError):validate_hardening_run(run,case)


    def test_nested_recovery_deadline_requires_post_startup_operation_witness(self):
        import json
        import tempfile
        from pathlib import Path
        from test_usb_campaign import capture_case_fixture
        from usb_refinement_contract import hardening_exercise
        case='operational-hardening-radio-recover-operation-deadline'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=capture_case_fixture(case,root)
            item=run['refinement_contract']['sessions'][0];directory=root/'result'
            rows=[json.loads(line) for line in (directory/item['trace_file']).read_text().splitlines()]
            terminal=json.loads((directory/item['completion_file']).read_text())
            phases=run['refinement_exercise']['phases']
            self.assertEqual(hardening_exercise(rows,terminal,run['harness'],phases,case)['status'],'PASS')
            for mutation in ('missing','startup','outside-budget'):
                changed=copy.deepcopy(rows)
                nested=next(row for row in changed if row.get('event')=='operation-start' and row.get('operation')=='modem-flush')
                if mutation=='missing':changed.remove(nested)
                elif mutation=='startup':nested['state']=10
                else:nested['ms']=1100
                with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'inside the delayed RX operation'):
                    hardening_exercise(changed,terminal,run['harness'],phases,case)


    def test_external_balance_change_cannot_restore_the_stale_initial_balance(self):
        import json
        import tempfile
        from pathlib import Path
        from test_usb_campaign import capture_case_fixture
        from usb_test_campaign import validate_hardening_run,sha
        case='operational-hardening-volume-external-zero'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=capture_case_fixture(case,root);run['case_record_path']=str(root/'record.json')
            self.assertEqual(validate_hardening_run(run,case)['status'],'PASS')
            entry=run['hardening_volume_readbacks']['phases'][-1]
            entry['pulse']['alsa_output.usb-fixture']['volume']=[round(65536*.3),round(65536*.3*.75)]
            path=Path(entry['pulse_path']);path.write_text(json.dumps(entry['pulse'])+'\n');entry['pulse_sha256']=sha(path)
            with self.assertRaisesRegex(ValueError,'measured stereo balance'):
                validate_hardening_run(run,case)


    def test_raw_balance_anchor_accepts_one_integer_rounding_but_rejects_accumulated_drift(self):
        import tempfile
        from pathlib import Path
        from test_usb_campaign import capture_case_fixture
        from usb_test_campaign import validate_hardening_volume_readbacks
        case='operational-hardening-volume-paced-rounded'
        with tempfile.TemporaryDirectory() as temp:
            run=capture_case_fixture(case,Path(temp));proof=run['hardening_volume_readbacks']
            proof['initial_sinks']['alsa_output.usb-fixture']['volume']=[26214,19660]
            actual=((0,0),(3276,2456),(6553,4914),(6553,4914),(9830,7372),(65536,49150))
            for entry,channels in zip(proof['phases'],actual):entry['pulse']['alsa_output.usb-fixture']['volume']=list(channels)
            phases=run['refinement_exercise']['phases']
            self.assertEqual(validate_hardening_volume_readbacks(proof,case,False,phases)['status'],'PASS')
            proof['phases'][-1]['pulse']['alsa_output.usb-fixture']['volume']=[65536,48969]
            with self.assertRaisesRegex(ValueError,'measured stereo balance'):
                validate_hardening_volume_readbacks(proof,case,False,phases)


if __name__=='__main__':unittest.main()
