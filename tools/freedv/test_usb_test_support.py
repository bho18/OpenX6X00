#!/usr/bin/env python3
"""Failure-path tests for the USB host campaign supervisor and audio oracle."""
from pathlib import Path
from array import array
import math
import os
import random
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
                              validate_operation_phase, validate_session_identity, microphone_frame_budget)


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


class IdentityAndPhaseTests(unittest.TestCase):
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
    def test_endpoint_failure_preserves_primary_error_and_reaps_running_controller(self):
        import test_usb_audio_integration as driver
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            speech = root/'speech.wav'
            with wave.open(str(speech), 'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE','not compressed'))
                wav.writeframes(b'\0'*320)
            modem=root/'modem.raw';modem.write_bytes(b'\0'*320)
            args=SimpleNamespace(output=root/'case',binary=Path(sys.executable),speech=speech,
                                 modem=modem,runner=None,fault_inject_so=None,case='normal')

            class RealChildren(ProcessSupervisor):
                def start(self, name, command, stdout=None, env=None):
                    # Real child lifetimes, with fake services so this regression
                    # needs neither Pulse nor a codec build.
                    script='import time;time.sleep(60)'
                    if name=='test':
                        script='import time;print("TX_ACTIVE",flush=True);time.sleep(60)'
                    return super().start(name,[sys.executable,'-c',script],stdout=stdout,env=env)

            endpoint_text=('alsa_input.usb-fixture null 1ch 16000Hz\n'
                           'alsa_output.usb-fixture null 2ch 48000Hz\n'
                           'radio_sink_fixture null 2ch 44100Hz\n')

            def command(command, case_root, name, commands, env, timeout=3, check=True):
                if name.startswith('endpoints-active'):
                    raise RuntimeError('primary endpoint failure')
                return {'exit_code':0,'timed_out':False}, endpoint_text

            with mock.patch.object(driver,'ProcessSupervisor',RealChildren), \
                 mock.patch.object(driver,'check_command',command), \
                 mock.patch.object(driver.shutil,'which',return_value=sys.executable):
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


if __name__=='__main__':
    unittest.main()
