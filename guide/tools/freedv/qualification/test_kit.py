#!/usr/bin/env python3
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch
import kit
import analysis
import reference
import zipfile
import shutil
import sys
import os
import importlib.util
import telemetry


class AnalysisTests(unittest.TestCase):
    def test_telemetry_large_partial_and_incomplete_reads(self):
        payload=(json.dumps({'t':'freedv_stats','available':True,'counter':2**60,'data':'x'*5000})+'\n').encode()
        class Socket:
            def __init__(self,parts):self.parts=list(parts)
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def settimeout(self,*args):pass
            def connect(self,*args):pass
            def sendall(self,data):self.sent=data
            def recv(self,n):return self.parts.pop(0) if self.parts else b''
        with patch.object(telemetry.socket,'socket',return_value=Socket([payload[:1000],payload[1000:]])):
            self.assertEqual(telemetry.rpc('freedv_stats')['counter'],2**60)
        with patch.object(telemetry.socket,'socket',return_value=Socket([b'{"t":"freedv","state":0}\n'])):
            self.assertEqual(telemetry.rpc('freedv_get')['state'],0)
        with patch.object(telemetry.socket,'socket',return_value=Socket([b'{"t":"wrong"}\n'])):
            with self.assertRaises(ValueError):telemetry.rpc('freedv_get')
        with patch.object(telemetry.socket,'socket',return_value=Socket([payload[:1000]])):
            with self.assertRaises(ValueError):telemetry.rpc('freedv_stats')
        with self.assertRaises(ValueError):telemetry.rpc('tx')
    def test_known_gaps_delays_clipping_discontinuity(self):
        original = list(range(1, 101))
        self.assertEqual(analysis.compare_known(original, [0]*7+original, 10)['delay_samples'], 7)
        self.assertEqual(analysis.compare_known(original, [0]*7+original, 10)['status'], 'PASS')
        for broken in (original[:40]+original[42:], original[:40]+[30000]+original[41:], original[:90]):
            self.assertEqual(analysis.compare_known(original, broken, 10)['status'], 'FAIL')
        a = analysis.accounting([0, 32767, -32768, 1], 8000, 6)
        self.assertEqual(a['clipped_samples'], 2)
        self.assertEqual(a['sample_difference'], -2)
        self.assertEqual(a['maximum_step'], 65535)
        self.assertEqual(a['intelligibility'], 'UNASSESSED')

    def test_distinct_markers(self):
        rf = [1, -1, 1, -1]
        speaker = [1, 1, -1, -1]
        raw = [2*a+3*b for a, b in zip(rf, speaker)]
        self.assertEqual(analysis.marker_projection(raw, rf)['gain'], 2)
        self.assertEqual(analysis.marker_projection(raw, speaker)['gain'], 3)

    def test_timing_upper_bound(self):
        m = {k: 'recorded' for k in ('event_definition', 'recording_delays', 'resolution', 'uncertainty_basis',
             'setup_validation_sha256', 'event_artifact_sha256', 'rf_artifact_sha256')}
        m.update(method='correlated_video_iq', interval_ms=480, uncertainty_ms=20, physically_held_ptt=False, uncertainty_components_ms={'frame': 10, 'alignment': 10})
        self.assertEqual(kit.timing_assessment(m)[0], 'PASS')
        m['uncertainty_ms'] = 21
        self.assertEqual(kit.timing_assessment(m)[0], 'FAIL')
        m['uncertainty_ms'] = float('nan')
        self.assertEqual(kit.timing_assessment(m)[0], 'INCONCLUSIVE')
        m['method'] = 'software_command_timestamps'
        self.assertEqual(kit.timing_assessment(m)[0], 'INCONCLUSIVE')


class SourceTests(unittest.TestCase):
    def test_executable_modes_and_source_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); archive = root/'source.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                i = zipfile.ZipInfo('upstream/autogen.sh'); i.external_attr = 0o100755 << 16
                z.writestr(i, '#!/bin/sh\nexit 0\n')
            files = reference.extract(archive, root/'source')
            self.assertEqual((root/'source/autogen.sh').stat().st_mode & 0o777, 0o755)
            reference.verify_tree(root/'source', files)
            (root/'source/autogen.sh').write_text('changed')
            with self.assertRaises(ValueError): reference.verify_tree(root/'source', files)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'runner'; self.binary.write_text('fixture executable')
        self.build = {'build_id': 'rehearsal', 'source_commit': 'a'*40, 'source_tree_sha256': 'b'*64,
                      'binaries': {str(self.binary): kit.sha(self.binary)}}
        self.profile = {'id': 'rehearsal-usb-dig', 'local_mode': 'USB-DIG', 'upper_sideband': True}
        self.pre = {**kit.identity(self.build, self.profile), 'schema_version': 1, 'family': 'gui',
                    'status': 'PASS', 'catalog_sha256': kit.sha(kit.CATALOG), 'kit_sha256': kit.sha(kit.__file__),
                    'binaries': self.build['binaries']}
        self.bundle = self.root / 'bundle'; self.bundle.mkdir()
        self.case = kit.cases()['native-to-gui-8000-cold']
        self.run = {**kit.identity(self.build, self.profile), 'case_id': self.case['id'],
                    'preflight': self.pre, 'complete': True, 'status': 'PASS', 'measurements': {}, 'artifacts': []}
        for key in self.case['measurements']:
            self.run['measurements'][key] = 'operator measurement'
        for role in self.case['recordings']:
            if role in ('original_speech', 'decoded_speech', 'modem_output'):
                p = self.bundle / (role+'.wav')
                with wave.open(str(p), 'wb') as f:
                    f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed')); f.writeframes(b'\x01\x00'*80)
            else:
                p = self.bundle / (role+'.txt'); p.write_text(role)
            self.run['artifacts'].append(kit.artifact(p, role))
        self.run['operator_assessment'] = {'result': 'PASS', 'reviewer': 'rehearsal', 'date': '2026-11-01', 'findings': 'rehearsal'}
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        kit.write(self.bundle / 'run.json', self.run); kit.write(self.bundle / 'preflight.json', self.pre)

    def assess(self):
        return kit.assess(self.bundle, self.build, self.profile)

    def test_listening_remains_unassessed(self):
        self.assertEqual(self.assess()['status'], 'INCONCLUSIVE')
        a = self.run['operator_assessment']
        a.update(intelligible_phrases=['turn one'], first_words=['turn'], last_words=['one'],
                 speech_artifact_sha256=next(x['sha256'] for x in self.run['artifacts'] if x['role']=='decoded_speech'))
        self.save()
        self.assertEqual(self.assess()['status'], 'PASS')  # operator result, fixture only
        a['speech_artifact_sha256'] = 'x'*64; self.save()
        self.assertEqual(self.assess()['status'], 'INCONCLUSIVE')

    def test_mismatched_build_profile(self):
        for key in ('build_sha256', 'profile_sha256', 'build_id', 'profile_id'):
            run = copy.deepcopy(self.run); self.run[key] = 'other'; self.save()
            with self.assertRaises(ValueError): self.assess()
            self.run = run

    def test_incomplete_missing_mismatched_recordings(self):
        self.run['complete'] = False; self.save(); self.assertEqual(self.assess()['status'], 'INCONCLUSIVE')
        self.run['complete'] = True
        self.run['artifacts'].pop(); self.save(); self.assertEqual(self.assess()['status'], 'INCONCLUSIVE')
        self.run['artifacts'][0]['sha256'] = 'a'*64; self.save(); self.assertEqual(self.assess()['status'], 'INCONCLUSIVE')

    def test_preflight_blocked_never_passes(self):
        self.run['preflight']['status'] = 'BLOCKED'; self.save(); self.assertEqual(self.assess()['status'], 'BLOCKED')

    def test_path_escape(self):
        self.run['artifacts'][0]['path'] = '../../outside'; self.save()
        with self.assertRaises(ValueError): self.assess()

    def test_rehearsal_entire_collection(self):
        (self.bundle / 'unlisted-private-file').write_text('must not copy')
        reports = kit.collect([self.bundle], self.build, self.profile, self.root / 'collection')
        self.assertEqual(reports[0]['status'], 'INCONCLUSIVE')
        self.assertFalse((self.root / 'collection' / self.case['id'] / 'unlisted-private-file').exists())
        self.assertTrue((self.root / 'collection' / 'results.md').is_file())

    def test_runner_rejects_other_build(self):
        self.pre['family']='engine'
        self.pre['binaries'][str(self.binary)] = 'a'*64
        with self.assertRaises(ValueError): kit.run('engine-synthetic', self.pre, self.root, self.root / 'bad-run')

    def test_real_run_collect_assess(self):
        self.pre['family']='engine'
        (self.root / 'CTestTestfile.cmake').write_text('add_test(rehearsal "'+self.binary.as_posix()+'")\n')
        self.binary.write_text('#!/bin/sh\nexit 0\n'); self.binary.chmod(0o755)
        self.pre['binaries'][str(self.binary)] = kit.sha(self.binary)
        self.pre.update(kit.identity(self.build, self.profile))
        # Use a real CTest run, with the temporary catalog case's exact regex.
        case = copy.deepcopy(kit.cases()['engine-synthetic']); case['ctest'] = '^rehearsal$'
        with patch.object(kit, 'cases', return_value={'engine-synthetic': case}):
            result = kit.run('engine-synthetic', self.pre, self.root, self.root / 'actual-run')
            self.assertEqual(result['automated_exit'], 0)
            results = kit.collect([self.root / 'actual-run'], self.build, self.profile, self.root / 'actual-collection')
            self.assertEqual(results[0]['status'], 'PASS')
            self.assertEqual(results[0]['scope'], 'development')

    def test_preflight_case_and_file_binding(self):
        self.pre['selected_cases']=['another-case'];self.save()
        with self.assertRaises(ValueError):self.assess()
        self.pre.pop('selected_cases');self.save()
        kit.write(self.bundle/'preflight.json',{'status':'PASS'})
        with self.assertRaises(ValueError):self.assess()

    def test_numbered_recordings_cannot_be_a_single_nonzero_output(self):
        self.run['case_id']='engine-numbered-turns';self.pre['family']='engine'
        case=kit.cases()['engine-numbered-turns']
        self.run['measurements']={k:'unmeasured' for k in case['measurements']}
        # Supply all role names and a general listening result; without per-turn
        # recordings this still cannot pass the hundred-turn acceptance gate.
        for role in ('engine_stats',):
            path=self.bundle/(role+'.txt');path.write_text('fixture');self.run['artifacts'].append(kit.artifact(path,role))
        self.run['operator_assessment'].update(intelligible_phrases=['phrase'],first_words=['first'],last_words=['last'],
            speech_artifact_sha256=next(a['sha256'] for a in self.run['artifacts'] if a['role']=='decoded_speech'))
        self.save();self.assertEqual(self.assess()['status'],'INCONCLUSIVE')
        self.run['measurements'].update(turn_count=99,turn_identities=list(range(99)));self.save()
        self.assertEqual(self.assess()['status'],'FAIL')

    def test_selected_preflight_and_pinned_inputs(self):
        validation=self.root/'validated.json';kit.write(validation,{'status':'PASS'})
        inventory={'equipment':{'reference_computer':{'verified':True},'pinned_codecs_models':{'verified':True}},
            'recording_format_verified':True,'tools_validation':{'path':str(validation),'sha256':kit.sha(validation)}}
        case=copy.deepcopy(kit.cases()['engine-synthetic']);case['tools']=['python3']
        other=copy.deepcopy(case);other['id']='needs-hardware';other['equipment']=['x6200']
        lock={'python3':kit.sha(shutil.which('python3'))}
        pins={}
        for name in ('rade','opus','model'):
            path=self.root/(name+'.archive');path.write_text('fixture '+name)
            pins[name]={'sha256':kit.sha(path)}
        pinfile=self.root/'pins.json';kit.write(pinfile,pins)
        with patch.object(kit,'cases',return_value={case['id']:case,other['id']:other}),patch.object(kit,'REFERENCE',pinfile):
            report=kit.preflight('engine',self.build,self.profile,inventory,lock,[case['id']])
            self.assertEqual(report['status'],'BLOCKED');self.assertTrue(any('pinned source' in s for s in report['missing']))
            inventory['inputs']=[{'path':str(self.root/(n+'.archive')),'sha256':p['sha256']} for n,p in pins.items()]
            report=kit.preflight('engine',self.build,self.profile,inventory,lock,[case['id']])
            self.assertEqual(report['status'],'PASS');self.assertEqual(report['selected_cases'],[case['id']])
            self.assertEqual(kit.preflight('engine',self.build,self.profile,inventory,lock)['status'],'BLOCKED')
            with self.assertRaises(ValueError):kit.preflight('engine',self.build,self.profile,inventory,lock,['not-in-family'])


@unittest.skipUnless(os.name=='posix','Linux webserver lease')
class WebLeaseTests(unittest.TestCase):
    def test_web_and_native_lock_orders_and_failed_admission_lifetime(self):
        import fcntl
        spec=importlib.util.spec_from_file_location('radio_lease',kit.ROOT/'webserver/src/x6100_webserver/radio_lease.py')
        lease=importlib.util.module_from_spec(spec);spec.loader.exec_module(lease)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lease';web=lease.acquire(path)
            native=os.open(path,os.O_RDONLY)
            with self.assertRaises(BlockingIOError):fcntl.flock(native,fcntl.LOCK_EX|fcntl.LOCK_NB)
            lease.release(web);fcntl.flock(native,fcntl.LOCK_EX|fcntl.LOCK_NB)
            before=len(list(Path('/proc/self/fd').iterdir()))
            for _ in range(100):
                with self.assertRaises(BlockingIOError):lease.acquire(path)
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)
            os.close(native);lease.release(lease.acquire(path))


if __name__ == '__main__':
    unittest.main()
