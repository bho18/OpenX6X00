#!/usr/bin/env python3
"""Create a spare-card test image offline. Accepts regular image files, never devices."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def request(tool, image, command):
    completed = subprocess.run([tool, '-w', '-R', command, str(image)], check=True, capture_output=True, text=True)
    return completed.stdout + completed.stderr


def quote(path):
    text = str(path)
    if any(c in text for c in ('"', '\n', '\r', '\\')):
        raise ValueError('unsupported debugfs path')
    return '"' + text + '"'


def copy_region(source, target, count):
    while count:
        data = source.read(min(count, 1024 * 1024))
        if not data:
            raise ValueError('truncated image partition')
        target.write(data)
        count -= len(data)


def require_arm_elf(path):
    with path.open('rb') as f:
        header = f.read(52)
    if len(header) != 52 or header[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', header, 18)[0] != 40 or not struct.unpack_from('<I', header, 36)[0] & 0x400:
        raise ValueError('expected little-endian ARM32 hard-float ELF: ' + str(path))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', required=True, type=Path)
    p.add_argument('--gui', required=True, type=Path)
    p.add_argument('--package', required=True, type=Path)
    p.add_argument('--variant', required=True, choices=('baseline', 'diagnostic-readonly'))
    p.add_argument('--firmware-commit', required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--debugfs', default='debugfs')
    p.add_argument('--e2fsck', default='e2fsck')
    a = p.parse_args()
    if not a.baseline.is_file() or not a.gui.is_file() or a.output.exists():
        p.error('baseline and GUI must be regular files; output must be new')
    package = a.package.resolve()
    metadata = json.loads((package / 'package-manifest.json').read_text())
    if metadata['target'] != 'x6200-buildroot' or metadata['firmware_commit'] != a.firmware_commit or not re.fullmatch('[0-9a-f]{40}', a.firmware_commit):
        p.error('test card requires an X6200 SDK package')
    require_arm_elf(a.gui)
    for binary in (package / 'bin').iterdir():
        require_arm_elf(binary)
    for line in (package / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = (package / name).resolve()
        if package not in path.parents or digest(path) != expected:
            raise ValueError('package checksum mismatch: ' + name)
    with a.baseline.open('rb') as image:
        mbr = image.read(512)
        if len(mbr) != 512 or mbr[510:] != b'\x55\xaa':
            raise ValueError('expected DOS partition table')
        partitions = [struct.unpack('<B3sB3sII', mbr[446 + i * 16:462 + i * 16]) for i in range(4)]
        linux = [(entry[4] * 512, entry[5] * 512) for entry in partitions if entry[2] == 0x83]
        if len(linux) != 1 or not any(entry[2] == 0x0c for entry in partitions):
            raise ValueError('unexpected X6200 card layout')
        offset, size = linux[0]
        if offset < 1024 * 1024 or size < 1024 * 1024 or offset + size > a.baseline.stat().st_size:
            raise ValueError('invalid root partition bounds')
        image.seek(offset + 1080)
        if image.read(2) != b'\x53\xef':
            raise ValueError('root partition is not ext filesystem')
    with tempfile.TemporaryDirectory(prefix='freedv-image-', dir=a.output.parent) as tmp:
        temp = Path(tmp)
        rootfs = temp / 'rootfs.ext4'
        with a.baseline.open('rb') as source, rootfs.open('wb') as target:
            source.seek(offset); copy_region(source, target, size)
        subprocess.run([a.e2fsck, '-fn', str(rootfs)], check=True)
        before = temp / 'old-gui'
        request(a.debugfs, rootfs, 'dump /usr/sbin/x6100_gui ' + quote(before))
        if not before.is_file():
            raise ValueError('baseline GUI is missing')
        def write_file(source, destination, executable=False):
            request(a.debugfs, rootfs, 'rm ' + quote(destination))
            request(a.debugfs, rootfs, 'write ' + quote(source) + ' ' + quote(destination))
            request(a.debugfs, rootfs, 'sif ' + quote(destination) + ' mode ' + ('0100755' if executable else '0100644'))
            verify = temp / 'verify'
            if verify.exists(): verify.unlink()
            request(a.debugfs, rootfs, 'dump ' + quote(destination) + ' ' + quote(verify))
            if not verify.is_file() or digest(verify) != digest(source):
                raise ValueError('image write verification failed: ' + destination)
        write_file(a.gui, '/usr/sbin/x6100_gui', True)
        request(a.debugfs, rootfs, 'mkdir /opt')
        request(a.debugfs, rootfs, 'mkdir /opt/freedv-benchmark')
        for path in sorted(package.rglob('*')):
            relative = path.relative_to(package).as_posix()
            destination = '/opt/freedv-benchmark/' + relative
            if path.is_symlink(): raise ValueError('package symlink refused')
            if path.is_dir(): request(a.debugfs, rootfs, 'mkdir ' + quote(destination))
            elif path.is_file(): write_file(path, destination, relative.startswith('bin/'))
        record = {'schema_version': 1, 'variant': a.variant, 'firmware_commit': a.firmware_commit,
                  'baseline_image_sha256': digest(a.baseline), 'gui_sha256': digest(a.gui),
                  'baseline_gui_sha256': digest(before), 'benchmark_manifest_sha256': digest(package / 'package-manifest.json'),
                  'routing_experiments_available': False, 'hardware_qualified': False}
        build_info = temp / 'build.json'; build_info.write_text(json.dumps(record, indent=2) + '\n')
        write_file(build_info, '/etc/freedv-test-build.json')
        subprocess.run([a.e2fsck, '-fn', str(rootfs)], check=True)
        # Preserve bootloader, boot partition, partition table and UUIDs.
        with a.output.open('xb') as target, a.baseline.open('rb') as source:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.seek(offset)
            with rootfs.open('rb') as root:
                copy_region(root, target, size)
        record['image_sha256'] = digest(a.output)
        a.output.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Created verified offline test image:', a.output)


if __name__ == '__main__':
    main()
