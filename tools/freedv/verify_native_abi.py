#!/usr/bin/env python3
"""Verify the complete ARM ELF dependency closure against actual image bytes.

Every baseline dependency must match the allocated code/data of its matching-SDK file;
Buildroot target stripping may remove debug sections. New codec
libraries come only from the checksummed package. QEMU's loader checks runtime
version requirements without starting the GUI or opening hardware devices.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from native_image import ExtImage, require_arm_elf, run, sha


def needed(readelf, path):
    return re.findall(r'Shared library: \[(.*?)\]', run(readelf, '-d', path))


def sdk_file(sysroot, name):
    candidates = []
    for directory in ('lib', 'usr/lib', 'usr/lib/pulseaudio'):
        path = sysroot / directory / name
        if path.is_file():
            candidates.append(path)
    if not candidates:
        candidates = [p for p in sysroot.rglob(name) if p.is_file()]
    if not candidates or len({sha(p.resolve()) for p in candidates}) != 1:
        raise ValueError('missing or ambiguous SDK dependency: ' + name)
    return candidates[0]


def same_sdk_build(readelf, actual, expected):
    """Accept only strip/debug differences between SDK and target libraries."""
    ids=[]
    for path in (actual, expected):
        match=re.search(r'Build ID: ([0-9a-f]+)',run(readelf,'-n',path))
        ids.append(match[1] if match else None)
    if ids[0] and ids[1] and ids[0]!=ids[1]:
        raise ValueError('SDK/target ELF build IDs differ: '+str(expected))
    sections={}
    for section in ('.text','.rodata','.data','.dynsym','.dynstr','.gnu.version','.gnu.version_d','.gnu.version_r'):
        pair=[]
        for i,path in enumerate((actual,expected)):
            dumped=run(readelf,'-x',section,path)
            pair.append(hashlib.sha256(dumped.encode()).hexdigest() if dumped.strip() else None)
        if pair[0]!=pair[1]:
            raise ValueError('SDK/target ELF '+section+' differs: '+str(expected))
        if pair[0]: sections[section]=pair[0]
    if '.text' not in sections:
        raise ValueError('ELF dependency has no comparable code: '+str(expected))
    return {'sdk_sha256':sha(expected),'image_sha256':sha(actual),'build_id':ids[0],
            'matching_allocated_section_dumps_sha256':sections}

def verify(gui, package, sdk, image, output, debugfs='debugfs', qemu='qemu-arm'):
    gui, package, sdk, output = map(lambda p: Path(p).resolve(), (gui, package, sdk, output))
    output.mkdir(parents=True, exist_ok=False)
    compilers = sorted((sdk/'bin').glob('arm*-gcc'), key=lambda p: ('buildroot' not in p.name, p.name))
    if not compilers:
        raise ValueError('matching SDK compiler missing')
    compiler = compilers[0]
    target = run(compiler, '-dumpmachine').strip()
    if 'buildroot' not in target or not target.startswith('arm'):
        raise ValueError('deployment requires ARM Buildroot SDK')
    readelf = sdk/'bin'/(target+'-readelf')
    sysroot = Path(run(compiler, '-print-sysroot').strip()).resolve()
    if sdk not in sysroot.parents:
        raise ValueError('SDK sysroot is outside relocated SDK')
    filesystem = ExtImage(image, output/'scratch', debugfs)
    require_arm_elf(gui)
    interpreter_match = re.search(r'Requesting program interpreter: (.*?)\]', run(readelf, '-l', gui))
    if not interpreter_match or interpreter_match[1] != '/lib/ld-linux-armhf.so.3':
        raise ValueError('unexpected GUI ARM loader')
    loader = interpreter_match[1]
    target_loader = filesystem.dump(loader)
    sdk_loader = sysroot/loader.lstrip('/')
    if not sdk_loader.is_file(): raise ValueError('matching SDK loader missing')
    loader_identity=same_sdk_build(readelf,target_loader,sdk_loader.resolve())
    runtime = output/'runtime'; (runtime/'lib').mkdir(parents=True)
    shutil.copy2(target_loader, runtime/'lib/ld-linux-armhf.so.3')
    (runtime/'lib/ld-linux-armhf.so.3').chmod(0o755)
    closure, queue = {}, [gui]
    for binary in (package/'bin').iterdir():
        if binary.is_file():
            require_arm_elf(binary); queue.append(binary)
    while queue:
        binary = queue.pop()
        for name in needed(readelf, binary):
            if '/' in name or name in ('.', '..'):
                raise ValueError('invalid ELF dependency name')
            if name in closure:
                continue
            packaged = package/'lib'/name
            if packaged.is_file():
                actual = filesystem.dump('/opt/freedv-benchmark/lib/'+name)
                expected, provider = packaged, 'benchmark-package'
            else:
                expected = sdk_file(sysroot, name)
                relative = expected.relative_to(sysroot)
                actual = filesystem.dump('/'+relative.as_posix())
                provider = 'matching-baseline-sdk'
            require_arm_elf(actual)
            if provider=='benchmark-package':
                if sha(actual)!=sha(expected.resolve()): raise ValueError('packaged dependency bytes differ: '+name)
                identity={'sdk_sha256':None,'image_sha256':sha(actual)}
            else:
                identity=same_sdk_build(readelf,actual,expected.resolve())
            copied = runtime/'lib'/name; shutil.copy2(actual, copied)
            closure[name] = {'sha256': sha(actual), 'provider': provider, 'sdk_identity':identity,
                             'needed': needed(readelf, copied)}
            queue.append(copied)
    ld = runtime/'lib/ld-linux-armhf.so.3'
    ld.chmod(0o755) # libc's dependency closure may have recopied the loader
    programs = [gui]+[p for p in (package/'bin').iterdir() if p.is_file()]
    loader_checks = {}
    for program in programs:
        result = subprocess.run([str(qemu), '-L', str(runtime), '-E', 'LD_LIBRARY_PATH='+str(runtime/'lib'),
                                 str(ld), '--list', str(program)], check=True, capture_output=True, text=True)
        loader_checks[program.name] = result.stdout
    record = {'schema_version': 1, 'status': 'PASS', 'hardware_qualified': False,
              'compiler_version': run(compiler, '--version').splitlines()[0],
              'compiler_target': target, 'loader_sha256': sha(target_loader), 'loader_sdk_identity':loader_identity,
              'gui_sha256': sha(gui), 'dependencies': closure, 'loader_checks': loader_checks}
    (output/'report.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('gui','package','sdk','rootfs','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--debugfs', default='debugfs')
    parser.add_argument('--qemu', default='qemu-arm')
    a = parser.parse_args()
    record = verify(a.gui,a.package,a.sdk,a.rootfs,a.output,a.debugfs,a.qemu)
    print('PASS: ARM loader and',len(record['dependencies']),'verified runtime dependencies')


if __name__ == '__main__':
    main()
