#!/usr/bin/env python3
"""Off-air WAV framing/rate/size gates; no radio access."""
from pathlib import Path
import tempfile
import unittest
import wave
import json
import hashlib
import subprocess
import shutil
import sys
import textwrap
import uuid
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
        for channels,width,rate,seconds in [(2,4,48000,1),(1,2,22050,1),(4,2,48000,1)]:
            with self.subTest(channels=channels,width=width,rate=rate,seconds=seconds),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'bad.wav'
                with wave.open(str(path),'wb') as w:
                    w.setparams((channels,width,rate,0,'NONE','not compressed'))
                    w.writeframes(b'\0'*(channels*width*rate*seconds))
                with self.assertRaises(ValueError):subject.read_sdr(path)

    def test_reported_30_364_second_recording_keeps_every_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sdr.wav';frames=1457456
            raw=b'\0\0'*(frames-1)+b'\x34\x12'
            with wave.open(str(path),'wb') as wav:
                wav.setparams((1,2,48000,0,'NONE','not compressed'));wav.writeframes(raw)
            pcm,info=subject.read_sdr(path)
            self.assertEqual(pcm,raw)
            self.assertEqual(info['frames'],frames)
            self.assertAlmostEqual(info['seconds'],30.3636666667)

    def test_full_120_second_stereo_recording_fits_preservation_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'sdr.wav';frames=120*48000
            with wave.open(str(path),'wb') as wav:
                wav.setparams((2,2,48000,0,'NONE','not compressed'))
                wav.writeframes(b'\0'*((frames-1)*4)+b'\x34\x12\x34\x12')
            output=root/'results';output.mkdir()
            preserved=subject.preserve_sdr(output,path)
            self.assertLessEqual(path.stat().st_size,subject.MAX_SDR_BYTES)
            self.assertEqual(subject.sha256(output/'sdr-usb-audio.wav'),preserved['sha256'])
            pcm,info=subject.read_sdr(path)
            self.assertEqual(info['frames'],frames)
            self.assertEqual(len(pcm),frames*2)
            self.assertEqual(pcm[-2:],b'\x34\x12')

    def test_duration_error_identifies_duration_without_iq_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'long.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setparams((1,2,8000,0,'NONE','not compressed'))
                wav.writeframes(b'\0\0'*(120*8000+1))
            with self.assertRaisesRegex(ValueError,'duration.*120 seconds') as error:
                subject.read_sdr(path)
            self.assertNotIn('iq',str(error.exception).lower())


class CollectionTests(unittest.TestCase):
    def collect_fixture(self, root, fault=1, rate=None, decoder_failure=False, rows=None, manifest_fields=None,
                        active_worker=False, immutable_fault=None, decoded_success=False,
                        completion=None, pointer=None, inventory_status='USB_DUPLEX_CANDIDATE',
                        extra_trace=None, journal=None, mic_frames=480, readiness=None):
        traces=root/'traces';traces.mkdir()
        (root/'proc').mkdir()
        if rows is None:
            rows=[dict(state=5 if fault>1 else 6,fault=fault,rf_tx=False)]
        (traces/'freedv-usb-fixture.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        (traces/'freedv-usb-fixture-mic.raw').write_bytes(b'\0\0'*mic_frames)
        if pointer is not None:
            (traces/'freedv-usb-current.json').write_text(json.dumps(pointer))
        if completion is not None:
            (traces/'freedv-usb-fixture-completion.json').write_text(
                completion if isinstance(completion, str) else json.dumps(completion))
        if readiness is not None:
            (traces/'freedv-usb-fixture-readiness.json').write_text(json.dumps(readiness))
        if extra_trace is not None:
            (traces/'freedv-usb-newer.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in extra_trace))
        if immutable_fault:
            (traces/'freedv-usb-fixture-fault.json').write_text(json.dumps(immutable_fault))
            (traces/'freedv-usb-fixture-metadata.json').write_text(json.dumps(dict(kind='session',rf_disabled=True)))
        if active_worker:
            descriptors=root/'proc/123/fd';descriptors.mkdir(parents=True)
            (descriptors/'7').symlink_to(traces/'freedv-usb-fixture.jsonl')
        runner=root/'runner';runner.write_bytes(b'fixture runner, never executed')
        library=root/'library';library.write_bytes(b'fixture codec, never loaded')
        manifest=dict(gui_sha256='fixture',runner_sha256=subject.sha256(runner),rade_sha256=subject.sha256(library))
        manifest.update(manifest_fields or {})
        mapped={'/tmp':traces,'/proc':root/'proc','/opt/freedv-benchmark/bin/freedv_voice_runner':runner,
                '/opt/freedv-benchmark/lib/librade.so.0.1':library}
        if journal is not None:
            directory=root/'journal';directory.mkdir()
            for name,record in journal.items():
                (directory/name).write_text(json.dumps(record))
            mapped['/mnt/freedv-usb-incidents']=directory
        sdr=None
        if rate:
            sdr=root/'input.wav'
            with wave.open(str(sdr),'wb') as w:
                w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(b'\0\0'*rate)
        output=root/'results';output.mkdir()
        real_decode=subject.decode_sdr
        def decode(output, manifest, sdr, result):
            if decoded_success:
                result['rf_recording_decoded']=True
                result['offair_decoder_stats']=dict(failures=0,synchronized_blocks=1,synthesis_blocks=1)
            else:
                return real_decode(output,manifest,sdr,result)
        with patch.object(subject,'Path',side_effect=lambda p:mapped.get(str(p),Path(p))), \
             patch.object(subject,'collect',return_value={'status':inventory_status}), \
             patch.object(subject,'post_stop_diagnostics',return_value={'scope':'POST_STOP_CURRENT_STATE fixture'}), \
             patch.object(subject,'decode_sdr',side_effect=decode), \
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

    def test_first_gui_fault_remains_primary_when_sdr_also_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(kind='session',rf_disabled=False),
                  dict(kind='sample',state=5,generation=2,rf_tx=False,fault=2,
                       first_fault=dict(code=2,origin='mic_flush_timeout',stream='mic',pa_errno=0)),
                  dict(kind='event',event='cleanup',state=6)]
            result,output,_=self.collect_fixture(Path(temp),rate=22050,rows=rows)
            self.assertEqual(result['primary_failure_stage'],'gui')
            self.assertEqual(result['reason'],'mic_flush_timeout')
            self.assertEqual(result['first_gui_fault']['stream'],'mic')
            self.assertEqual(result['stages']['audio']['status'],'FAULT')
            self.assertEqual(result['last_gui_state']['generation'],2)
            self.assertEqual(result['stages']['rf_decode']['status'],'BLOCKED')
            self.assertEqual(len(result['blockers']),2)
            self.assertTrue((output/'usb-session.jsonl').exists())

    def test_radio_control_fault_does_not_claim_an_audio_continuity_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(kind='sample',state=5,generation=2,rf_tx=False,fault=20,
                       first_fault=dict(code=20,origin='control-key',stream='control'))]
            result,_,_=self.collect_fixture(Path(temp),rows=rows)
            self.assertEqual(result['primary_failure_stage'],'gui')
            self.assertEqual(result['reason'],'control-key')
            self.assertEqual(result['stages']['audio']['status'],'UNASSESSED')

    def test_completed_rf_disabled_qualification_is_not_rf_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(kind='session',rf_disabled=True,qualification_target=3),
                  dict(kind='sample',state=6,generation=8,rf_valid=True,rf_tx=False,fault=1,rf_disabled=True,
                       qualification_complete=True,qualification_turns=3)]
            result,_,_=self.collect_fixture(Path(temp),rows=rows)
            self.assertEqual(result['status'],'COLLECTED_NO_SDR_RECORDING')
            self.assertTrue(result['historical_runtime_evidence'])
            self.assertFalse(result['ready_for_rf_test'])
            self.assertFalse(result['rf_tested'])
            self.assertFalse(result['rf_recording_decoded'])
            self.assertEqual(result['stages']['qualification']['status'],'PASS')

    def test_qualification_pass_with_unexpected_tx_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(kind='session',rf_disabled=True,qualification_target=3),
                  dict(kind='sample',state=3,generation=4,rf_valid=True,rf_tx=True,fault=0,rf_disabled=True),
                  dict(kind='sample',state=6,generation=8,rf_valid=True,rf_tx=False,fault=1,rf_disabled=True,
                       qualification_complete=True,qualification_turns=3)]
            result,_,_=self.collect_fixture(Path(temp),rows=rows)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertFalse(result['ready_for_rf_test'])

    def test_incomplete_rf_disabled_qualification_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(kind='session',rf_disabled=True,qualification_target=3),
                  dict(kind='sample',state=6,generation=4,rf_tx=False,fault=1,rf_disabled=True,
                       qualification_complete=False,qualification_turns=1)]
            result,_,_=self.collect_fixture(Path(temp),rows=rows)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['primary_failure_stage'],'qualification')

    def test_live_session_after_qualification_is_not_still_marked_rf_disabled(self):
        rows=[dict(kind='session',rf_disabled=True,qualification_target=3),
              dict(kind='sample',state=1,generation=8,rf_tx=False,fault=0,rf_disabled=True,
                   qualification_complete=True,qualification_turns=3),
              dict(kind='sample',state=6,generation=10,rf_tx=False,fault=1,rf_disabled=False,
                   qualification_complete=True,qualification_turns=3)]
        with tempfile.TemporaryDirectory() as temp:
            result,_,_=self.collect_fixture(Path(temp),rows=rows)
            self.assertFalse(result['rf_disabled'])
            self.assertEqual(result['status'],'COLLECTED_NO_SDR_RECORDING')
            self.assertEqual(result['stages']['qualification']['status'],'PASS')

    def test_new_image_cannot_bypass_required_readiness_with_legacy_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(state=6,fault=1,rf_tx=False,rf_valid=True,rf_clear_confirmed=True)]
            result,_,_=self.collect_fixture(Path(temp),rows=rows,manifest_fields=dict(usb_startup_contract_version=2,on_device_readiness_required=True))
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['primary_failure_stage'],'qualification')
            self.assertFalse(result['ready_for_rf_test'])

    def test_normal_stop_without_rx_confirmation_stays_blocked_despite_valid_sdr_decode(self):
        cases=[dict(rf_clear_confirmed=False,rf_valid=True,rf_tx=False),
               dict(rf_clear_confirmed=True,rf_valid=False,rf_tx=False),
               dict(rf_clear_confirmed=True,rf_valid=True,rf_tx=True),
               dict(rf_valid=True,rf_tx=False)]
        for fields in cases:
            with self.subTest(fields=fields),tempfile.TemporaryDirectory() as temp:
                row=dict(kind='sample',state=6,generation=10,fault=1,rf_disabled=False,
                         qualification_complete=True,qualification_turns=3,**fields)
                rows=[dict(kind='session',schema='x6200.usb-audio.v2',qualification_target=3),row]
                result,_,_=self.collect_fixture(Path(temp),rows=rows,rate=48000,decoded_success=True,
                    manifest_fields=dict(usb_startup_contract_version=2,on_device_readiness_required=True))
                self.assertEqual(result['status'],'BLOCKED')
                self.assertEqual(result['primary_failure_stage'],'gui')
                self.assertIn('Final RX confirmation',result['reason'])
                self.assertEqual(result['stages']['control_completion']['status'],'UNCONFIRMED')
                self.assertEqual(result['stages']['rf_decode']['status'],'DECODED_LISTENING_REQUIRED')
                self.assertTrue(result['rf_recording_decoded'])
                self.assertEqual(result['stages']['collection']['status'],'PASS')

    def test_confirmed_rx_and_valid_sdr_decode_keep_independent_success_outcomes(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(kind='session',schema='x6200.usb-audio.v2',qualification_target=3),
                  dict(kind='sample',state=6,generation=10,fault=1,rf_disabled=False,rf_valid=True,rf_tx=False,
                       rf_clear_confirmed=True,qualification_complete=True,qualification_turns=3)]
            result,_,_=self.collect_fixture(Path(temp),rows=rows,rate=48000,decoded_success=True,
                manifest_fields=dict(usb_startup_contract_version=2,on_device_readiness_required=True))
            self.assertEqual(result['status'],'RF_RECORDING_DECODED_LISTENING_REQUIRED')
            self.assertEqual(result['stages']['control_completion']['status'],'CONFIRMED_RX')
            self.assertFalse(result['rf_tested'])

    def test_active_worker_preserves_immutable_fault_but_never_mutable_trace_or_pcm(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot=dict(schema='x6200.usb-first-fault.v1',first_fault=dict(code=11,origin='audio-worker-stalled',stream='control'))
            result,output,sdr=self.collect_fixture(Path(temp),rate=48000,active_worker=True,immutable_fault=snapshot)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['stages']['collection']['status'],'BLOCKED')
            self.assertTrue(result['worker_active'])
            self.assertEqual(result['primary_failure_stage'],'gui')
            self.assertEqual(result['reason'],'audio-worker-stalled')
            self.assertEqual(result['first_gui_fault']['code'],11)
            self.assertEqual(json.loads((output/'runtime-fault.json').read_text()),snapshot)
            self.assertTrue((output/'runtime-metadata.json').exists())
            self.assertFalse((output/'usb-session.jsonl').exists())
            self.assertFalse((output/'mic.wav').exists())
            self.assertEqual((output/'sdr-usb-audio.wav').read_bytes(),sdr.read_bytes())
            self.assertEqual(result['stages']['rf_decode']['status'],'BLOCKED')

    def test_active_worker_without_sidecar_remains_a_collection_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.collect_fixture(Path(temp),active_worker=True)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['primary_failure_stage'],'collection')
            self.assertFalse((output/'usb-session.jsonl').exists())


class CurrentSessionTests(unittest.TestCase):
    identity = dict(session_id='freedv-usb-fixture', build_id='fixture-build', source_commit='e'*40)

    def output_rows(self, turns=3):
        rows=[]
        for turn in range(1,turns+1):
            base=100+(turn-1)*200
            rows.extend([dict(kind='event',event='state',state=3,ms=base),
                         dict(kind='event',event='state',state=4,ms=base+10),
                         dict(kind='backend-tail',schema='x6200.usb.playback-tail.v1',turn=turn,
                              drain_started_ms=base+10,protocol_ack_ms=base+20,
                              timing_issued_ms=base+21,timing_issued_us=(base+21)*1000,
                              timing_ack_ms=base+30,timing_ack_us=(base+30)*1000,
                              write_index_bytes=202,read_index_bytes=200,sink_usec=10000,
                              transport_usec=5000,configured_sink_usec=50000,protocol_pending_usec=23,
                              wait_usec=15023,wait_until_us=(base+30)*1000+15024,
                              completed_us=(base+45)*1000+500,complete=True,
                              rf_tx_confirmed=True,rf_disabled=True,scope='pulse-reported-backend-tail',
                              **self.identity),
                         dict(kind='event',event='state',state=8,ms=base+45)])
        return rows

    def fixture(self, root, rows=None, completion_fields=None, pointer_fields=None,
                completion_missing=False, pointer_missing=False, completion_raw=None,
                output_contract=True, **kwargs):
        header = dict(kind='session', schema='x6200.usb-audio.v3', qualification_target=3, **self.identity)
        final = dict(kind='sample', ms=1000, state=6, generation=8, fault=1, rf_disabled=True,
                     rf_valid=True, rf_tx=False, rf_clear_confirmed=True, qualification_complete=True,
                     qualification_turns=3, qualification_target=3,turns=3)
        if output_contract:
            header['usb_output_contract_version']=1
        if rows is None:
            rows=[header,*self.output_rows(),final] if output_contract else [header,final]
        elif output_contract:
            rows=[dict(row) for row in rows]
            for row in rows:
                if row.get('kind')=='session':row.setdefault('usb_output_contract_version',1)
                if row.get('kind')=='sample':row.setdefault('turns',row.get('qualification_turns',0))
            samples=[row for row in rows if row.get('kind')=='sample']
            turns=samples[-1].get('turns',0) if samples else 0
            if type(turns) is int and turns>0 and not any(row.get('kind')=='event' for row in rows):
                rows[1:1]=self.output_rows(turns)
        samples=[row for row in rows if row.get('kind')=='sample']
        turns=samples[-1].get('turns',0) if samples else 0
        completion = dict(kind='completion', schema='x6200.usb-completion.v1', terminal=True,
                          trace_complete=True, finalized_ms=1001, last_sample_ms=1000,
                          trace_rows=sum(row.get('kind') in ('event','sample') for row in rows),events_dropped=0,
                          trace_history_truncated=False, storage_errno=0,
                          **self.identity, **{name:final[name] for name in
                          ('state','fault','rf_disabled','rf_valid','rf_tx','rf_clear_confirmed',
                           'qualification_complete','qualification_turns','qualification_target')})
        if output_contract:
            completion.update(usb_output_contract_version=1,turns=turns,
                              playback_tail_completed_intervals=sum(row.get('kind')=='backend-tail' and row.get('complete') is True for row in rows),
                              playback_tail_interrupted_intervals=sum(row.get('kind')=='backend-tail' and row.get('complete') is False for row in rows))
        completion.update(completion_fields or {})
        pointer = dict(kind='session-pointer', schema='x6200.usb-current.v1', terminal=False,
                       trace_basename='freedv-usb-fixture.jsonl', completion_basename='freedv-usb-fixture-completion.json',
                       **self.identity)
        pointer.update(pointer_fields or {})
        manifest = dict(usb_startup_contract_version=3, on_device_readiness_required=True,
                        firmware_commit=self.identity['source_commit'],build_id=self.identity['build_id'])
        if output_contract:manifest['usb_output_contract_version']=1
        manifest.update(kwargs.pop('manifest_fields', {}))
        return CollectionTests.collect_fixture(self, root, rows=rows,
            manifest_fields=manifest, pointer=None if pointer_missing else pointer,
            completion=None if completion_missing else completion_raw if completion_raw is not None else completion,
            **kwargs)

    def fault(self, origin='capture-not-resumed-after-preparation', **fields):
        return dict(kind='first-fault', first_fault=dict(code=17,origin=origin,stream='mic'), **self.identity, **fields)

    def operational_fixture(self, root, *, ready=True, completion_fields=None, readiness_fields=None,
                            manifest_version=4, schema_version=4, fault=0, receive_only=False):
        receipt = dict(kind='startup-readiness', schema='x6200.usb.operational-readiness.v1',
                       status='PASS', ready_ms=200, generation=1, tuning_revision=7,
                       radio_sequence=30, ptt_release_sequence=4, ptt=False, rf_valid=True,
                       rf_tx=False, configuration_ready=True, captures_fresh=True, buffers_ready=True,
                       tx_eligible=not receive_only, headset_id='usb-fixture', headset_name='USB fixture', source='usb_mic', sink='usb_headphones',
                       **self.identity)
        receipt.update(readiness_fields or {})
        final = dict(kind='sample', usb_startup_contract_version=4, ms=1000, state=5 if fault > 1 else 6, fault=fault,
                     generation=9, rf_disabled=receive_only, rf_valid=True, rf_tx=False, rf_clear_confirmed=True,
                     qualification_complete=False, qualification_target=0, qualification_turns=0, turns=0,
                     operational=True, startup_ready=ready, leaving=True, configuration_ready=True, tx_eligible=not receive_only,
                     tuning_revision=7, tx_timeout_seconds=180)
        header = dict(kind='session', schema=f'x6200.usb-audio.v{schema_version}',
                      usb_startup_contract_version=schema_version, usb_output_contract_version=1,
                      operational=True, qualification_target=0, **self.identity)
        completion = {key:value for key,value in final.items() if key not in ('kind', 'ms', 'generation')}
        completion.update(completion_reason='fault' if fault > 1 else 'navigation')
        completion.update(completion_fields or {})
        rows = [header, *([receipt] if ready else []), final]
        return self.fixture(root, rows=rows, completion_fields=completion,
                            readiness=receipt if ready else None,
                            manifest_fields=dict(usb_startup_contract_version=manifest_version))

    def test_operational_readiness_is_distinct_from_diagnostic_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_ = self.operational_fixture(Path(temp))
            self.assertEqual(result['status'], 'COLLECTED_NO_SDR_RECORDING')
            self.assertEqual(result['stages']['startup']['status'], 'PASS')
            self.assertEqual(result['stages']['qualification']['status'], 'NOT_REQUESTED')
            self.assertEqual(result['stages']['audio']['status'], 'OPERATIONAL_STARTUP_READY')
            self.assertEqual(result['runtime_completion']['completion_reason'], 'navigation')
            self.assertFalse(result['runtime_completion']['qualification_complete'])
            self.assertTrue((output/'runtime-readiness.json').exists())
            self.assertFalse(result['ready_for_rf_test'])
            self.assertFalse(result['rf_tested'])

    def test_operational_receive_only_readiness_is_not_diagnostic_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            result,_,_ = self.operational_fixture(Path(temp), receive_only=True)
            self.assertEqual(result['status'], 'COLLECTED_NO_SDR_RECORDING')
            self.assertTrue(result['rf_disabled'])
            self.assertEqual(result['stages']['startup']['status'], 'PASS')
            self.assertEqual(result['stages']['qualification']['status'], 'NOT_REQUESTED')
            self.assertFalse(result['runtime_startup_readiness']['tx_eligible'])
            self.assertFalse(result['ready_for_rf_test'])

    def test_operational_missing_readiness_is_blocked_without_losing_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            result,_,_ = self.operational_fixture(Path(temp), ready=False, fault=1)
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['stages']['session']['status'], 'COMPLETE')
            self.assertEqual(result['stages']['startup']['status'], 'BLOCKED')

    def test_operational_contract_cannot_claim_qualification_or_ignore_readiness(self):
        cases = [dict(completion_fields=dict(qualification_complete=True)),
                 dict(completion_fields=dict(qualification_target=3)),
                 dict(readiness_fields=dict(ptt=True)), dict(readiness_fields=dict(captures_fresh=False)),
                 dict(readiness_fields=dict(radio_sequence=True)),
                 dict(readiness_fields=dict(source_commit='f'*40)),
                 dict(completion_fields=dict(tx_timeout_seconds=20)),
                 dict(completion_fields=dict(completion_reason='unknown'))]
        for change in cases:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                result,output,_ = self.operational_fixture(Path(temp), **change)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertTrue((output/'runtime-completion.json').exists())

    def test_operational_and_diagnostic_versions_cannot_be_mixed(self):
        for manifest,schema in ((3,4), (4,3)):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as temp:
                result,_,_ = self.operational_fixture(Path(temp), manifest_version=manifest, schema_version=schema)
                self.assertEqual(result['status'], 'BLOCKED')

    def test_current_closed_session_is_ready_without_claiming_rf(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp))
            self.assertEqual(result['status'],'READY_FOR_RF_TEST')
            self.assertFalse(result['rf_tested'])
            self.assertEqual(result['stages']['session']['status'],'COMPLETE')
            self.assertEqual(result['session_identity'],self.identity)
            self.assertTrue((output/'runtime-completion.json').exists())
            self.assertEqual(result['output_contract']['completed_turns'],3)

    def test_legacy_startup_v3_remains_diagnostic_without_output_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),output_contract=False)
            self.assertEqual(result['status'],'COLLECTED_NO_SDR_RECORDING')
            self.assertFalse(result['ready_for_rf_test'])
            self.assertEqual(result['output_contract']['status'],'DIAGNOSTIC_ONLY')
            self.assertTrue((output/'usb-session.jsonl').exists())

    def test_required_output_marker_rejects_legacy_header_or_flags_without_receipts(self):
        for marker in (None,2,True,'1',1):
            rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,**self.identity),
                  *self.output_rows(),
                  dict(kind='sample',ms=1000,state=6,generation=8,fault=1,rf_disabled=True,rf_valid=True,
                       rf_tx=False,rf_clear_confirmed=True,qualification_complete=True,
                       qualification_turns=3,qualification_target=3,turns=3)]
            if marker is not None:rows[0]['usb_output_contract_version']=marker
            if marker==1 and type(marker) is int:
                rows=[row for row in rows if row.get('kind')!='backend-tail']
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as temp:
                result,output,_=self.fixture(Path(temp),rows=rows,output_contract=False,
                    manifest_fields=dict(usb_output_contract_version=1),
                    completion_fields=dict(usb_output_contract_version=1,turns=3,
                        playback_tail_completed_intervals=0,playback_tail_interrupted_intervals=0))
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])
                self.assertTrue((output/'usb-session.jsonl').exists())

    def test_unknown_manifest_output_contract_cannot_promote(self):
        for marker in (None,0,2,True,'1'):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),manifest_fields={'usb_output_contract_version':marker})
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])

    def test_output_completion_marker_is_mandatory_for_new_image(self):
        for marker in (None,0,2,True,'1'):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),completion_fields={'usb_output_contract_version':marker})
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])

    def test_receipt_identity_ordinal_and_tail_timing_are_collection_gates(self):
        for field,value in (('source_commit','f'*40),('session_id','freedv-usb-old'),
                            ('turn',2),('wait_usec',0),('completed_us',130000),
                            ('schema','x6200.usb.playback-tail.v0')):
            rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,**self.identity),
                  *self.output_rows(),
                  dict(kind='sample',ms=1000,state=6,generation=8,fault=1,rf_disabled=True,rf_valid=True,
                       rf_tx=False,rf_clear_confirmed=True,qualification_complete=True,
                       qualification_turns=3,qualification_target=3,turns=3)]
            next(row for row in rows if row.get('kind')=='backend-tail')[field]=value
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temp:
                result,output,_=self.fixture(Path(temp),rows=rows)
                self.assertEqual(result['status'],'BLOCKED')
                self.assertEqual(result['primary_failure_stage'],'collection')
                self.assertFalse(result['ready_for_rf_test'])
                self.assertTrue((output/'usb-session.jsonl').exists())

    def test_zero_turn_stop_or_fault_has_complete_collection_without_tail(self):
        for state,fault in ((6,1),(5,9)):
            rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,**self.identity),
                  dict(kind='sample',ms=1000,state=state,generation=2,fault=fault,rf_disabled=True,
                       rf_valid=True,rf_tx=False,rf_clear_confirmed=True,qualification_complete=False,
                       qualification_turns=0,qualification_target=3,turns=0)]
            with self.subTest(state=state),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),rows=rows,completion_fields=dict(state=state,fault=fault,
                    qualification_complete=False,qualification_turns=0))
                self.assertEqual(result['stages']['collection']['status'],'PASS')
                self.assertEqual(result['stages']['session']['status'],'COMPLETE')
                self.assertEqual(result['output_contract']['receipt_count'],0)
                self.assertEqual(result['output_contract']['completed_turns'],0)
                self.assertFalse(result['ready_for_rf_test'])

    def test_crashed_rx_snapshot_cannot_be_mistaken_for_stopped(self):
        rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,**self.identity),
              dict(kind='sample',ms=1000,state=1,generation=8,fault=0,rf_disabled=True,rf_valid=True,rf_tx=False,
                   rf_clear_confirmed=True,qualification_complete=True,qualification_turns=3,qualification_target=3)]
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),rows=rows)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertFalse(result['ready_for_rf_test'])
            self.assertTrue((output/'usb-session.jsonl').exists())
            self.assertTrue((output/'mic.wav').exists())

    def test_missing_or_incomplete_completion_never_promotes(self):
        cases=[dict(completion_missing=True),dict(completion_fields={'terminal':False}),
               dict(completion_fields={'trace_complete':False}),dict(completion_fields={'last_sample_ms':999}),
               dict(completion_fields={'finalized_ms':999}),dict(completion_fields={'rf_tx':True}),
               dict(completion_fields={'qualification_turns':2}),dict(completion_fields={'trace_rows':2}),
               dict(completion_fields={'events_dropped':1}),dict(completion_fields={'trace_history_truncated':True}),
               dict(completion_fields={'storage_errno':5}),dict(completion_fields={'storage_errno':False})]
        for fields in cases:
            with self.subTest(fields=fields),tempfile.TemporaryDirectory() as temp:
                result,output,_=self.fixture(Path(temp),**fields)
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])
                self.assertTrue((output/'usb-session.jsonl').exists())

    def test_build_and_session_mismatches_are_preserved_and_blocked(self):
        cases=[dict(completion_fields={'build_id':'old-build'}),dict(completion_fields={'source_commit':'f'*40}),
               dict(completion_fields={'session_id':'freedv-usb-other'}),dict(pointer_fields={'build_id':'old-build'}),
               dict(pointer_fields={'source_commit':'f'*40})]
        for fields in cases:
            with self.subTest(fields=fields),tempfile.TemporaryDirectory() as temp:
                result,output,_=self.fixture(Path(temp),**fields)
                self.assertEqual(result['status'],'BLOCKED')
                self.assertTrue((output/'runtime-current.json').exists())
                self.assertTrue((output/'runtime-completion.json').exists())
                self.assertTrue((output/'usb-session.jsonl').exists())

    def test_current_pointer_overrides_newer_unrelated_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),extra_trace=[dict(state=5,fault=2,rf_tx=False)])
            self.assertEqual(result['status'],'READY_FOR_RF_TEST')
            self.assertIn('fixture-build',(output/'usb-session.jsonl').read_text())

    def test_no_pointer_or_legacy_fallback_for_current_image(self):
        for fields in (dict(pointer_missing=True),dict(rows=[dict(state=6,fault=1,rf_tx=False)])):
            with self.subTest(fields=fields),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),**fields)
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])

    def test_malformed_current_contract_is_blocked_without_a_type_crash(self):
        for version in ('3', None, True, 99):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),manifest_fields={'usb_startup_contract_version':version})
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])

    def test_wrongly_typed_terminal_values_cannot_claim_readiness(self):
        for field,value in (('rf_disabled','false'),('fault',False),('qualification_turns',True)):
            rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,**self.identity),
                  dict(kind='sample',ms=1000,state=6,generation=8,fault=1,rf_disabled=True,rf_valid=True,rf_tx=False,
                       rf_clear_confirmed=True,qualification_complete=True,qualification_turns=3,qualification_target=3)]
            rows[-1][field]=value
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),rows=rows,completion_fields={field:value})
                self.assertEqual(result['status'],'BLOCKED')
                self.assertFalse(result['ready_for_rf_test'])

    def test_explicit_bounded_soak_audio_budget_is_shared_with_collector(self):
        rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,trace_pcm_seconds=40,**self.identity),
              dict(kind='sample',ms=1000,state=6,generation=8,fault=1,rf_disabled=True,rf_valid=True,rf_tx=False,
                   rf_clear_confirmed=True,qualification_complete=True,qualification_turns=3,qualification_target=3)]
        with tempfile.TemporaryDirectory() as temp:
            result,_,_=self.fixture(Path(temp),rows=rows,mic_frames=48000*31)
            self.assertEqual(result['status'],'READY_FOR_RF_TEST')
            self.assertEqual(result['audio_taps']['mic']['seconds'],31)

    def test_audio_cannot_exceed_declared_or_global_evidence_budget(self):
        for limit,frames in ((30,48000*31),(121,480)):
            rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,trace_pcm_seconds=limit,**self.identity),
                  dict(kind='sample',ms=1000,state=6,generation=8,fault=1,rf_disabled=True,rf_valid=True,rf_tx=False,
                       rf_clear_confirmed=True,qualification_complete=True,qualification_turns=3,qualification_target=3)]
            with self.subTest(limit=limit),tempfile.TemporaryDirectory() as temp:
                result,_,_=self.fixture(Path(temp),rows=rows,mic_frames=frames)
                self.assertEqual(result['status'],'BLOCKED')

    def test_clean_trace_cannot_hide_immutable_fault(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),immutable_fault=self.fault())
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['reason'],'capture-not-resumed-after-preparation')
            self.assertEqual(result['first_gui_fault']['code'],17)
            self.assertTrue((output/'runtime-fault.json').exists())

    def test_disagreeing_fault_records_remain_blocked_with_both_origins(self):
        rows=[dict(kind='session',schema='x6200.usb-audio.v3',qualification_target=3,**self.identity),
              dict(kind='sample',ms=1000,state=5,generation=8,fault=17,rf_disabled=True,rf_valid=True,rf_tx=False,
                   rf_clear_confirmed=True,qualification_complete=False,qualification_turns=0,qualification_target=3,
                   first_fault=dict(code=17,origin='trace-origin',stream='mic'))]
        with tempfile.TemporaryDirectory() as temp:
            result,_,_=self.fixture(Path(temp),rows=rows,immutable_fault=self.fault('sidecar-origin'),
                completion_fields=dict(state=5,fault=17,qualification_complete=False,qualification_turns=0))
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['conflicting_faults']['trace']['origin'],'trace-origin')
            self.assertEqual(result['conflicting_faults']['immutable']['origin'],'sidecar-origin')

    def test_corrupt_completion_bytes_and_trace_remain_available(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),completion_raw='{incomplete publication')
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual((output/'runtime-completion.json').read_text(),'{incomplete publication')
            self.assertTrue((output/'usb-session.jsonl').exists())

    def test_returned_incomplete_inventory_cannot_promote_closed_session(self):
        for status in ('BLOCKED','NO_USB_AUDIO_DEVICE','USB_AUDIO_INCOMPLETE'):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temp:
                result,output,_=self.fixture(Path(temp),inventory_status=status)
                self.assertEqual(result['status'],'BLOCKED')
                self.assertEqual(result['primary_failure_stage'],'collection')
                self.assertTrue((output/'usb-session.jsonl').exists())

    def test_reboot_journal_preserves_fault_but_never_replaces_closed_trace(self):
        slot=7
        records=dict(metadata=dict(kind='session',schema='x6200.usb-audio.v3',**self.identity),fault=self.fault())
        journal={'current-session.json':dict(schema='x6200.usb.incident-pointer.v1',slot=slot,session_id=self.identity['session_id'])}
        journal.update({f'slot-{slot:02d}-{kind}.json':dict(schema='x6200.usb.incident.v1',kind=kind,
                        session_id=self.identity['session_id'],record=record) for kind,record in records.items()})
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),pointer_missing=True,journal=journal)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertFalse(result['ready_for_rf_test'])
            self.assertEqual(result['first_gui_fault']['origin'],'capture-not-resumed-after-preparation')
            self.assertEqual(result['persistent_journal']['status'],'COLLECTED')
            self.assertTrue((output/'persistent-incidents/current-session.json').exists())

    def test_journal_slot_mismatch_is_retained_as_partial_diagnostics(self):
        slot=7
        wrong=(slot+1)%16
        journal={'current-session.json':dict(schema='x6200.usb.incident-pointer.v1',slot=slot,session_id=self.identity['session_id']),
                 f'slot-{wrong:02d}-fault.json':dict(schema='x6200.usb.incident.v1',kind='fault',
                     session_id=self.identity['session_id'],record=self.fault())}
        with tempfile.TemporaryDirectory() as temp:
            result,output,_=self.fixture(Path(temp),pointer_missing=True,journal=journal)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['persistent_journal']['status'],'PARTIAL')
            self.assertNotIn('first_gui_fault',result)
            self.assertTrue((output/f'persistent-incidents/slot-{wrong:02d}-fault.json').exists())


class KitContractTests(unittest.TestCase):
    def test_cli_kit_requires_startup_v3_and_preserves_exact_hashes(self):
        script=Path(subject.__file__).with_name('build_usb_rf_kit.py')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            manifest=dict(usb_rf_test_ready=True,software_gates_passed=True,
                          gui_sha256='a'*64,runner_sha256='b'*64,rade_sha256='c'*64,image_sha256='d'*64,firmware_commit='e'*40)
            path=root/'image.json';path.write_text(json.dumps(manifest))
            command=[sys.executable,str(script),'--image-manifest',str(path),'--output',str(root/'kit.zip')]
            rejected=subprocess.run(command,capture_output=True,text=True,timeout=15)
            self.assertNotEqual(rejected.returncode,0)
            self.assertFalse((root/'kit.zip').exists())
            manifest.update(usb_startup_contract_version=2,on_device_readiness_required=True)
            path.write_text(json.dumps(manifest))
            legacy=subprocess.run(command,capture_output=True,text=True,timeout=15)
            self.assertNotEqual(legacy.returncode,0)
            manifest.update(usb_startup_contract_version=3,build_id='fixture-build')
            path.write_text(json.dumps(manifest))
            for marker in (None,0,2,True,'1'):
                if marker is None:manifest.pop('usb_output_contract_version',None)
                else:manifest['usb_output_contract_version']=marker
                path.write_text(json.dumps(manifest))
                rejected=subprocess.run(command,capture_output=True,text=True,timeout=15)
                self.assertNotEqual(rejected.returncode,0)
                self.assertFalse((root/'kit.zip').exists())
            manifest['usb_output_contract_version']=1
            path.write_text(json.dumps(manifest))
            accepted=subprocess.run(command,capture_output=True,text=True,timeout=15)
            self.assertEqual(accepted.returncode,0,accepted.stderr)
            import zipfile
            with zipfile.ZipFile(root/'kit.zip') as z:
                kit=json.loads(z.read('rf-test-manifest.json'))
                self.assertEqual(kit['usb_startup_contract_version'],3)
                self.assertEqual(kit['usb_output_contract_version'],1)
                self.assertIn('usb_output_contract.py',kit['files'])
                self.assertTrue(kit['on_device_readiness_required'])
                self.assertFalse(kit['rf_commands'])
                for name in ('gui_sha256','runner_sha256','rade_sha256','image_sha256','firmware_commit','build_id'):
                    self.assertEqual(kit[name],manifest[name])
                for name,digest in kit['files'].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
            manifest['usb_startup_contract_version']=4
            path.write_text(json.dumps(manifest))
            operational_command=[*command[:-1],str(root/'operational-kit.zip')]
            accepted=subprocess.run(operational_command,capture_output=True,text=True,timeout=15)
            self.assertEqual(accepted.returncode,0,accepted.stderr)
            with zipfile.ZipFile(root/'operational-kit.zip') as z:
                kit=json.loads(z.read('rf-test-manifest.json'))
                self.assertEqual(kit['usb_startup_contract_version'],4)
                self.assertEqual(kit['usb_output_contract_version'],1)


@unittest.skipIf(sys.platform == 'win32', 'The radio CLI uses Linux /tmp.')
class CliTests(unittest.TestCase):
    def execute_cli(self, root, arguments):
        adapter=textwrap.dedent('''\
            import sys
            from pathlib import Path
            root=Path(sys.argv[1]);sys.path.insert(0,str(root))
            import usb_rf_evidence as subject
            subject.Path=lambda value: {'/tmp':root/'traces','/proc':root/'proc',
                '/mnt/freedv-usb-incidents':root/'journal'}.get(str(value),Path(value))
            sys.argv=[str(root/'usb_rf_evidence.py')]+sys.argv[2:]
            raise SystemExit(subject.main())
        ''')
        return subprocess.run([sys.executable,'-c',adapter,str(root),*arguments],capture_output=True,text=True,timeout=15)

    def run_current_cli(self, **fixture_fields):
        with tempfile.TemporaryDirectory(prefix='usb current cli ') as temp:
            root=Path(temp)
            # The real CLI, parser, report writer and process exit execute in
            # the subprocess. Only inventory and runtime filesystem locations
            # are adapted; no global /tmp pointer or radio is touched.
            fixture=CurrentSessionTests()
            fixture.fixture(root, **fixture_fields)
            shutil.copy2(Path(subject.__file__),root/'usb_rf_evidence.py')
            shutil.copy2(Path(subject.__file__).with_name('usb_output_contract.py'),root/'usb_output_contract.py')
            (root/'rf-test-manifest.json').write_text(json.dumps(dict(gui_sha256='fixture',
                usb_startup_contract_version=3,usb_output_contract_version=1,on_device_readiness_required=True,
                firmware_commit=fixture.identity['source_commit'],build_id=fixture.identity['build_id'])))
            (root/'usb_headset_inventory.py').write_text(textwrap.dedent('''\
                import hashlib
                def collect(**kwargs): return {'status':'USB_DUPLEX_CANDIDATE'}
                def sha256(path): return hashlib.sha256(path.read_bytes()).hexdigest()
                def command(args, **kwargs):
                    return {'command':args,'exit_code':0,'stdout':'fixture only','stderr':'','timed_out':False,'output_truncated':False}
            '''))
            process=self.execute_cli(root,['--output',str(root/'cli-results')])
            report=json.loads((root/'cli-results/rf-evidence.json').read_text())
            self.assertTrue((root/'cli-results/SHA256SUMS').exists())
            return process,report

    def test_cli_current_closed_session_exits_zero_with_bound_identity(self):
        process,report=self.run_current_cli()
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertEqual(report['status'],'READY_FOR_RF_TEST')
        self.assertEqual(report['session_identity'],CurrentSessionTests.identity)

    def test_cli_current_missing_completion_exits_one(self):
        process,report=self.run_current_cli(completion_missing=True)
        self.assertEqual(process.returncode,1,process.stdout+process.stderr)
        self.assertEqual(report['status'],'BLOCKED')
        self.assertFalse(report['ready_for_rf_test'])

    def test_cli_current_sidecar_fault_exits_one_with_original_reason(self):
        process,report=self.run_current_cli(immutable_fault=CurrentSessionTests().fault())
        self.assertEqual(process.returncode,1,process.stdout+process.stderr)
        self.assertEqual(report['reason'],'capture-not-resumed-after-preparation')

    def test_cli_current_mismatched_build_exits_one(self):
        process,report=self.run_current_cli(completion_fields={'build_id':'older-build'})
        self.assertEqual(process.returncode,1,process.stdout+process.stderr)
        self.assertEqual(report['status'],'BLOCKED')

    def run_cli(self, rows, invalid_sdr=False, damaged_manifest=False):
        with tempfile.TemporaryDirectory(prefix='usb rf cli ') as temp:
            root=Path(temp)
            shutil.copy2(Path(subject.__file__),root/'usb_rf_evidence.py')
            shutil.copy2(Path(subject.__file__).with_name('usb_output_contract.py'),root/'usb_output_contract.py')
            (root/'rf-test-manifest.json').write_text(json.dumps(dict(gui_sha256='fixture',runner_sha256='fixture',rade_sha256='fixture')))
            if damaged_manifest:
                (root/'rf-test-manifest.json').write_text('{damaged transfer')
            # Inventory and filesystem roots are isolated adapters. The actual
            # published parser, report writer and OS exit contract execute below.
            (root/'usb_headset_inventory.py').write_text(textwrap.dedent('''\
                import hashlib
                def collect(**kwargs): return {'status':'USB_DUPLEX_CANDIDATE'}
                def sha256(path): return hashlib.sha256(path.read_bytes()).hexdigest()
                def command(args, **kwargs):
                    return {'command':args,'exit_code':0,'stdout':'fixture only','stderr':'','timed_out':False,'output_truncated':False}
            '''))
            (root/'traces').mkdir();(root/'proc').mkdir()
            trace=root/'traces'/('freedv-usb-cli-'+uuid.uuid4().hex+'.jsonl')
            trace.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            output=root/'results'
            args=['--output',str(output)]
            if invalid_sdr:
                wav=root/'invalid-rate.wav'
                with wave.open(str(wav),'wb') as w:
                    w.setparams((1,2,22050,0,'NONE','not compressed'));w.writeframes(b'\0\0'*22050)
                args+=['--sdr-wav',str(wav)]
            try:
                process=self.execute_cli(root,args)
                report=json.loads((output/'rf-evidence.json').read_text())
                self.assertEqual((output/'usb-session.jsonl').read_text(),trace.read_text())
                preserved=(output/'sdr-usb-audio.wav').read_bytes() if invalid_sdr else None
                if invalid_sdr:self.assertEqual(preserved,wav.read_bytes())
                self.assertTrue((output/'SHA256SUMS').exists())
                return process,report
            finally:
                trace.unlink(missing_ok=True)

    def test_cli_blocked_report_exits_one_and_preserves_evidence(self):
        process,report=self.run_cli([dict(state=5,fault=2,rf_tx=False,first_fault=dict(code=2,origin='mic_flush_timeout'))])
        self.assertEqual(process.returncode,1,process.stdout+process.stderr)
        self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(report['reason'],'mic_flush_timeout')

    def test_cli_successful_diagnostic_collection_exits_zero(self):
        process,report=self.run_cli([dict(state=6,fault=1,rf_tx=False)])
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertEqual(report['status'],'COLLECTED_NO_SDR_RECORDING')
        self.assertFalse(report['rf_recording_decoded'])

    def test_cli_invalid_wav_exits_one_and_preserves_input(self):
        process,report=self.run_cli([dict(state=6,fault=1,rf_tx=False)],invalid_sdr=True)
        self.assertEqual(process.returncode,1,process.stdout+process.stderr)
        self.assertEqual(report['status'],'BLOCKED')
        self.assertIn('22050',report['reason'])

    def test_cli_damaged_manifest_exits_one_but_preserves_runtime_trace(self):
        process,report=self.run_cli([dict(state=6,fault=1,rf_tx=False)],damaged_manifest=True)
        self.assertEqual(process.returncode,1,process.stdout+process.stderr)
        self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(report['primary_failure_stage'],'collection')
        self.assertIn('Kit manifest unavailable',report['reason'])


if __name__=='__main__':unittest.main()
