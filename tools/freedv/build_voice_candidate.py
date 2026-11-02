#!/usr/bin/env python3
"""Assemble a locally testable persistent-voice spare-card image, never flash RF hardware."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from build_native_images import current_overlay
from native_image import require_arm_elf,sha
from prepare_native_image import prepare

ROOT=Path(__file__).resolve().parents[2]
BASELINE_SHA='a11d8fa49156a8a9ada151e2472c1da1fde2115c22563b331270035f6b6285ab'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','sdk','gui','runner','rade','fixtures','web-wheel','verification','work','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--debugfs',default='debugfs');p.add_argument('--e2fsck',default='e2fsck');p.add_argument('--qemu',default='qemu-arm')
    a=p.parse_args()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
        raise ValueError('candidate source must be committed before image assembly')
    verification=json.loads(a.verification.read_text())
    if verification.get('status')!='PASS' or verification.get('host_tests',0)<30 or verification.get('persistent_turns',0)<100:
        raise ValueError('completed host suite and actual persistent-case verification are required')
    for name,expected in verification['source_files'].items():
        if sha(ROOT/name)!=expected:raise ValueError('verification source mismatch: '+name)
    if sha(a.baseline)!=BASELINE_SHA:raise ValueError('verified X6200 baseline image required')
    require_arm_elf(a.gui);require_arm_elf(a.runner);require_arm_elf(a.rade)
    work=a.work.resolve();work.mkdir(parents=True,exist_ok=False)
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    package=work/'package';(package/'bin').mkdir(parents=True);(package/'lib').mkdir();(package/'fixtures').mkdir()
    shutil.copy2(a.runner,package/'bin/freedv_voice_runner')
    shutil.copy2(a.rade,package/'lib/librade.so.0.1')
    for path in sorted(a.fixtures.glob('rade-*')):
        if path.is_file():shutil.copy2(path,package/'fixtures'/path.name)
    if not (package/'fixtures/rade-continuous.raw').is_file():raise ValueError('continuous diagnostic fixture missing')
    model=json.loads((ROOT/'tools/freedv/qualification/reference-sources.json').read_text())['model']['sha256']
    record={'firmware_commit':revision,'source_tree_sha256':verification['source_tree_sha256'],
            'software_gates_passed':True,'hardware_qualified':False,'ready_for_guided_hardware_tests':True,
            'candidate_kind':'persistent voice diagnostic spare-card image',
            'normal_mode_qualified':False,'host_verification_sha256':sha(a.verification),
            'gui_sha256':sha(a.gui),'runner_sha256':sha(a.runner),'rade_sha256':sha(a.rade),
            'web_wheel_sha256':sha(a.web_wheel),'model_sha256':model,
            'physical_hand_ptt_release':'UNSUPPORTED by pinned documented API; never inferred from aggregate TX'}
    metadata={**record,'target':'x6200-buildroot','sources':json.loads((ROOT/'tools/freedv/sources.json').read_text())}
    (package/'package-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    (package/'SHA256SUMS').write_text(''.join(sha(path)+'  '+path.relative_to(package).as_posix()+'\n'
        for path in sorted(package.rglob('*')) if path.is_file()))
    overlay=current_overlay(work,'0.3.6',a.web_wheel)
    data=overlay/'usr/share/x6100/freedv';data.mkdir(parents=True,exist_ok=True)
    shutil.copytree(package/'fixtures',data/'fixtures',dirs_exist_ok=True)
    (data/'build-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    image=out/'x6200-persistent-voice-test.img'
    result=prepare(a.baseline,a.gui,package,overlay,'diagnostic',revision,a.sdk,image,record,a.debugfs,a.e2fsck,a.qemu)
    shutil.copy2(a.verification,out/'software-verification.json')
    (out/'candidate-manifest.json').write_text(json.dumps({**record,'image_sha256':result['image_sha256'],
        'signature_key_preserved':result['signature_key_preserved'],'actual_image_abi':result['abi']['status']},indent=2)+'\n')
    guide=out/'guide/docs/freedv';shutil.copytree(ROOT/'docs/freedv',guide)
    (out/'START-HERE.md').write_text('# Persistent FreeDV test candidate\n\n'
        'Write `x6200-persistent-voice-test.img.xz` to a separate spare card with verification. '
        'Preserve the working .2 card. Boot with PTT released and a dummy load.\n\n'
        'Open APP → Modes → FreeDV test. Follow [the test instructions](guide/docs/freedv/candidate-test.md). '
        'The new persistent TX/RX cases exercise the actual production adapter. '
        'This is a hardware-test candidate, not a qualified release.\n')
    subprocess.run([sys.executable,str(ROOT/'tools/freedv/build_native_test_package.py'),
        '--output',str(out/'native-voice-test-kit.zip')],check=True)
    with (out/'candidate-source.tar').open('xb') as archive:
        subprocess.run(['git','archive','--format=tar','--prefix=OpenX6X00/','HEAD'],cwd=ROOT,stdout=archive,check=True)
    subprocess.run(['git','bundle','create',str(out/'candidate-source.bundle'),
        '21df019884da3e8cdc1b4521a4b4f8a51ca210e1..HEAD'],cwd=ROOT,check=True)
    subprocess.run(['xz','-T2','-6',str(out/'candidate-source.tar')],check=True)
    subprocess.run(['xz','-T2','-6','--keep',str(image)],check=True)
    shutil.move(image,work/'validated-candidate.img')
    (out/'SHA256SUMS').write_text(''.join(sha(path)+'  '+path.relative_to(out).as_posix()+'\n'
        for path in sorted(out.rglob('*')) if path.is_file()))
    print('PASS: actual-image ABI, loader, filesystem, signing-key and root-partition preservation checks; candidate:',out)


if __name__=='__main__':main()
