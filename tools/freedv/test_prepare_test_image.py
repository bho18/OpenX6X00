#!/usr/bin/env python3
"""Synthetic ext4 image test; these fake ELF fixtures must never be deployed."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=Path('/usr/sbin'))
    a = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='freedv-synthetic-image-') as directory:
        t = Path(directory)
        root = t / 'root'; (root / 'usr/sbin').mkdir(parents=True); (root / 'etc').mkdir()
        (root / 'usr/sbin/x6100_gui').write_bytes(b'old synthetic GUI')
        (root / 'etc/sentinel').write_bytes(b'preserve settings and signature policy')
        fs = t / 'rootfs'
        with fs.open('wb') as f: f.truncate(16 * 1024 * 1024)
        subprocess.run([str(a.tools / 'mke2fs'), '-q', '-t', 'ext4', '-F', '-d', str(root), str(fs)], check=True)
        mbr = bytearray(512); mbr[510:] = b'\x55\xaa'
        struct.pack_into('<B3sB3sII', mbr, 446, 0x80, bytes(3), 0x0c, bytes(3), 2048, 2048)
        struct.pack_into('<B3sB3sII', mbr, 462, 0, bytes(3), 0x83, bytes(3), 4096, fs.stat().st_size // 512)
        image = t / 'baseline.img'
        boot = bytes(mbr) + b'B' * (2 * 1024 * 1024 - 512)
        image.write_bytes(boot + fs.read_bytes())
        original = hashlib.sha256(image.read_bytes()).hexdigest()
        package = t / 'package'; (package / 'bin').mkdir(parents=True)
        elf = bytearray(52); elf[:6] = b'\x7fELF\x01\x01'; struct.pack_into('<H', elf, 18, 40); struct.pack_into('<I', elf, 36, 0x5000400)
        gui = t / 'new-gui'; gui.write_bytes(elf)
        (package / 'bin/synthetic-not-executable').write_bytes(elf)
        (package / 'package-manifest.json').write_text(json.dumps({'target':'x6200-buildroot','firmware_commit':'a'*40}))
        (package / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + str(p.relative_to(package)) + '\n' for p in sorted(package.rglob('*')) if p.is_file()))
        output = t / 'test.img'
        command = [sys.executable, str(Path(__file__).with_name('prepare_test_image.py')),
                   '--baseline', str(image), '--gui', str(gui), '--package', str(package), '--variant', 'diagnostic-readonly',
                   '--firmware-commit', 'a'*40, '--output', str(output), '--debugfs', str(a.tools/'debugfs'), '--e2fsck', str(a.tools/'e2fsck')]
        subprocess.run(command, check=True)
        assert output.read_bytes()[:len(boot)] == boot
        assert hashlib.sha256(image.read_bytes()).hexdigest() == original
        after = t / 'after.ext4'; after.write_bytes(output.read_bytes()[len(boot):])
        sentinel = t / 'sentinel'
        subprocess.run([str(a.tools/'debugfs'), '-R', 'dump /etc/sentinel ' + str(sentinel), str(after)], check=True)
        assert sentinel.read_bytes() == (root/'etc/sentinel').read_bytes()
        assert subprocess.run(command, capture_output=True).returncode != 0 # refuses overwrite
        (package/'bin/synthetic-not-executable').write_bytes(b'tampered')
        failed = command.copy(); failed[failed.index('--output') + 1] = str(t/'refused.img')
        assert subprocess.run(failed, capture_output=True).returncode != 0
        assert not (t/'refused.img').exists()
        print('PASS: offline ext4 replacement, untouched boot region/source/sentinel, checksum and overwrite refusals')


if __name__ == '__main__':
    main()
