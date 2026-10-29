#!/usr/bin/env python3
"""Apply current runtime assets and a tested GUI to a disposable X6200 image."""
import argparse
import json
from pathlib import Path
import stat
import subprocess
import tempfile
from native_image import ExtImage, extract_root, replace_root, require_arm_elf, require_regular, sha
from verify_native_abi import verify as verify_abi


def prepare(baseline, gui, package, overlay, variant, revision, sdk, output, build_record,
            debugfs='debugfs', e2fsck='e2fsck', qemu='qemu-arm'):
    baseline,gui,package,overlay,sdk,output=map(lambda p:Path(p).resolve(),(baseline,gui,package,overlay,sdk,output))
    if output.exists(): raise ValueError('output image already exists')
    require_regular(baseline); require_arm_elf(gui)
    metadata=json.loads((package/'package-manifest.json').read_text())
    if metadata['target']!='x6200-buildroot' or metadata['firmware_commit']!=revision:
        raise ValueError('matching source/SDK benchmark package required')
    for line in (package/'SHA256SUMS').read_text().splitlines():
        expected,name=line.split('  ',1); p=package/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not p.is_file() or sha(p)!=expected:
            raise ValueError('package checksum mismatch: '+name)
    with tempfile.TemporaryDirectory(prefix='native-image-',dir=output.parent) as tempdir:
        temp=Path(tempdir); root=temp/'rootfs.ext4'; offset,size=extract_root(baseline,root)
        subprocess.run([str(e2fsck),'-fn',str(root)],check=True,capture_output=True,text=True)
        image=ExtImage(root,temp/'scratch',debugfs)
        old_gui=image.dump('/usr/sbin/x6100_gui')
        # Neither replacing a signing key nor an allow-unsigned marker belongs
        # in diagnostic delivery. Preserve the baseline key byte-for-byte.
        old_key=image.dump('/usr/share/x6100/update.pub')
        if sha(old_key)!=sha(overlay/'usr/share/x6100/update.pub'):
            raise ValueError('update verification key differs from baseline; explicit review required')
        files={}
        # Replace complete web module/metadata so deleted endpoints do not
        # survive from the v0.3.5 baseline. Preserve unrelated packages.
        site='/usr/lib/python3.11/site-packages'
        if not image.info(site): raise ValueError('expected baseline Python 3.11 runtime')
        for child,_ in image.children(site):
            if child.startswith('x6100_webserver'):
                image.remove_tree(site+'/'+child)
        image.remove_tree('/usr/share/x6100/themes')
        for path in sorted(overlay.rglob('*')):
            if path.is_symlink(): raise ValueError('runtime overlay symlinks require explicit handling')
            if not path.is_file(): continue
            destination='/'+path.relative_to(overlay).as_posix()
            if destination.startswith(('/mnt/','/lib/firmware/')) or destination.endswith('/allow-unsigned'):
                raise ValueError('diagnostic overlay touches protected data/firmware policy')
            files[destination]=image.write_file(path,destination)
        files['/usr/sbin/x6100_gui']=image.write_file(gui,'/usr/sbin/x6100_gui',0o755)
        image.mkdir('/opt/freedv-benchmark')
        for path in sorted(package.rglob('*')):
            if path.is_symlink(): raise ValueError('flatten codec package symlinks before imaging')
            if path.is_file():
                name=path.relative_to(package).as_posix()
                files['/opt/freedv-benchmark/'+name]=image.write_file(path,'/opt/freedv-benchmark/'+name,
                                                                    0o755 if name.startswith('bin/') else 0o644)
        if sha(image.dump('/usr/share/x6100/update.pub'))!=sha(old_key):
            raise ValueError('signature verification key changed')
        # Verify exact loader/dependency bytes and version requirements against
        # the edited image before creating any deliverable SD-card image.
        abi=verify_abi(gui,package,sdk,root,temp/'abi',debugfs,qemu)
        record={**build_record,'schema_version':1,'variant':variant,'firmware_commit':revision,
                'hardware_qualified':False,'ready_for_guided_hardware_tests':variant=='diagnostic',
                'baseline_image_sha256':sha(baseline),'baseline_gui_sha256':sha(old_gui),
                'gui_sha256':sha(gui),'package_manifest_sha256':sha(package/'package-manifest.json'),
                'root_partition_offset':offset,'root_partition_bytes':size,
                'signature_key_preserved':True,'runtime_assets':files,'abi':abi}
        internal=temp/'build.json'; internal.write_text(json.dumps(record,indent=2)+'\n')
        image.write_file(internal,'/etc/freedv-test-build.json',0o644)
        subprocess.run([str(e2fsck),'-fn',str(root)],check=True,capture_output=True,text=True)
        replace_root(baseline,root,output,offset,size)
        record['image_sha256']=sha(output)
        output.with_suffix('.json').write_text(json.dumps(record,indent=2)+'\n')
        output.with_suffix('.abi.json').write_text(json.dumps(abi,indent=2)+'\n')
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','gui','package','overlay','sdk','output','build-record'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--variant',choices=('normal','diagnostic'),required=True)
    p.add_argument('--revision',required=True)
    p.add_argument('--debugfs',default='debugfs'); p.add_argument('--e2fsck',default='e2fsck'); p.add_argument('--qemu',default='qemu-arm')
    a=p.parse_args(); record=json.loads(a.build_record.read_text())
    if record.get('software_gates_passed') is not True or record.get('firmware_commit')!=a.revision:
        p.error('build record must contain completed gates for this exact source commit')
    prepare(a.baseline,a.gui,a.package,a.overlay,a.variant,a.revision,a.sdk,a.output,record,a.debugfs,a.e2fsck,a.qemu)
    print('Created verified spare-card image:',a.output)


if __name__=='__main__': main()
