#!/usr/bin/env python3
"""Failure-path tests for the USB host campaign supervisor and audio oracle."""
from pathlib import Path
from array import array
import math
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from types import SimpleNamespace
import wave

from usb_test_support import (ProcessSupervisor, completed_turns, require, run_bounded,
                              save_json, validate_turn_decode, modem_waveform_match,
                              validate_operation_phase, validate_session_identity, microphone_frame_budget,
                              PulseTestRuntime, require_unix_socket_path, pulse_startup_failure,
                              audio_buffer_contract, planned_fixture_eof, validate_drain_loss_wait,
                              render_capacity_audit, validate_tail_backpressure,
                              capture_close_oracle, indexed_capture_source_match, validate_capture_release_boundary,
                              validate_capture_close_failure)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_child_failure_retains_exit_and_log(self):
        result = run_bounded([sys.executable, '-c', 'print("fixture failed",flush=True);raise SystemExit(7)'],
                             self.root/'child.log', 2, terminate_grace=.1)
        self.assertEqual(result['exit_code'], 7)
        self.assertFalse(result['timed_out'])
        self.assertIn('fixture failed', (self.root/'child.log').read_text())

    def test_hung_group_is_killed_and_reaped(self):
        script = 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(60)'
        before = time.monotonic()
        result = run_bounded([sys.executable, '-c', script], self.root/'hang.log', .15, terminate_grace=.1)
        self.assertTrue(result['timed_out'])
        self.assertLess(result['exit_code'], 0)
        self.assertLess(time.monotonic()-before, 2)
        self.assertIn('ready', (self.root/'hang.log').read_text())

    def test_early_fixture_exit_is_not_silent_or_restarted(self):
        supervisor = ProcessSupervisor(self.root, os.environ.copy())
        child = supervisor.start('source', [sys.executable, '-c', 'raise SystemExit(12)'])
        child.wait(timeout=2)
        with self.assertRaisesRegex(RuntimeError, 'source.*12'):
            supervisor.require_running()
        record = supervisor.close()
        self.assertEqual(len(record['children']), 1)
        self.assertEqual(record['children'][0]['exit_code'], 12)

    def test_planned_fixture_eof_still_rejects_a_failed_process(self):
        supervisor=ProcessSupervisor(self.root,os.environ.copy())
        child=supervisor.start('finite-source',[sys.executable,'-c','raise SystemExit(7)'])
        supervisor.expect_exit(child,exit_code=0);child.wait(timeout=2)
        with self.assertRaisesRegex(RuntimeError,'instead of 0'):
            supervisor.require_running()
        supervisor.close()

    def test_cleanup_reaps_all_children(self):
        supervisor = ProcessSupervisor(self.root, os.environ.copy())
        children = [supervisor.start('child'+str(i), [sys.executable, '-c', 'import time;time.sleep(60)']) for i in range(3)]
        record = supervisor.close(grace=.1)
        self.assertFalse(record['errors'])
        self.assertTrue(all(child.returncode is not None for child in children))

    def test_required_check_survives_python_optimization(self):
        result = run_bounded([sys.executable, '-O', '-c', 'from usb_test_support import require;require(False,"must fail")'],
                             self.root/'optimized.log', 2, cwd=Path(__file__).parent, terminate_grace=.1)
        self.assertNotEqual(result['exit_code'], 0)
        self.assertIn('must fail', (self.root/'optimized.log').read_text())

    def test_atomic_record_survives_replace(self):
        path=self.root/'record.json';save_json(path, {'status':'FAIL','reason':'primary'})
        self.assertIn('primary',path.read_text())
        self.assertFalse(path.with_name(path.name+'.tmp').exists())

    def test_interrupted_nested_supervisor_reaps_detached_child_group(self):
        nested = self.root/'nested.log'
        code = ('import signal,sys;from usb_test_support import run_bounded;'
                'signal.signal(signal.SIGTERM,lambda s,f:(_ for _ in ()).throw(RuntimeError("interrupted")));'
                'run_bounded([sys.executable,"-c","import os,signal,time;'
                'signal.signal(signal.SIGTERM,signal.SIG_IGN);print(os.getpid(),flush=True);time.sleep(60)"],'
                +repr(str(nested))+',60,terminate_grace=.1)')
        result=run_bounded([sys.executable,'-c',code],self.root/'outer.log',.2,
                           cwd=Path(__file__).parent,terminate_grace=1)
        self.assertTrue(result['timed_out'])
        pid=int(nested.read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid,0)


class PulseRuntimeTests(unittest.TestCase):
    def test_long_evidence_path_uses_short_real_unix_socket_without_path_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence=Path(temporary)/('attempt-'+'x'*72)/('case-'+'y'*72)
            evidence.mkdir(parents=True)
            invalid=evidence/'native'
            self.assertGreater(len(os.fsencode(invalid)),107)
            with socket.socket(socket.AF_UNIX) as rejected:
                with self.assertRaisesRegex(OSError,'AF_UNIX path too long'):
                    rejected.bind(str(invalid))
            with self.assertRaisesRegex(ValueError,'107-byte'):
                require_unix_socket_path(invalid)
            with mock.patch.dict(os.environ,{'PATH':''}):
                runtime=PulseTestRuntime(evidence)
                try:
                    with socket.socket(socket.AF_UNIX) as server:
                        server.bind(str(runtime.socket));server.listen(1)
                        with socket.socket(socket.AF_UNIX) as client:
                            client.connect(str(runtime.socket));client.sendall(b'preflight')
                            peer,_=server.accept()
                            with peer:
                                self.assertEqual(peer.recv(9),b'preflight')
                    save_json(evidence/'pulse-runtime-identity.json',runtime.identity())
                finally:
                    runtime.close()
            self.assertFalse(runtime.directory.exists())
            self.assertTrue((evidence/'pulse-runtime-identity.json').is_file())
            self.assertLessEqual(len(os.fsencode(runtime.socket)),107)

    def test_mandatory_module_failure_is_fatal_but_system_bus_noise_is_not(self):
        ordinary='E: [null-sink] util.c: Failed to connect to system bus: No such file or directory'
        self.assertIsNone(pulse_startup_failure(ordinary))
        fatal='E: [pulseaudio] module.c: Failed to load module "module-native-protocol-unix": initialization failed.'
        self.assertEqual(pulse_startup_failure(ordinary+'\n'+fatal),fatal)


class SinkOracleTests(unittest.TestCase):
    def record(self, seconds=1.2, blocks=10):
        return {'failures':0,'synchronized_blocks':blocks,'synthesis_blocks':blocks,'decoded_seconds':seconds}

    def test_complete_turn_passes(self):
        self.assertEqual(validate_turn_decode(self.record(),self.record(),1)['status'],'PASS')

    def test_missing_entire_second_turn_cannot_hide_behind_first_sync(self):
        validate_turn_decode(self.record(),self.record(),1)
        with self.assertRaisesRegex(ValueError,'Turn 2.*did not decode'):
            validate_turn_decode(self.record(),self.record(0,0),2)

    def test_truncated_speech_rejected_even_with_sync(self):
        with self.assertRaisesRegex(ValueError,'truncated'):
            validate_turn_decode(self.record(),self.record(.7,6),1)

    def test_decoder_failure_rejected(self):
        bad=self.record();bad['failures']=1
        with self.assertRaisesRegex(ValueError,'decoder failed'):
            validate_turn_decode(self.record(),bad,1)

    def test_each_turn_requires_drain(self):
        with self.assertRaisesRegex(ValueError,'omitted drain'):
            completed_turns([{'kind':'event','event':'state','state':3,'ms':1},
                             {'kind':'event','event':'state','state':8,'ms':2}])

    def audio(self, seed, gain=1):
        rng=random.Random(seed);samples=array('h')
        for frame in range(120):
            amplitude=rng.randrange(500,15000)*gain
            samples.extend(round(amplitude*math.sin(2*math.pi*200*i/8000)) for i in range(80))
        return samples.tobytes()

    def test_matching_modem_waveform_allows_transport_delay(self):
        reference=self.audio(7)
        observed=b'\0'*(8000*2//10)+self.audio(7,.25)
        result=modem_waveform_match(reference,8000,observed,8000)
        self.assertGreater(result['correlation'],.95)

    def test_headset_half_volume_gain_and_native_sample_phase(self):
        rng=random.Random(11);reference=array('h')
        for frame in range(120):
            amplitude=rng.randrange(500,15000)
            reference.extend(round(amplitude*math.sin(2*math.pi*6000*i/48000)) for i in range(480))
        observed=array('h',[0]*4801+[round(value*.125) for value in reference])
        result=modem_waveform_match(reference.tobytes(),48000,observed.tobytes(),48000,expected_gain=.125)
        self.assertGreater(result['correlation'],.95)


    def test_same_duration_valid_but_stale_turn_is_rejected(self):
        validate_turn_decode(self.record(),self.record(),2)
        with self.assertRaisesRegex(ValueError,'waveform differs'):
            modem_waveform_match(self.audio(19),8000,self.audio(7,.25),8000)

    def test_missing_whole_later_modem_turn_rejected(self):
        with self.assertRaises(ValueError):
            modem_waveform_match(self.audio(19),8000,b'\0'*19200,8000)

    def test_head_tail_and_interior_deletion_rejected(self):
        reference=self.audio(7);observed=self.audio(7,.25)
        for start,count in [(0,4800),(len(observed)-4800,4800),(8000,1600)]:
            with self.subTest(start=start):
                damaged=observed[:start]+b'\0'*count+observed[start+count:]
                with self.assertRaises(ValueError):
                    modem_waveform_match(reference,8000,damaged,8000)

    def test_duplicate_and_resized_payload_rejected(self):
        reference=self.audio(7);observed=self.audio(7,.25)
        first=observed[:len(observed)//2]
        values=array('h');values.frombytes(observed)
        stretched=array('h',(v for sample in values for v in (sample,sample))).tobytes()
        for damaged in [first+first,stretched]:
            with self.subTest(size=len(damaged)):
                with self.assertRaises(ValueError):
                    modem_waveform_match(reference,8000,damaged,8000)

    def test_gain_corruption_rejected(self):
        with self.assertRaisesRegex(ValueError,'gain differs'):
            modem_waveform_match(self.audio(7),8000,self.audio(7,.5),8000)

    def test_capture_frames_must_match_active_clock_in_both_directions(self):
        valid=microphone_frame_budget(1250,60000,960)
        self.assertEqual(valid['delivery_tolerance_percent'],1)
        for active_ms,accepted in [(1250,45600),(20000,945600),(1250,74400)]:
            with self.subTest(active_ms=active_ms,accepted=accepted):
                with self.assertRaisesRegex(ValueError,'microphone frames'):
                    microphone_frame_budget(active_ms,accepted,960)


class AudioReserveTests(unittest.TestCase):
    def descriptors(self):
        rows=[]
        for name,rate in [('mic',48000),('radio-rx',44100),('headset',48000),('modem',44100)]:
            rows.append(dict(kind='stream',stream=name,rate=rate,channels=1,maxlength=rate*2,
                tlength=round(rate*.45)*2,minreq=round(rate*.02)*2,prebuf=round(rate*.43)*2,fragsize=round(rate*.02)*2))
        return rows

    def test_four_stream_profile_covers_pause_codec_quantum_and_margin(self):
        self.assertEqual(audio_buffer_contract(self.descriptors())['status'],'PASS')

    def test_old_70ms_modem_contract_is_insufficient(self):
        rows=self.descriptors();rows[-1].update(maxlength=17640,tlength=6174,minreq=1764,prebuf=4412)
        result=audio_buffer_contract(rows)
        self.assertEqual(result['status'],'FAIL')
        self.assertAlmostEqual(result['streams']['modem']['usable_ms'],50)

    def test_headset_reserve_and_each_capture_capacity_are_required(self):
        for name in ('mic','radio-rx','headset'):
            rows=self.descriptors();row=next(row for row in rows if row['stream']==name)
            if name=='headset':row['prebuf']=0
            else:row['maxlength']=round(row['rate']*.15)*2
            with self.subTest(stream=name):
                self.assertEqual(audio_buffer_contract(rows)['status'],'FAIL')

    def test_finite_fixture_must_end_normally_after_its_full_duration(self):
        self.assertEqual(planned_fixture_eof(0,9.984,10.01)['status'],'PLANNED_EOF')
        for code,elapsed in [(1,10.01),(0,8.5)]:
            with self.assertRaises(ValueError):planned_fixture_eof(code,9.984,elapsed)



class CaptureCloseTests(unittest.TestCase):
    def fixture(self):
        source=array('h',((index*97)%60000-30000 if index<48528 else 30001 for index in range(50000)))
        if sys.byteorder!='little':source.byteswap()
        pcm=source.tobytes();accepted=pcm[528*2:48528*2]
        close=dict(kind='capture-close',turn=1,complete=True,start_index=528,cutoff_index=48528,
            consumed_index=48528,submitted_first_index=528,submitted_end_index=48528,
            mic_tap_start=0,mic_tap_end=48000,close_started_ms=2000,timing_issued_ms=2001,
            frozen_ms=2005,reached_ms=2010,closed_ms=2011,capture_started_ms=1000,write_index_bytes=96000,
            source_usec=10000,transport_usec=1000,sink_usec=200000,frontier_extra_frames=528,
            tail_frames=480,release_fence_index=48048,cutoff_split_frames=472,index_gap=False,close_timeout_ms=750)
        rows=[dict(kind='stream',stream='mic',rate=48000,channels=1,fragsize=1920),
            dict(kind='event',event='state',state=3,ms=1000),dict(kind='event',event='capture-release-observed',state=3,ms=2000),
            dict(kind='event',event='state',state=4,ms=2000),close,
            dict(kind='source-accounting',turn=1,ms=2012,active_ms=1000,accepted_mic_frames=48000,passed=True),
            dict(kind='event',event='state',state=8,ms=2300)]
        blocks=[dict(index_start=start,index_end=end,tap_start=start,tap_frames=end-start,valid=True)
                for start,end in ((0,20000),(20000,49000),(49000,50000))]
        return rows,accepted,blocks,pcm

    def test_finite_capture_frontier_ignores_monitor_playback_latency(self):
        rows,accepted,blocks,pcm=self.fixture()
        closed=capture_close_oracle(rows,accepted,1)
        result=indexed_capture_source_match(closed['turns'][0],accepted,blocks,pcm)
        self.assertEqual(closed['turns'][0]['cutoff_index'],48528)
        self.assertEqual(result['compared_frames'],48000)
        self.assertEqual(result['post_cutoff_frames_observed'],1472)
        self.assertEqual(result['split_block']['cutoff_index'],48528)

    def test_missing_pre_release_samples_cannot_be_repaired_with_post_cutoff_audio(self):
        rows,accepted,blocks,pcm=self.fixture()
        closed=capture_close_oracle(rows,accepted,1)['turns'][0]
        substituted=accepted[:20000]+accepted[20600:]+pcm[48528*2:(48528+300)*2]
        self.assertEqual(len(substituted),len(accepted))
        with self.assertRaisesRegex(ValueError,'exact indexed'):
            indexed_capture_source_match(closed,substituted,blocks,pcm)

    def test_index_gap_or_duplicate_is_rejected_even_when_pcm_length_matches(self):
        rows,accepted,blocks,pcm=self.fixture();closed=capture_close_oracle(rows,accepted,1)['turns'][0]
        for change in (-1,1):
            changed=[dict(block) for block in blocks]
            changed[1]['index_start']+=change;changed[1]['tap_frames']-=change
            with self.assertRaises(ValueError):indexed_capture_source_match(closed,accepted,changed,pcm)

    def test_incomplete_interval_or_post_cutoff_consumption_cannot_complete(self):
        for field,value in [('consumed_index',48527),('submitted_end_index',48529),('mic_tap_end',47999),('complete',False)]:
            rows,accepted,_,_=self.fixture();rows[4][field]=value
            with self.assertRaises(ValueError):capture_close_oracle(rows,accepted,1)

    def test_monitor_corrected_or_fabricated_frontier_is_rejected(self):
        for field,value in [('cutoff_index',38928),('frontier_extra_frames',527),('write_index_bytes',95999)]:
            rows,accepted,_,_=self.fixture();rows[4][field]=value
            with self.assertRaises(ValueError):capture_close_oracle(rows,accepted,1)

    def test_source_accounting_cannot_precede_closure_or_hide_wall_time_loss(self):
        rows,accepted,_,_=self.fixture();rows[4],rows[5]=rows[5],rows[4]
        with self.assertRaises(ValueError):capture_close_oracle(rows,accepted,1)
        rows,accepted,_,_=self.fixture();rows[2]['ms']=rows[3]['ms']=2100;rows[4]['close_started_ms']=2100
        rows[4].update(timing_issued_ms=2101,frozen_ms=2105,reached_ms=2110,closed_ms=2111)
        rows[5].update(ms=2112,active_ms=1100)
        with self.assertRaisesRegex(ValueError,'observed wall time'):capture_close_oracle(rows,accepted,1)

    def test_capture_closure_does_not_extend_750ms_deadline(self):
        rows,accepted,_,_=self.fixture();rows[4]['closed_ms']=2751;rows[-1]['ms']=2800;rows[5]['ms']=2752
        with self.assertRaisesRegex(ValueError,'bounded release'):capture_close_oracle(rows,accepted,1)

    def test_closed_capture_can_precede_later_rf_drain_failure_without_counting_a_turn(self):
        rows,accepted,_,_=self.fixture();rows[-1]['state']=5
        result=capture_close_oracle(rows,accepted,0)
        self.assertEqual(result['completed_intervals'],1)
        self.assertEqual(result['completed_rf_turns'],0)
        self.assertFalse(result['turns'][0]['rf_completed'])

    def boundary(self,mode):
        rows,accepted,blocks,pcm=self.fixture();close=rows[4]
        if mode=='held150':close.update(reached_ms=2110,closed_ms=2111);rows[5]['ms']=2112
        closed=capture_close_oracle(rows,accepted,1)['turns'][0]
        prefix=('INJECT MIC release mode='+mode+' hold_started_ms='+('1950' if mode=='held150' else '1800')+
            ' hold_ended_ms='+('2100' if mode=='held150' else '1950')+
            ' callback_started_ms='+('2100' if mode=='held150' else '1950')+
            ' barrier_started_ms='+('0' if mode=='held150' else '1999')+
            ' release_observed_ms=2000 callback_ended_ms='+('2110' if mode=='held150' else '2001')+
            ' blocked_peeks=1 cutoff_index=48528 sentinel_frames=1472 no-discard=1')
        return close,closed,accepted,blocks,pcm,prefix,mode

    def test_release_handshakes_and_post_cutoff_sentinel_are_independent(self):
        for mode in ('split','held150'):
            result=validate_capture_release_boundary(*self.boundary(mode))
            self.assertTrue(result['post_cutoff_sentinel_excluded'])
            self.assertEqual(result['post_cutoff_sentinel_frames'],1472)

    def test_callback_or_peek_bypass_cannot_claim_release_boundary_coverage(self):
        for mode,old,new in [('split','release_observed_ms=2000','release_observed_ms=2002'),
                             ('held150','blocked_peeks=1','blocked_peeks=0'),
                             ('held150','release_observed_ms=2000','release_observed_ms=2101')]:
            values=list(self.boundary(mode));values[-2]=values[-2].replace(old,new)
            with self.assertRaises(ValueError):validate_capture_release_boundary(*values)

    def test_missing_post_cutoff_sentinel_cannot_credit_exclusion(self):
        values=list(self.boundary('held150'));values[-2]=values[-2].replace('sentinel_frames=1472','sentinel_frames=0')
        with self.assertRaises(ValueError):validate_capture_release_boundary(*values)

    def failure(self,mode):
        rows,_,_,_=self.fixture();close=rows[4];close.update(complete=False)
        origin='local-stop' if mode=='stop' else 'microphone-close-frontier-invalid' if mode=='invalid' else 'aggregate-queue-limit'
        fault=1 if mode=='stop' else 23 if mode=='invalid' else 6
        if mode=='stop':close.update(frozen_ms=0,cutoff_index=0)
        rows[5]={'kind':'event','event':'first-fault','state':4,'ms':2012,'status':fault,'operation':origin}
        rows.insert(4,dict(kind='event',event='capture-close-start',state=4,ms=2001))
        rows.insert(6,dict(kind='sample',state=4,ms=2007,capture=500))
        harness=dict(fault=fault,turns=0,eoo=0,physical_tx=False,rf_clear_confirmed=True)
        log=('INJECT MIC close-timing requested_ms=2001 mode='+mode+' phase=drain\n'+
             'INJECT MIC close-timing mode='+mode+' real_ack_ms=2005 success=1 withheld='+('1' if mode=='missing' else '0')+' phase=drain\n')
        if mode=='invalid':log+='INJECT MIC close-timing retrograde read_index_bytes=100 write_index_bytes=0 source_usec=0 transport_usec=0 phase=drain'
        if mode=='stop':log+='INJECT MIC close-timing stop-before-forward stop_ms=2005 callback_invoked=1 phase=drain'
        return rows,harness,log,mode

    def test_actual_close_failure_requires_drain_ack_and_no_eoo_or_completed_turn(self):
        for mode in ('missing','invalid','stop'):
            self.assertEqual(validate_capture_close_failure(*self.failure(mode))['status'],'PASS')
        for key in ('turns','eoo'):
            values=list(self.failure('missing'));values[1][key]=1
            with self.assertRaises(ValueError):validate_capture_close_failure(*values)
        values=list(self.failure('invalid'));values[2]=values[2].replace('phase=drain','phase=tx')
        with self.assertRaises(ValueError):validate_capture_close_failure(*values)

    def test_late_capture_timing_cannot_commit_a_frontier_after_local_stop(self):
        values=list(self.failure('stop'))
        next(row for row in values[0] if row.get('kind')=='capture-close')['frozen_ms']=2006
        with self.assertRaisesRegex(ValueError,'after local Stop'):validate_capture_close_failure(*values)

    def test_unrelated_queue_failure_cannot_credit_withheld_capture_ack(self):
        values=list(self.failure('missing'))
        next(row for row in values[0] if row.get('event')=='first-fault')['operation']='render-queue-limit'
        with self.assertRaisesRegex(ValueError,'unrelated queue'):validate_capture_close_failure(*values)


class IdentityAndPhaseTests(unittest.TestCase):
    def drain_wait(self, dropped=150, turns=0):
        rows=[{'kind':'event','event':'operation-start','operation':'modem-trigger','state':4,'ms':101},
              {'kind':'event','event':'operation-end','operation':'modem-trigger','state':4,'ms':752,'status':1,'duration_ms':651},
              {'kind':'event','event':'first-fault','operation':'tx-status-disappeared-during-drain','state':4,'ms':753,'status':4}]
        harness={'fault':4,'turns':turns,'physical_tx':False}
        log=('RF_DROP_DURING_TRIGGER_WAIT started_ms=101 drop_ms='+str(dropped)+' ack_ms=751\n'
             'INJECT PA delayed-ack action=trigger phase=tx issued_ms=100 wait_started_ms=101 ack_ms=751 requested_ms=650 success=1')
        return rows,harness,log

    def test_rf_loss_during_successful_delayed_trigger_blocks_completion(self):
        self.assertEqual(validate_drain_loss_wait(*self.drain_wait())['successful_ack_wait_ms'],650)

    def test_rf_loss_before_wait_or_a_counted_turn_cannot_credit_the_boundary(self):
        for values in [self.drain_wait(dropped=99),self.drain_wait(turns=1)]:
            with self.assertRaises(ValueError):validate_drain_loss_wait(*values)

    def delayed_flush(self,phase='initial-rx',*,wait_started=101,ack=751,duration=650):
        state={'initial-rx':0,'tx':2,'rx-return':9}[phase]
        rows=[{'kind':'session','operation_timeout_ms':1000},
              {'kind':'event','event':'operation-start','operation':'modem-flush','state':state,'ms':101},
              {'kind':'event','event':'operation-end','operation':'modem-flush','state':state,'ms':101+duration,
               'status':1,'duration_ms':duration}]
        log=('INJECT PA delayed-ack action=flush phase='+phase+' issued_ms=100 wait_started_ms='+str(wait_started)+
             ' ack_ms='+str(ack)+' requested_ms=650 success=1')
        return rows,phase,'delay',log

    def test_delayed_ack_clock_begins_inside_all_three_controller_wait_phases(self):
        for phase in ('initial-rx','tx','rx-return'):
            with self.subTest(phase=phase):
                result=validate_operation_phase(*self.delayed_flush(phase))
                self.assertEqual(result['delay_witness']['hold_ms'],650)
                self.assertEqual(result['delay_witness']['trace_start_ms'],101)

    def test_issue_time_delay_cannot_credit_649ms_controller_wait(self):
        with self.assertRaises(ValueError):
            validate_operation_phase(*self.delayed_flush(wait_started=100,ack=750,duration=649))

    def test_declared_trace_duration_cannot_mask_short_hold_or_pre_wait_origin(self):
        for values in [self.delayed_flush(ack=750),self.delayed_flush(wait_started=100,ack=750)]:
            with self.assertRaises(ValueError):validate_operation_phase(*values)

    def test_unmatched_or_missing_delay_witness_cannot_credit_runtime_duration(self):
        rows,phase,mode,log=self.delayed_flush()
        for changed in ('',log.replace('phase=initial-rx','phase=tx'),log.replace('ack_ms=751','ack_ms=752'),
                        log.replace('requested_ms=650','requested_ms=649')):
            with self.assertRaises(ValueError):validate_operation_phase(rows,phase,mode,changed)

    def trace(self):
        rows=[{'kind':'session','session_id':'one','source_commit':'source','build_id':'build'},
              {'kind':'sample','ms':12,'state':6,'fault':1}]
        done={'kind':'completion','terminal':True,'session_id':'one','source_commit':'source',
              'build_id':'build','trace_complete':True,'trace_history_truncated':False,
              'events_dropped':0,'storage_errno':0,'last_sample_ms':12,'state':6,'fault':1}
        return rows,done

    def test_stale_source_cannot_qualify(self):
        rows,done=self.trace()
        with self.assertRaisesRegex(ValueError,'source commit'):
            validate_session_identity(rows,done,'new-source','build')

    def test_missing_or_mismatched_completion_rejected(self):
        rows,done=self.trace()
        with self.assertRaisesRegex(ValueError,'completion'):
            validate_session_identity(rows,{})
        done['last_sample_ms']=11
        with self.assertRaisesRegex(ValueError,'final controller sample'):
            validate_session_identity(rows,done)

    def test_storage_failure_only_passes_its_explicit_case(self):
        rows,done=self.trace();done['storage_errno']=13
        with self.assertRaisesRegex(ValueError,'storage result'):
            validate_session_identity(rows,done)
        validate_session_identity(rows,done,expected_storage_error=True)

    def test_initial_failure_cannot_credit_tx_phase(self):
        rows=[{'kind':'event','event':'operation-end','operation':'modem-flush','state':0,
               'status':-1,'duration_ms':1}, {'kind':'event','event':'first-fault','state':0,'status':15}]
        with self.assertRaisesRegex(ValueError,'requested tx'):
            validate_operation_phase(rows,'tx','reject')
        self.assertEqual(validate_operation_phase(rows,'initial-rx','reject')['trace_state'],0)


class DriverFailureTests(unittest.TestCase):
    def test_failed_controller_without_terminal_receipt_stays_incomplete(self):
        import test_usb_audio_integration as driver
        import json
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime=root/'result';runtime.mkdir()
            (runtime/'harness-failure.json').write_text(json.dumps({'status':'FAIL','primary_fault':6}))
            (runtime/'freedv-usb-current.json').write_text(json.dumps({'completion_basename':'session-completion.json'}))
            record={'status':'FAIL','error':'primary render-queue-limit'}
            driver.retain_failed_controller(root,SimpleNamespace(),record)
            self.assertEqual(record['error'],'primary render-queue-limit')
            self.assertEqual(record['harness_failure']['primary_fault'],6)
            self.assertIn('did not finish',record['failure_collection_error'])

    def test_endpoint_failure_preserves_primary_error_and_reaps_running_controller(self):
        import test_usb_audio_integration as driver
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            speech = root/'speech.wav'
            with wave.open(str(speech), 'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE','not compressed'))
                wav.writeframes(b'\0'*320)
            modem=root/'modem.raw';modem.write_bytes(b'\0'*320)
            args=SimpleNamespace(output=root/('attempt-'+'a'*72)/('case-'+'b'*72),binary=Path(sys.executable),speech=speech,
                                 modem=modem,runner=None,fault_inject_so=None,case='normal')

            class RealChildren(ProcessSupervisor):
                def start(self, name, command, stdout=None, env=None, cwd=None):
                    # Real child lifetimes, with fake services so this regression
                    # needs neither Pulse nor a codec build.
                    script='import time;time.sleep(60)'
                    if name=='pulse':
                        script=('import os,socket,time;server=socket.socket(socket.AF_UNIX);'
                                'server.bind(os.environ["PULSE_SERVER"].split("unix:",1)[1]);'
                                'server.listen(1);peer,_=server.accept();peer.close();time.sleep(60)')
                    elif name=='test':
                        proof={'requested_output_argument':str(command[1])}
                        script=('import time,json,os;from pathlib import Path;'
                                'record='+repr(proof)+';record["cwd"]=os.getcwd();'
                                'Path("incidents").mkdir();'
                                'record["incident_directory"]=str(Path.cwd()/"incidents");'
                                'Path("freedv-usb-namespace-proof.json").write_text(json.dumps(record));'
                                'print("TX_ACTIVE",flush=True);time.sleep(60)')
                    return super().start(name,[sys.executable,'-c',script],stdout=stdout,env=env,cwd=cwd)

            endpoint_text=('alsa_input.usb-fixture null 1ch 16000Hz\n'
                           'alsa_output.usb-fixture null 2ch 48000Hz\n'
                           'radio_sink_fixture null 2ch 44100Hz\n')

            def command(command, case_root, name, commands, env, timeout=3, check=True):
                if name.startswith('endpoints-active'):
                    raise RuntimeError('primary endpoint failure')
                if name.startswith('pulse-ready'):
                    with socket.socket(socket.AF_UNIX) as client:
                        try:
                            client.connect(env['PULSE_SERVER'].split('unix:',1)[1])
                        except OSError:
                            return {'exit_code':1,'timed_out':False}, 'socket not ready'
                return {'exit_code':0,'timed_out':False}, endpoint_text

            with mock.patch.object(driver,'ProcessSupervisor',RealChildren), \
                 mock.patch.object(driver,'check_command',command), \
                 mock.patch.object(driver.shutil,'which',return_value=sys.executable), \
                 mock.patch.dict(os.environ,{'PATH':''}):
                with self.assertRaisesRegex(RuntimeError,'primary endpoint failure'):
                    driver.run_case(args)
            import json
            record=json.loads((args.output/'case-result.json').read_text())
            self.assertEqual(record['error'],'primary endpoint failure')
            self.assertEqual(record['status'],'FAIL')
            self.assertFalse(record['process_cleanup']['errors'])
            children=record['process_cleanup']['children']
            self.assertTrue(any(child['name']=='test' for child in children))
            self.assertTrue(all(child['exit_code'] is not None for child in children))
            self.assertGreater(len(os.fsencode(args.output/'native')),107)
            self.assertLessEqual(record['pulse_runtime']['socket_path_bytes'],107)
            self.assertTrue(record['pulse_runtime']['cleanup_attempted'])
            self.assertTrue(record['pulse_runtime']['directory_removed'])
            self.assertIn('socket='+record['pulse_runtime']['socket'],(args.output/'default.pa').read_text())
            self.assertTrue((args.output/'pulse-runtime-identity.json').is_file())
            proof=json.loads((args.output/'result/freedv-usb-namespace-proof.json').read_text())
            self.assertEqual(proof['requested_output_argument'],'.')
            self.assertEqual(proof['cwd'],str(args.output/'result'))
            self.assertEqual(proof['incident_directory'],str(args.output/'result/incidents'))
            self.assertEqual(record['controller_output_argument'],'.')
            self.assertEqual(record['controller_working_directory'],str(args.output/'result'))
            self.assertTrue((args.output/'result/incidents').is_dir())


class InfrastructureFailureTests(unittest.TestCase):
    def child_receipts(self,command,log_path,timeout,env=None,terminate_grace=None):
        import json
        case_name=command[command.index('--case')+1]
        qualification=case_name=='rf-disabled'
        case=Path(command[command.index('--output')+1]);runtime=case/'result';runtime.mkdir(parents=True)
        collector=case/'failure-collector-contract/actual';collector.mkdir(parents=True)
        identity={'source_commit':env['USB_TEST_EXPECTED_SOURCE_COMMIT'],'build_id':env['USB_TEST_EXPECTED_BUILD_ID']}
        fault=4 if qualification else 13;origin='radio-packet-stale' if qualification else 'modem-buffer-budget'
        code=-6 if qualification and getattr(self,'abort_qualification',False) else 2
        session='freedv-usb-session'
        def write(path,value):path.write_text(json.dumps(value))
        write(case/'case-result.json',dict(status='FAIL',controller_exit_code=code,failure_collector_contract={'status':'PASS'}))
        write(runtime/'harness-failure.json',dict(status='FAIL',exit_code=code,cleanup_requested=True,stopped=True,closed=True,
            primary_fault=fault,primary_origin=origin,rf_disabled=qualification,keys=0,telemetry_freeze_recovered_ms=550))
        write(runtime/'freedv-usb-current.json',dict(session_id=session,completion_basename=session+'-completion.json',
            trace_basename=session+'.jsonl',**identity))
        write(runtime/(session+'-completion.json'),dict(terminal=True,trace_complete=True,rf_clear_confirmed=True,
            fault=fault,rf_disabled=qualification,qualification_complete=False,**identity))
        write(runtime/(session+'-fault.json'),dict(first_fault={'code':fault,'origin':origin},**identity))
        write(collector/'rf-evidence.json',dict(status='BLOCKED',ready_for_rf_test=False))
        rows=[dict(kind='event',event='first-fault',ms=500,status=fault,operation=origin)]
        if qualification:
            rows[:0]=[dict(kind='event',event='qualification-start',ms=100),
                dict(kind='sample',qualification_running=True,ms=300)]
            write(runtime/'qualification-status-freeze.json',dict(kind='test-telemetry-freeze',ms=200,packet_ms=190,
                sequence=10,duration_ms=350,qualification_running=not getattr(self,'freeze_before_qualification',False),keys=0))
        (runtime/(session+'.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in rows))
        (case/'test.log').write_text('INJECT negotiated buffer rejection\n' if not qualification else 'Injected finite telemetry freeze\n')
        Path(log_path).write_text('Unit receipt adapter: no audio or controller execution\n')
        return {'exit_code':1,'timed_out':False}

    def run_receipts(self,root):
        import usb_harness_failure_selftest as subject
        inputs=[]
        for name in ('binary','speech','modem','runner','injector'):
            path=root/name;path.write_bytes(name.encode());inputs.append(path)
        with mock.patch.object(subject,'run_bounded',side_effect=self.child_receipts):
            return subject.run_failure_preservation(*inputs,root/'proof','source','build',subject.sha(inputs[0]))

    def test_infrastructure_prerequisite_cannot_pass_a_successful_or_hung_driver(self):
        import usb_harness_failure_selftest as subject
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);inputs=[]
            for name in ('binary','speech','modem','runner','injector'):
                path=root/name;path.write_bytes(name.encode());inputs.append(path)
            for number,execution in enumerate([{'exit_code':0,'timed_out':False},{'exit_code':-9,'timed_out':True}]):
                with mock.patch.object(subject,'run_bounded',return_value=execution):
                    result=subject.run_failure_preservation(*inputs,root/str(number),'source','build',subject.sha(inputs[0]))
                self.assertEqual(result['status'],'FAIL')
                self.assertFalse(result['checks'])

    def test_both_initial_and_active_qualification_children_must_preserve_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=self.run_receipts(Path(temporary))
        self.assertEqual(result['schema'],'x6200.usb.infrastructure.v2')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(set(result['variants']),{'initial_buffer','qualification_telemetry'})
        self.assertEqual(result['variants']['qualification_telemetry']['terminal_fault_code'],4)

    def test_initial_failure_preserved_cannot_mask_later_qualification_abort(self):
        self.abort_qualification=True
        with tempfile.TemporaryDirectory() as temporary:
            result=self.run_receipts(Path(temporary))
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['variants']['initial_buffer']['status'],'PASS')
        self.assertEqual(result['variants']['qualification_telemetry']['status'],'FAIL')
        self.assertIn('aborted',result['reason'])
        self.assertFalse(result['checks'])

    def test_pre_qualification_freeze_cannot_claim_active_qualification_coverage(self):
        self.freeze_before_qualification=True
        with tempfile.TemporaryDirectory() as temporary:
            result=self.run_receipts(Path(temporary))
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('during active',result['reason'])


class RenderCapacityTests(unittest.TestCase):
    def evidence(self):
        def row(stage,ms,reserved,before,generated):
            return dict(kind='render-queue',stage=stage,ms=ms,reserved_from_frames=reserved,
                queue_before_frames=before,generated_frames=generated,queue_after_frames=before+generated,
                input_frames=0,output_capacity_frames=4608,queue_limit_frames=22050)
        rows=[row('regular-wait',120,18000,18000,0),row('regular',260,17000,17000,4000),
              row('src-final',280,100,100,19),row('src-final',282,100,100,0)]
        harness={'render_hold':{'started_ms':100,'ended_ms':250,'hits':9},
                 'render_queue':{'limit_frames':22050,'output_bound_frames':4608,'high_water_frames':21000,
                    'deferred_calls':1,'final_deferred_calls':0,'final_flush_calls':2,'final_flush_frames':19,
                    'generated_frames':4019,'written_frames':4019}}
        log='INJECT render hold started no-discard=1\nINJECT render hold ended no-discard=1'
        return rows,harness,log

    def test_bounded_backpressure_preserves_the_complete_tail(self):
        result=validate_tail_backpressure(*self.evidence())
        self.assertEqual(result['render_audit']['final_flush_frames'],19)

    def test_old_stale_capacity_counterexample_is_rejected(self):
        rows,_,_=self.evidence()
        rows[-1].update(reserved_from_frames=21921,queue_before_frames=22040,
                        generated_frames=19,queue_after_frames=22059)
        with self.assertRaisesRegex(ValueError,'bounded queue'):
            render_capacity_audit(rows)

    def test_unreserved_conversion_cannot_credit_a_safe_final_count(self):
        rows,_,_=self.evidence()
        rows[1].update(reserved_from_frames=19000,queue_before_frames=17000)
        with self.assertRaisesRegex(ValueError,'reserving'):
            render_capacity_audit(rows)

    def test_discarded_tail_or_pressure_outside_the_gate_is_rejected(self):
        rows,harness,log=self.evidence();harness['render_queue']['written_frames']-=19
        with self.assertRaises(ValueError):validate_tail_backpressure(rows,harness,log)
        rows,harness,log=self.evidence();rows[0]['ms']=90
        with self.assertRaises(ValueError):validate_tail_backpressure(rows,harness,log)


if __name__=='__main__':
    unittest.main()
