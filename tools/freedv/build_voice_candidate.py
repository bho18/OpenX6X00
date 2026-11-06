#!/usr/bin/env python3
"""Assemble a locally testable persistent-voice spare-card image, never flash RF hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from build_native_images import current_overlay
from native_image import require_arm_elf,sha
from prepare_native_image import prepare
from usb_test_campaign import validate_campaign
from test_usb_audio_integration import CASES as USB_CONTROLLER_CASES
from build_native_development import validate_build_record

ROOT=Path(__file__).resolve().parents[2]
BASELINE_SHA='a11d8fa49156a8a9ada151e2472c1da1fde2115c22563b331270035f6b6285ab'
USB_REQUIRED_COVERAGE = {
    'prekey_audio', 'blocking_control', 'slow_pa_ack', 'direction_health',
    'rf_disabled', 'retained_release', 'stop_priority', 'device_loss',
    'sink_decode', 'hardware_rates', 'unsynchronized_rx',
    'capture_reacquisition', 'preparation_sample_accounting',
    'independent_telemetry', 'phase_targeting', 'per_turn_sink',
    'many_turns', 'session_reopen', 'cold_idle', 'drain_rf_ownership',
    'full_lifecycle', 'source_continuity', 'collector_contract',
    'negotiated_buffers', 'service_jitter', 'receive_sink', 'short_payload',
    'tail_backpressure',
    'infrastructure_failure_preservation',
    'capture_close', 'source_boundary',
}


def verify_usb_integration(verification):
    """Only the complete, source-bound repeated campaign can promote an image."""
    evidence = verification.get('usb_integration', {})
    matrix = evidence.get('matrix', {})
    if (evidence.get('status') != 'PASS' or evidence.get('contract_version') != 3 or
            evidence.get('evidence_cli', {}).get('status') != 'PASS' or
            matrix.get('status') != 'PASS' or
            matrix.get('complete_matrix') is not True or matrix.get('skipped') != [] or
            not USB_REQUIRED_COVERAGE <= set(matrix.get('coverage', []))):
        raise ValueError('USB RF image requires session-contract v3 qualification and actual CLI checks')
    cases = matrix.get('cases', {})
    if not cases or any(case.get('status') != 'PASS' for case in cases.values()):
        raise ValueError('USB integration cases must all execute and pass')
    validate_campaign(matrix, expected_cases=USB_CONTROLLER_CASES)
    for field in ('source_commit', 'source_tree_sha256', 'build_id'):
        if not verification.get(field) or matrix.get(field) != verification[field]:
            raise ValueError('USB campaign does not match candidate ' + field)


def verify_usb_builds(verification):
    """A latest PASS cannot hide a failed prerequisite build or changed inputs."""
    expected = {'host':'host', 'host-native':'host-native-validation',
                'arm':'x6200-diagnostic', 'normal':'x6200-normal', 'x6100':'x6100'}
    records = verification.get('build_records', {})
    if set(records) != set(expected):
        raise ValueError('USB candidate requires all five sealed prerequisite build records')
    for name, variant in expected.items():
        record = validate_build_record(records[name])
        if record.get('variant') != variant:
            raise ValueError('Wrong prerequisite build variant: ' + name)
        for field in ('source_commit', 'source_tree_sha256', 'build_id'):
            if record.get(field) != verification.get(field):
                raise ValueError('Prerequisite build does not match candidate ' + field + ': ' + name)


def verify_source_manifest(repository, files, tree_sha256):
    """Bind the recorded file hashes to the complete, clean committed source."""
    repository = Path(repository).resolve()
    if not isinstance(files, dict) or not all(isinstance(name, str) for name in files):
        raise ValueError('verification source_files must be a file-hash mapping')
    entries = subprocess.check_output(
        ['git', 'ls-tree', '-rz', '--full-tree', 'HEAD'], cwd=repository
    ).decode().split('\0')
    committed = set()
    for entry in entries:
        if not entry:
            continue
        metadata, name = entry.split('\t', 1)
        mode, kind, _ = metadata.split()
        if mode == '160000' and kind == 'commit':
            continue  # Submodules have separately pinned identities.
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('non-regular committed source is unsupported: ' + name)
        committed.add(name)
    if set(files) != committed:
        raise ValueError('verification source manifest must contain every committed regular file and no extras')
    if subprocess.check_output(
            ['git', 'status', '--porcelain', '--untracked-files=no'], cwd=repository, text=True).strip():
        raise ValueError('verification source must match the clean committed tree')
    for name, expected in files.items():
        if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
            raise ValueError('invalid verification source hash: ' + name)
        if sha(repository / name) != expected:
            raise ValueError('verification source mismatch: ' + name)
    digest = hashlib.sha256(''.join(files[name] + '  ' + name + '\n'
                                    for name in sorted(files)).encode()).hexdigest()
    if tree_sha256 != digest:
        raise ValueError('verification source tree digest mismatch')


def create_source_bundle(repository, bundle, revision):
    """Verify a full reachable-history bundle without borrowing local objects."""
    repository, bundle = Path(repository).resolve(), Path(bundle).resolve()
    if bundle.exists():
        raise FileExistsError(bundle)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
    if actual != revision:
        raise ValueError('source HEAD changed before bundle creation')
    subprocess.run(['git', 'bundle', 'create', str(bundle), 'HEAD'], cwd=repository, check=True)
    with tempfile.TemporaryDirectory(prefix='verify-source-bundle-', dir=bundle.parent) as directory:
        subprocess.run(['git', 'init', '--bare', directory], check=True, capture_output=True)
        subprocess.run(['git', '-C', directory, 'bundle', 'verify', str(bundle)],
                       check=True, capture_output=True)
    heads = subprocess.check_output(['git', 'bundle', 'list-heads', str(bundle)], text=True).splitlines()
    if heads != [revision + ' HEAD']:
        raise ValueError('source bundle does not identify the verified HEAD')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','sdk','gui','runner','rade','fixtures','web-wheel','verification','work','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--debugfs',default='debugfs');p.add_argument('--e2fsck',default='e2fsck');p.add_argument('--qemu',default='qemu-arm')
    p.add_argument('--pulse-probe',type=Path)
    p.add_argument('--usb-rf',action='store_true',help='Separate USB headset RF development candidate; native guard stays blocked')
    a=p.parse_args()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
        raise ValueError('candidate source must be committed before image assembly')
    verification=json.loads(a.verification.read_text())
    if a.usb_rf:
        verify_usb_integration(verification)
        verify_usb_builds(verification)
    if verification.get('status')!='PASS' or verification.get('source_commit')!=revision:
        raise ValueError('passing verification for this exact source revision is required')
    # Route and collection corrections need relevant regression/build checks.
    # An inherited endurance result retains its original source identity; a
    # counter of 100 is neither a microphone-route prerequisite nor evidence
    # that this candidate ran that experiment.
    if not verification.get('host_tests') or any(
            verification.get('target_builds',{}).get(name)!='PASS'
            for name in ('x6200_diagnostic','x6200_normal','x6100_full_application')):
        raise ValueError('host regressions and all supported application builds are required')
    smoke=verification.get('arm_runner',{})
    if smoke.get('status')!='PASS' or smoke.get('stats',{}).get('modem_blocks',0)<1 or smoke['stats'].get('failures',1):
        raise ValueError('ARM runner must execute real processing; zero-work success is not valid evidence')
    verify_source_manifest(ROOT,verification.get('source_files'),verification.get('source_tree_sha256'))
    for name,path in (('sdk',a.gui),('runner',a.runner),('pulse_probe',a.pulse_probe),('rade',a.rade)):
        if path and verification.get('binaries',{}).get(name)!=sha(path):
            raise ValueError('verification binary mismatch: '+name)
    if smoke.get('rade_sha256') != sha(a.rade):
        raise ValueError('packaged RADE must match the library used by the executed ARM runner')
    if smoke.get('runner_sha256') != sha(a.runner):
        raise ValueError('packaged runner must match the executed ARM runner')
    if sha(a.baseline)!=BASELINE_SHA:raise ValueError('verified X6200 baseline image required')
    require_arm_elf(a.gui);require_arm_elf(a.runner);require_arm_elf(a.rade)
    work=a.work.resolve();work.mkdir(parents=True,exist_ok=False)
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    package=work/'package';(package/'bin').mkdir(parents=True);(package/'lib').mkdir();(package/'fixtures').mkdir()
    shutil.copy2(a.runner,package/'bin/freedv_voice_runner')
    if a.pulse_probe:
        require_arm_elf(a.pulse_probe)
        shutil.copy2(a.pulse_probe,package/'bin/freedv_voice_pulse_probe')
    shutil.copy2(a.rade,package/'lib/librade.so.0.1')
    for path in sorted(a.fixtures.glob('rade-*')):
        if path.is_file():shutil.copy2(path,package/'fixtures'/path.name)
    if not (package/'fixtures/rade-continuous.raw').is_file():raise ValueError('continuous diagnostic fixture missing')
    model=json.loads((ROOT/'tools/freedv/qualification/reference-sources.json').read_text())['model']['sha256']
    record={'firmware_commit':revision,'source_tree_sha256':verification['source_tree_sha256'],
            'build_id':verification.get('build_id','unqualified-development'),
            'software_gates_passed':True,'hardware_qualified':False,'ready_for_guided_hardware_tests':False,
            'candidate_kind':'report-informed native half-duplex development spare-card image',
            'hardware_status':'BLOCKED',
            'validation_scope':'Software and image integrity passed; native RX/TX remains blocked by the incomplete live hardware audio/PTT contract',
            'legacy_route_gain_experiments':'WITHDRAWN on X6200',
            'receive_only_mapping_ui':False,
            'normal_mode_qualified':False,'host_verification_sha256':sha(a.verification),
            'gui_sha256':sha(a.gui),'runner_sha256':sha(a.runner),'rade_sha256':sha(a.rade),
            'modem_fixture_sha256':sha(package/'fixtures/rade-continuous.raw'),
            'web_wheel_sha256':sha(a.web_wheel),'model_sha256':model,
            'physical_hand_ptt_release':'UNSUPPORTED by pinned documented API; never inferred from aggregate TX'}
    if a.usb_rf:
        record.update(candidate_kind='USB headset / panel PTT RF development image',
                      hardware_status='RF_DISABLED_EVALUATION_REQUIRED',ready_for_guided_hardware_tests=True,
                      usb_rf_test_ready=True,native_mode_status='BLOCKED: unchanged native MCU contract guard',
                      validation_scope='Session-contract v3 repeated software campaign passed; real-device cold-idle evaluation precedes local RF arming and the SDR trial',
                      usb_startup_contract_version=3,on_device_readiness_required=True,
                      rf_arm_policy='Local only, after the shared-core RF-disabled audio evaluation passes; panel PTT only; requested 1 W',
                      stock_baseband_lease_available=False,physical_rf_off_timing='UNMEASURED')
    if a.pulse_probe:record['pulse_probe_sha256']=sha(a.pulse_probe)
    metadata={**record,'target':'x6200-buildroot','sources':json.loads((ROOT/'tools/freedv/sources.json').read_text())}
    (package/'package-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    (package/'SHA256SUMS').write_text(''.join(sha(path)+'  '+path.relative_to(package).as_posix()+'\n'
        for path in sorted(package.rglob('*')) if path.is_file()))
    overlay=current_overlay(work,'0.3.6',a.web_wheel)
    data=overlay/'usr/share/x6100/freedv';data.mkdir(parents=True,exist_ok=True)
    shutil.copytree(package/'fixtures',data/'fixtures',dirs_exist_ok=True)
    (data/'build-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    image=out/'x6200-native-development.img'
    result=prepare(a.baseline,a.gui,package,overlay,'diagnostic',revision,a.sdk,image,record,a.debugfs,a.e2fsck,a.qemu)
    shutil.copy2(a.verification,out/'software-verification.json')
    (out/'candidate-manifest.json').write_text(json.dumps({**record,'image_sha256':result['image_sha256'],
        'signature_key_preserved':result['signature_key_preserved'],'actual_image_abi':result['abi']['status']},indent=2)+'\n')
    guide=out/'guide/docs/freedv';shutil.copytree(ROOT/'docs/freedv',guide)
    start=(ROOT/('docs/freedv/usb-rf-end-to-end.md' if a.usb_rf else 'docs/freedv/native-development-build.md')).read_text()
    start=start.replace('(native-half-duplex-qualification.md)',
                        '(guide/docs/freedv/native-half-duplex-qualification.md)')
    start=start.replace('(baseband-research/README.md)',
                        '(guide/docs/freedv/baseband-research/README.md)')
    (out/'START-HERE.md').write_text(start)
    if a.usb_rf:
        subprocess.run([sys.executable,str(ROOT/'tools/freedv/build_usb_rf_kit.py'),
            '--image-manifest',str(out/'candidate-manifest.json'),
            '--output',str(out/'usb-rf-test-kit.zip')],check=True)
    else:
        subprocess.run([sys.executable,str(ROOT/'tools/freedv/build_native_test_package.py'),
            '--output',str(out/'native-voice-test-kit.zip')],check=True)
    with (out/'candidate-source.tar').open('xb') as archive:
        subprocess.run(['git','archive','--format=tar','--prefix=OpenX6X00/','HEAD'],cwd=ROOT,stdout=archive,check=True)
    create_source_bundle(ROOT,out/'candidate-source.bundle',revision)
    subprocess.run(['xz','-T2','-6',str(out/'candidate-source.tar')],check=True)
    subprocess.run(['xz','-T2','-6','--keep',str(image)],check=True)
    shutil.move(image,work/'validated-candidate.img')
    (out/'SHA256SUMS').write_text(''.join(sha(path)+'  '+path.relative_to(out).as_posix()+'\n'
        for path in sorted(out.rglob('*')) if path.is_file()))
    print('PASS: actual-image ABI, loader, filesystem, signing-key and root-partition preservation checks; candidate:',out)


if __name__=='__main__':main()
