#!/usr/bin/env python3
"""Build normal/diagnostic v0.3.6 images from one source and matching SDK.

Consumes completed host, codec and SDK correctness gates; edits only disposable
rootfs copies. No flashing, RF control, baseband updates or release publishing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZipFile
from fetch_native_baseline import BUILDROOT, BUILDROOT_SUBMODULE, ARTIFACT_SHA256
from native_image import sha
from prepare_native_image import prepare

ROOT=Path(__file__).resolve().parents[2]
BASELINE_SOURCE='fd7f0bba70c0c8f8854219b6422600cbd428a6dc'
APP_BASELINE='91f3b5e03f8cf7bffc58a1c44eaef6221be21631'


def run(*args,**kwargs):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=True,**kwargs)


def copy_files(source,destination):
    for path in sorted(source.rglob('*')):
        if path.is_symlink(): raise ValueError('source overlay symlink requires explicit handling: '+str(path))
        if path.is_file() and '__pycache__' not in path.parts:
            target=destination/path.relative_to(source); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)


def current_overlay(work,version):
    overlay=work/'runtime-overlay'; overlay.mkdir()
    copy_files(ROOT/'rootfs',overlay)
    copy_files(ROOT/'images',overlay/'usr/share/x6100/images')
    destination=overlay/'usr/share/x6100'; destination.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'third-party/cty/cty.dat',destination/'cty.dat')
    shutil.copy2(ROOT/'boot/logo.png',destination/'bootlogo.png')
    (destination/'app-version').write_text(version+'\n')
    # Rootfs package requirements and boot/kernel sources must be identical to
    # the verified baseline for this offline derivation. The v0.3.6 additions in
    # local-build.sh are X6100-only U-Boot or informational upstream reporting.
    changed=subprocess.check_output(['git','diff','--name-only',BASELINE_SOURCE,'HEAD','--','buildroot'],cwd=ROOT,text=True).splitlines()
    for name in changed:
        if name!='buildroot/local-build.sh' and not name.startswith('buildroot/br2_external/board/X6100/u-boot/'):
            raise ValueError('new image/kernel dependency requires a full pinned Buildroot rebuild: '+name)
    expected=subprocess.check_output(['git','show',BASELINE_SOURCE+':rootfs/usr/share/x6100/image-required'],cwd=ROOT,text=True)
    old=next(line for line in expected.splitlines() if line.startswith('x6200 ')).split()[1]
    new=next(line for line in (ROOT/'rootfs/usr/share/x6100/image-required').read_text().splitlines() if line.startswith('x6200 ')).split()[1]
    if old!=new: raise ValueError('X6200 image level changed; full baseline rebuild required')
    (overlay/'etc/openx6x00-image').write_text('model=x6200\nlevel='+new+'\nversion='+version+'\n')
    old_web_project=subprocess.check_output(['git','show',BASELINE_SOURCE+':webserver/pyproject.toml'],cwd=ROOT,text=True)
    if old_web_project!=(ROOT/'webserver/pyproject.toml').read_text():
        raise ValueError('webserver package requirements changed; review baseline runtime dependencies explicitly')
    wheels=work/'wheels'; wheels.mkdir()
    run('python3','-m','pip','wheel','--no-deps','--wheel-dir',wheels,ROOT/'webserver')
    wheel=list(wheels.glob('x6100_webserver-*.whl'))
    if len(wheel)!=1: raise ValueError('one current webserver wheel required')
    site=overlay/'usr/lib/python3.11/site-packages'; site.mkdir(parents=True)
    with ZipFile(wheel[0]) as archive:
        for member in archive.infolist():
            if Path(member.filename).is_absolute() or '..' in Path(member.filename).parts or member.filename.endswith('/'):
                if member.filename.endswith('/'): continue
                raise ValueError('invalid webserver wheel path')
            target=site/member.filename; target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(member) as src,target.open('xb') as dst: shutil.copyfileobj(src,dst)
    script=overlay/'usr/bin/x6100_webserver'; script.parent.mkdir(parents=True,exist_ok=True)
    script.write_text('#!/usr/bin/python3\nimport runpy\nrunpy.run_module("x6100_webserver",run_name="__main__")\n'); script.chmod(0o755)
    return overlay


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('recovery','benchmark-work','host-gates','host-codec-gates','codec-gates','work','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--debugfs',default='debugfs'); parser.add_argument('--e2fsck',default='e2fsck'); parser.add_argument('--qemu',default='qemu-arm')
    a=parser.parse_args()
    for name in ('CPATH','LIBRARY_PATH','PKG_CONFIG_PATH','CMAKE_PREFIX_PATH'):
        os.environ.pop(name,None)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
        raise ValueError('commit tracked changes before image build')
    run('git','merge-base','--is-ancestor',APP_BASELINE,revision,cwd=ROOT)
    recovery=json.loads(a.recovery.read_text()); sdk=Path(recovery['sdk']); baseline=Path(recovery['baseline_image'])
    if (recovery['artifact_sha256']!=ARTIFACT_SHA256 or recovery['buildroot_commit']!=BUILDROOT or
            recovery['buildroot_submodule']!=BUILDROOT_SUBMODULE or sha(baseline)!=recovery['baseline_image_sha256']):
        raise ValueError('verified baseline/SDK recovery record required')
    host=json.loads(a.host_gates.read_text()); codec=json.loads(a.codec_gates.read_text())
    host_codec=json.loads(a.host_codec_gates.read_text())
    if host_codec.get('status')!='PASS' or host_codec.get('firmware_commit')!=revision:
        raise ValueError('source-matching real codec-enabled native adapter checks required')
    if host.get('status')!='PASS' or host.get('firmware_commit')!=revision or codec.get('status')!='PASS':
        raise ValueError('completed source-matching host and codec gates required')
    required={'freedv_native_radio_backend','freedv_native_audio_adapter','freedv_session_controller',
              'freedv_capture_dispatch','freedv_audio_playback','freedv_writer_preferences',
              'freedv_input_fence','freedv_front_input','freedv_remote_input_fence','freedv_native_coordinator'}
    if not required<=set(host.get('executed_diagnostic_tests',[])):
        raise ValueError('real backend/audio fault tests must execute before imaging')
    for name,expected in codec['source_sha256'].items():
        if sha(ROOT/name)!=expected: raise ValueError('SDK stream gate source differs: '+name)
    package_work=a.benchmark_work.resolve(); package=package_work/'package'
    metadata=json.loads((package/'package-manifest.json').read_text())
    if metadata['target']!='x6200-buildroot' or metadata['firmware_commit']!=revision:
        raise ValueError('matching source SDK benchmark package required')
    for key in ('rade_acquisition_experiment','rade_pipeline_experiment','rade_acquisition_parallel_experiment'):
        if not metadata.get(key,{}).get('enabled') or not metadata[key].get('sdk_validation'):
            raise ValueError('mandatory matching-SDK correctness gates absent: '+key)
    if metadata.get('native_diagnostic_fixture',{}).get('sha256')!='aaf341b481e7b8d81130483edf8352b13c11459887ed8cba8570d5503c2d6d5d':
        raise ValueError('pinned continuous diagnostic fixture required')
    work=a.work.resolve(); work.mkdir(parents=True,exist_ok=False)
    output=a.output.resolve(); output.mkdir(parents=True,exist_ok=False)
    version=re.search(r'#define VERSION\s+".* v([0-9.]+)"',(ROOT/'src/main.h').read_text())[1]
    if version!='0.3.6': raise ValueError('explicitly re-qualify changed application version')
    overlay=current_overlay(work,version)
    opus=package_work/'rade_c/build_opus-prefix/src/build_opus'
    paths={'FREEDV_RADE_SOURCE':package_work/'sources/rade_c/src','FREEDV_OPUS_BUILD':opus,
           'FREEDV_RADE_LIBRARY':package_work/'rade_c/src/librade.so.0.1','FREEDV_OPUS_LIBRARY':opus/'.libs/libopus.a',
           'FREEDV_SAMPLERATE_LIBRARY':package_work/'samplerate/src/libsamplerate.a',
           'FREEDV_SAMPLERATE_INCLUDE_DIR':package_work/'sources/libsamplerate-0.2.2/include'}
    cc=sorted((sdk/'bin').glob('arm*-gcc'),key=lambda p:('buildroot' not in p.name,p.name))[0]
    target=subprocess.check_output([cc,'-dumpmachine'],text=True).strip()
    sysroot=Path(subprocess.check_output([cc,'-print-sysroot'],text=True).strip())
    ne10=sysroot/'usr/lib/libNE10.so'
    if ne10.is_file(): paths['FREEDV_NE10_LIBRARY']=ne10
    record={'firmware_commit':revision,'upstream_application_baseline':APP_BASELINE,'application_version':version,
            'software_gates_passed':True,'hardware_qualified':False,'baseline_recovery':recovery,
            'host_gates_sha256':sha(a.host_gates),'host_codec_gates_sha256':sha(a.host_codec_gates),'codec_gates_sha256':sha(a.codec_gates),
            'source_manifest_sha256':sha(ROOT/'tools/freedv/sources.json'),
            'model_sha256':metadata['opus_model_sha256']}
    data=overlay/'usr/share/x6100/freedv'; data.mkdir(parents=True,exist_ok=True)
    copy_files(package/'fixtures',data/'fixtures')
    (data/'build-manifest.json').write_text(json.dumps({**record,'sources':metadata['sources'],
        'codec_package_manifest_sha256':sha(package/'package-manifest.json'),
        'fixture':metadata['native_diagnostic_fixture']},indent=2)+'\n')
    variants=[]
    for variant in ('normal','diagnostic'):
        build=work/('gui-'+variant)
        options=['-DCMAKE_TOOLCHAIN_FILE='+str(sdk/'share/buildroot/toolchainfile.cmake'),
                 '-DCMAKE_BUILD_TYPE=RelWithDebInfo','-DHW_BACKEND=x6200','-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON',
                 '-DENABLE_FREEDV_DIAGNOSTICS='+('ON' if variant=='diagnostic' else 'OFF'),
                 '-DENABLE_FREEDV_NATIVE_CODEC='+('ON' if variant=='diagnostic' else 'OFF')]
        if variant=='diagnostic': options+=['-D'+key+'='+str(value) for key,value in paths.items()]
        run('cmake','-S',ROOT,'-B',build,*options)
        run('cmake','--build',build,'--target','x6100_gui','-j',a.jobs)
        runtime=work/('gui-'+variant+'.runtime'); shutil.copy2(build/'src/x6100_gui',runtime)
        symbols=output/('gui-'+variant+'.debug')
        run(sdk/'bin'/(target+'-objcopy'),'--only-keep-debug',runtime,symbols)
        run(sdk/'bin'/(target+'-strip'),'--strip-unneeded',runtime)
        run(sdk/'bin'/(target+'-objcopy'),'--add-gnu-debuglink='+str(symbols),runtime)
        candidate=output/('x6200-'+variant+'.img')
        result=prepare(baseline,runtime,package,overlay,variant,revision,sdk,candidate,record,a.debugfs,a.e2fsck,a.qemu)
        variants.append({'variant':variant,'image_sha256':result['image_sha256'],'path':candidate.name})
        shutil.copy2(build/'CMakeCache.txt',output/('cmake-'+variant+'.txt'))
    for source,name in [(ROOT/'tools/freedv/sources.json','sources.json'),(a.host_gates,'host-gates.json'),
                        (a.codec_gates,'codec-gates.json'),(a.host_codec_gates,'host-codec-gates.json'),(a.recovery,'sdk-recovery.json')]: shutil.copy2(source,output/name)
    (output/'release-manifest.json').write_text(json.dumps({**record,'variants':variants},indent=2)+'\n')
    # Build documentation is separate from the operator's short guided session.
    shutil.copy2(ROOT/'docs/freedv/native-image-build.md',output/'BUILD.md')
    shutil.copy2(ROOT/'docs/freedv/native-radio-test.md',output/'RADIO-TEST.md')
    sums=[sha(path)+'  '+path.name for path in sorted(output.iterdir()) if path.is_file()]
    (output/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
    print('PASS: paired normal/diagnostic images passed build, filesystem and runtime ABI gates:',output)


if __name__=='__main__': main()
