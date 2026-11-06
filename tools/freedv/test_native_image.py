#!/usr/bin/env python3
"""Synthetic image checks. No generated fixture is deployable firmware."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from native_image import ExtImage, extract_root, replace_root, root_partition, sha
from fetch_native_baseline import unpack_sdk
from verify_native_abi import same_sdk_build
from prepare_native_image import prepare
from build_voice_candidate import verify_usb_integration, USB_REQUIRED_COVERAGE, USB_CONTROLLER_CASES
from usb_test_campaign import Campaign, MATRIX_SCHEMA, sha as campaign_sha, INFRASTRUCTURE_PREREQUISITES, host_resource_configuration
from test_usb_campaign import infrastructure_fixture, capture_case_fixture, resource_fixture, SYNTHETIC_TRANSPORT_SHA


class UsbReleaseGateTests(unittest.TestCase):
    def evidence(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-usb-gate-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        identity = {'source_commit':'a'*40, 'source_tree_sha256':'b'*64, 'build_id':'synthetic-gate-unit',
                    'test_binary_sha256':'c'*64,'runner_sha256':'d'*64,'speech_sha256':'e'*64,
                    'modem_sha256':'f'*64,'fault_injector_sha256':'1'*64}
        identity['host_resource_configuration']=host_resource_configuration()
        identity['transport_library_sha256']=SYNTHETIC_TRANSPORT_SHA
        expected = list(USB_CONTROLLER_CASES)
        plan = {'profile':'qualification', 'repetitions':2, 'expected_cases':expected,
                'selected_cases':expected, 'debug_subset':False,
                'infrastructure_prerequisites':list(INFRASTRUCTURE_PREREQUISITES),
                'host_resource_configuration':identity['host_resource_configuration']}
        executions = [name+'@'+str(repeat+1) for repeat in range(2) for name in expected]
        cases = {}
        for name in expected:
            runs = []
            for repeat in (1,2):
                log = root/(name+'-'+str(repeat)+'.log')
                log.write_text('Synthetic metadata gate fixture; no controller executed.\n')
                case_root=root/(name+'-'+str(repeat));case_root.mkdir()
                case_record = case_root/'case-result.json'
                extra=capture_case_fixture(name,case_root)
                case_record.write_text(json.dumps({'synthetic':True,**extra})+'\n')
                runs.append({'status':'PASS', 'repeat':repeat, 'log_path':str(log),
                             'log_sha256':campaign_sha(log), 'case_record_path':str(case_record),
                             'case_record_sha256':campaign_sha(case_record),**extra})
            cases[name] = {'status':'PASS', 'runs':runs}
        matrix = {'schema':MATRIX_SCHEMA, 'status':'PASS', 'identity':identity, **identity,
                  'plan':plan, 'complete_matrix':True, 'skipped':[], 'not_executed':[],
                  'expected_executions':executions, 'executed':executions,
                  'coverage':sorted(USB_REQUIRED_COVERAGE),
                  'cases':cases,'host_resources':resource_fixture(root/'resources',identity),
                  'infrastructure_prerequisites':{'unexpected_runtime_failure':infrastructure_fixture(
                      root/'infrastructure',identity,identity['fault_injector_sha256'])}}
        campaign = Campaign(root/'history',identity,plan,root/'evidence')
        try:
            snapshot = campaign.finish(matrix)
        finally:
            campaign.close()
        matrix = {**matrix, 'campaign':snapshot, 'promotion_eligible':snapshot['promotion_eligible']}
        return {**identity, 'usb_integration': {'status':'PASS', 'contract_version':3,
                'evidence_cli':{'status':'PASS'}, 'matrix':matrix}}

    def test_old_pass_and_incomplete_or_failed_evaluation_cannot_release(self):
        with self.assertRaises(ValueError):
            verify_usb_integration({'usb_integration': {'status': 'PASS'}})
        for mutation in ('coverage', 'case', 'cli', 'infrastructure'):
            evidence = self.evidence()
            if mutation == 'coverage': evidence['usb_integration']['matrix']['coverage'].remove('rf_disabled')
            elif mutation == 'case': evidence['usb_integration']['matrix']['cases'][USB_CONTROLLER_CASES[0]]['status'] = 'SKIPPED'
            elif mutation == 'infrastructure': evidence['usb_integration']['matrix']['infrastructure_prerequisites']={}
            else: evidence['usb_integration']['evidence_cli']['status'] = 'FAIL'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_usb_integration(evidence)

    def test_executed_contract_acceptance_does_not_claim_hardware_qualification(self):
        evidence = self.evidence()
        verify_usb_integration(evidence)
        self.assertNotIn('hardware_qualified', evidence)


    def test_single_run_or_debug_campaign_cannot_promote(self):
        for field, value in (('repetitions',1), ('debug_subset',True), ('profile','regression')):
            evidence=self.evidence()
            evidence['usb_integration']['matrix']['plan'][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_usb_integration(evidence)

    def test_candidate_identity_and_retained_artifacts_must_match(self):
        for field in ('source_commit','source_tree_sha256','build_id'):
            evidence=self.evidence();evidence[field]='different'
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_usb_integration(evidence)
        evidence=self.evidence()
        run=evidence['usb_integration']['matrix']['cases'][USB_CONTROLLER_CASES[0]]['runs'][0]
        Path(run['log_path']).write_text('changed after qualification')
        with self.assertRaises(ValueError):verify_usb_integration(evidence)

    def test_boolean_pass_with_unresolved_history_cannot_promote(self):
        evidence=self.evidence()
        evidence['usb_integration']['matrix']['campaign']['blockers']=['prior-failure']
        with self.assertRaises(ValueError):verify_usb_integration(evidence)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='native-image-unit-')
        self.t=Path(self.directory.name)
        root=self.t/'root'; (root/'usr/sbin').mkdir(parents=True); (root/'etc').mkdir(); (root/'lib').mkdir()
        (root/'usr/lib').mkdir(); (root/'etc/sentinel').write_bytes(b'preserve configuration and signature enforcement')
        (root/'usr/share/x6100').mkdir(parents=True)
        (root/'usr/share/x6100/update.pub').write_bytes(b'synthetic unchanged signing key')
        (root/'usr/lib/python3.11/site-packages').mkdir(parents=True)
        (root/'usr/sbin/x6100_gui').write_bytes(b'old synthetic executable')
        (root/'lib/real.so').write_bytes(b'linked target')
        (root/'usr/lib/alias.so').symlink_to('../../lib/real.so')
        self.fs=self.t/'rootfs.ext4'
        with self.fs.open('wb') as f: f.truncate(16*1024*1024)
        subprocess.run(['mke2fs','-q','-t','ext4','-F','-d',str(root),str(self.fs)],check=True,capture_output=True)
        mbr=bytearray(512); mbr[510:]=b'\x55\xaa'
        struct.pack_into('<B3sB3sII',mbr,446,0x80,bytes(3),0x0c,bytes(3),2048,2048)
        struct.pack_into('<B3sB3sII',mbr,462,0,bytes(3),0x83,bytes(3),4096,self.fs.stat().st_size//512)
        self.boot=bytes(mbr)+b'B'*(2*1024*1024-512)
        self.image=self.t/'baseline.img'; self.image.write_bytes(self.boot+self.fs.read_bytes()+b'DATA-GEOMETRY-SENTINEL')

    def tearDown(self): self.directory.cleanup()

    def test_offline_replacement_preserves_source_boot_tail_and_other_files(self):
        original=sha(self.image); edited=self.t/'edited.ext4'; offset,size=extract_root(self.image,edited)
        image=ExtImage(edited,self.t/'scratch')
        self.assertEqual(image.dump('/usr/lib/alias.so').read_bytes(),b'linked target')
        source=self.t/'new'; source.write_bytes(b'new synthetic executable')
        image.write_file(source,'/usr/sbin/x6100_gui',0o755)
        self.assertEqual(image.dump('/usr/sbin/x6100_gui').read_bytes(),source.read_bytes())
        self.assertEqual(image.dump('/etc/sentinel').read_bytes(),b'preserve configuration and signature enforcement')
        image.mkdir('/opt/test/nested'); image.write_file(source,'/opt/test/nested/value',0o640)
        self.assertEqual([x[0] for x in image.children('/opt/test/nested')],['value'])
        image.remove_tree('/opt/test'); self.assertIsNone(image.info('/opt/test'))
        out=self.t/'candidate.img'; replace_root(self.image,edited,out,offset,size)
        self.assertEqual(out.read_bytes()[:len(self.boot)],self.boot)
        self.assertTrue(out.read_bytes().endswith(b'DATA-GEOMETRY-SENTINEL'))
        self.assertEqual(sha(self.image),original)
        subprocess.run(['e2fsck','-fn',str(edited)],check=True,capture_output=True)
        with self.assertRaises(FileExistsError): replace_root(self.image,edited,out,offset,size)

    def test_reject_device_symlink_and_bad_layout(self):
        link=self.t/'link'; link.symlink_to(self.image)
        with self.assertRaises(ValueError): root_partition(link)
        corrupt=self.t/'corrupt'; corrupt.write_bytes(b'not an SD card')
        with self.assertRaises(ValueError): root_partition(corrupt)
        with self.assertRaises(ValueError): root_partition(Path('/dev/null'))

    def test_diagnostic_image_does_not_infer_hardware_readiness(self):
        package=self.t/'package'; package.mkdir()
        (package/'package-manifest.json').write_text(json.dumps({'target':'x6200-buildroot','firmware_commit':'test-revision'}))
        (package/'SHA256SUMS').write_text(sha(package/'package-manifest.json')+'  package-manifest.json\n')
        overlay=self.t/'overlay'; (overlay/'usr/share/x6100').mkdir(parents=True)
        (overlay/'usr/share/x6100/update.pub').write_bytes(b'synthetic unchanged signing key')
        gui=self.t/'gui'; gui.write_bytes(b'synthetic replacement executable')
        # Exercise actual offline filesystem/metadata assembly while replacing
        # only ARM/ABI checks, which this synthetic image cannot satisfy.
        with patch('prepare_native_image.require_arm_elf'), patch('prepare_native_image.verify_abi',return_value={'status':'SYNTHETIC-TEST'}):
            record=prepare(self.image,gui,package,overlay,'diagnostic','test-revision',self.t,
                           self.t/'diagnostic.img',{'software_gates_passed':True})
        self.assertFalse(record['hardware_qualified'])
        self.assertIs(record['ready_for_guided_hardware_tests'],False)
        serialized=json.loads((self.t/'diagnostic.json').read_text())
        self.assertIs(serialized['ready_for_guided_hardware_tests'],False)

    def test_abi_accepts_debug_only_changes_and_rejects_code_changes(self):
        original=Path('/bin/true')
        marker=self.t/'marker'; marker.write_bytes(b'development debug metadata')
        debug=self.t/'with-debug'
        subprocess.run(['objcopy','--add-section','.debug_native_test='+str(marker),str(original),str(debug)],check=True)
        self.assertNotEqual(sha(original),sha(debug))
        identity=same_sdk_build('readelf',original,debug)
        self.assertIn('.text',identity['matching_allocated_section_dumps_sha256'])
        text=self.t/'text'
        subprocess.run(['objcopy','--dump-section','.text='+str(text),str(original),str(self.t/'discard')],check=True)
        data=bytearray(text.read_bytes()); data[0]^=1; text.write_bytes(data)
        changed=self.t/'changed'
        subprocess.run(['objcopy','--update-section','.text='+str(text),str(original),str(changed)],check=True)
        with self.assertRaises(ValueError): same_sdk_build('readelf',original,changed)

    def test_sdk_target_root_links_are_confined_and_host_absolute_links_refused(self):
        import io, tarfile
        archive=self.t/'target-root-link.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            payload=b'target-only program'
            entry=tarfile.TarInfo('sdk/arm-test/sysroot/bin/udevadm'); entry.size=len(payload); entry.mode=0o755
            tar.addfile(entry,io.BytesIO(payload))
            link=tarfile.TarInfo('sdk/arm-test/sysroot/sbin/udevadm'); link.type=tarfile.SYMTYPE; link.linkname='/bin/udevadm'
            tar.addfile(link)
        dest=self.t/'safe-sdk'; normalized=unpack_sdk(archive,dest)
        self.assertEqual(normalized[0]['original'],'/bin/udevadm')
        self.assertEqual((dest/'sdk/arm-test/sysroot/sbin/udevadm').read_bytes(),payload)
        self.assertEqual((dest/'sdk/arm-test/sysroot/sbin/udevadm').readlink(),Path('../bin/udevadm'))
        recorded=self.t/'recorded-host-prefix.tar.gz'
        with tarfile.open(recorded,'w:gz') as tar:
            for name,payload in [('sdk/share/buildroot/sdk-location',b'/old/build/host\n'),('sdk/bin/fw_printenv',b'verified SDK program')]:
                entry=tarfile.TarInfo(name); entry.size=len(payload); entry.mode=0o644
                tar.addfile(entry,io.BytesIO(payload))
            link=tarfile.TarInfo('sdk/bin/fw_setenv'); link.type=tarfile.SYMTYPE; link.linkname='/old/build/host/bin/fw_printenv'; tar.addfile(link)
        moved=self.t/'relocated-sdk'; rows=unpack_sdk(recorded,moved)
        self.assertEqual((moved/'sdk/bin/fw_setenv').read_bytes(),b'verified SDK program')
        self.assertEqual(rows[0]['relative_target'],'fw_printenv')
        bad=self.t/'host-absolute-link.tar.gz'
        with tarfile.open(bad,'w:gz') as tar:
            link=tarfile.TarInfo('sdk/bin/host-program'); link.type=tarfile.SYMTYPE; link.linkname='/bin/true'; tar.addfile(link)
        with self.assertRaises(ValueError): unpack_sdk(bad,self.t/'bad-sdk')

    def test_sdk_extract_rejects_link_escape(self):
        import tarfile
        archive=self.t/'bad.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            entry=tarfile.TarInfo('sdk/outside'); entry.type=tarfile.SYMTYPE; entry.linkname='../../outside'
            tar.addfile(entry)
        with self.assertRaises(ValueError): unpack_sdk(archive,self.t/'sdk')
        self.assertFalse((self.t/'outside').exists())


if __name__=='__main__': unittest.main()
