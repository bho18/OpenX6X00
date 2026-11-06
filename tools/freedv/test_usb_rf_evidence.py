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
        for channels,width,rate,seconds in [(2,4,48000,1),(1,2,22050,1),(1,2,48000,31),(4,2,48000,1)]:
            with self.subTest(channels=channels,width=width,rate=rate,seconds=seconds),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'bad.wav'
                with wave.open(str(path),'wb') as w:
                    w.setparams((channels,width,rate,0,'NONE','not compressed'))
                    w.writeframes(b'\0'*(channels*width*rate*seconds))
                with self.assertRaises(ValueError):subject.read_sdr(path)


class CollectionTests(unittest.TestCase):
    def collect_fixture(self, root, fault=1, rate=None, decoder_failure=False, rows=None, manifest_fields=None,
                        active_worker=False, immutable_fault=None, decoded_success=False):
        traces=root/'traces';traces.mkdir()
        (root/'proc').mkdir()
        if rows is None:
            rows=[dict(state=5 if fault>1 else 6,fault=fault,rf_tx=False)]
        (traces/'freedv-usb-fixture.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        (traces/'freedv-usb-fixture-mic.raw').write_bytes(b'\0\0'*480)
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
             patch.object(subject,'collect',return_value={'status':'USB_DUPLEX_CANDIDATE'}), \
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
            self.assertEqual(result['status'],'READY_FOR_RF_TEST')
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


class KitContractTests(unittest.TestCase):
    def test_cli_kit_requires_startup_v2_and_preserves_exact_hashes(self):
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
            accepted=subprocess.run(command,capture_output=True,text=True,timeout=15)
            self.assertEqual(accepted.returncode,0,accepted.stderr)
            import zipfile
            with zipfile.ZipFile(root/'kit.zip') as z:
                kit=json.loads(z.read('rf-test-manifest.json'))
                self.assertEqual(kit['usb_startup_contract_version'],2)
                self.assertTrue(kit['on_device_readiness_required'])
                self.assertFalse(kit['rf_commands'])
                for name in ('gui_sha256','runner_sha256','rade_sha256','image_sha256','firmware_commit'):
                    self.assertEqual(kit[name],manifest[name])
                for name,digest in kit['files'].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)


@unittest.skipIf(sys.platform == 'win32', 'The radio CLI uses Linux /tmp.')
class CliTests(unittest.TestCase):
    def run_cli(self, rows, invalid_sdr=False, damaged_manifest=False):
        with tempfile.TemporaryDirectory(prefix='usb rf cli ') as temp:
            root=Path(temp)
            shutil.copy2(Path(subject.__file__),root/'usb_rf_evidence.py')
            (root/'rf-test-manifest.json').write_text(json.dumps(dict(gui_sha256='fixture',runner_sha256='fixture',rade_sha256='fixture')))
            if damaged_manifest:
                (root/'rf-test-manifest.json').write_text('{damaged transfer')
            # Only the inventory adapter is synthetic. The actual published CLI,
            # argument parser, report writer and OS exit contract execute below.
            (root/'usb_headset_inventory.py').write_text(textwrap.dedent('''\
                import hashlib
                def collect(**kwargs): return {'status':'USB_DUPLEX_CANDIDATE'}
                def sha256(path): return hashlib.sha256(path.read_bytes()).hexdigest()
                def command(args, **kwargs):
                    return {'command':args,'exit_code':0,'stdout':'fixture only','stderr':'','timed_out':False,'output_truncated':False}
            '''))
            trace=Path('/tmp')/('freedv-usb-cli-'+uuid.uuid4().hex+'.jsonl')
            trace.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            output=root/'results'
            args=[sys.executable,str(root/'usb_rf_evidence.py'),'--output',str(output)]
            if invalid_sdr:
                wav=root/'invalid-rate.wav'
                with wave.open(str(wav),'wb') as w:
                    w.setparams((1,2,22050,0,'NONE','not compressed'));w.writeframes(b'\0\0'*22050)
                args+=['--sdr-wav',str(wav)]
            try:
                process=subprocess.run(args,capture_output=True,text=True,timeout=15)
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
