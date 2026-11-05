#!/usr/bin/env python3
"""Offline ext4 image editing primitives; regular files only, never block devices."""
import hashlib
from pathlib import Path
import posixpath
import re
import shutil
import stat
import struct
import subprocess


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def quote(value):
    text = str(value)
    if any(c in text for c in ('"', '\n', '\r', '\\', '\x00')):
        raise ValueError('unsupported debugfs path')
    return '"' + text + '"'


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs).stdout


def require_regular(path):
    if not stat.S_ISREG(Path(path).lstat().st_mode):
        raise ValueError('regular file required: ' + str(path))


def require_arm_elf(path):
    require_regular(path)
    header = Path(path).read_bytes()[:52]
    if (len(header) != 52 or header[:6] != b'\x7fELF\x01\x01' or
            struct.unpack_from('<H', header, 18)[0] != 40 or not struct.unpack_from('<I', header, 36)[0] & 0x400):
        raise ValueError('expected little-endian ARM32 hard-float ELF: ' + str(path))


def root_partition(image):
    require_regular(image)
    with Path(image).open('rb') as stream:
        mbr = stream.read(512)
        if len(mbr) != 512 or mbr[510:] != b'\x55\xaa':
            raise ValueError('DOS partition table required')
        entries = [struct.unpack('<B3sB3sII', mbr[446+i*16:462+i*16]) for i in range(4)]
        if entries[0][2] not in (0x0b, 0x0c) or entries[1][2] != 0x83:
            raise ValueError('expected X6200 FAT boot and Linux root partitions')
        offset, size = entries[1][4]*512, entries[1][5]*512
        if offset < 1024*1024 or size < 1024*1024 or offset+size > Path(image).stat().st_size:
            raise ValueError('root partition bounds invalid')
        stream.seek(offset+1080)
        if stream.read(2) != b'\x53\xef':
            raise ValueError('root partition is not ext filesystem')
    return offset, size


def copy_region(source, target, count):
    while count:
        block = source.read(min(count, 1024*1024))
        if not block:
            raise ValueError('truncated image')
        target.write(block); count -= len(block)


class ExtImage:
    def __init__(self, image, scratch, debugfs='debugfs'):
        self.image, self.scratch, self.debugfs = Path(image), Path(scratch), debugfs
        require_regular(self.image)
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.sequence = 0

    def request(self, command, write=False):
        args = [self.debugfs] + (['-w'] if write else []) + ['-R', command, self.image]
        result = subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True)
        return result.stdout + result.stderr

    def info(self, name):
        result = self.request('stat ' + quote(name))
        if not re.search(r'Inode:\s+\d+', result):
            return None
        return result

    def resolve(self, name):
        name = posixpath.normpath('/' + name.lstrip('/'))
        for _ in range(32):
            parts = name.split('/')[1:]
            changed = False
            for i in range(len(parts)):
                prefix = '/' + '/'.join(parts[:i+1])
                info = self.info(prefix)
                if not info:
                    return None
                if 'Type: symlink' not in info:
                    continue
                fast = re.search(r'Fast link dest: "(.*)"', info)
                if fast:
                    target = fast[1]
                else:
                    target = self.dump(prefix, follow=False).read_text()
                replacement = target if target.startswith('/') else posixpath.join(posixpath.dirname(prefix), target)
                name = posixpath.normpath(posixpath.join(replacement, *parts[i+1:]))
                changed = True
                break
            if not changed:
                return name
        raise ValueError('symlink loop in root image: ' + name)

    def dump(self, name, follow=True):
        resolved = self.resolve(name) if follow else name
        if resolved is None:
            raise ValueError('missing image file: ' + name)
        self.sequence += 1
        output = self.scratch / ('dump-' + str(self.sequence))
        self.request('dump ' + quote(resolved) + ' ' + quote(output))
        if not output.is_file():
            raise ValueError('cannot dump image file: ' + name)
        return output

    def children(self, name):
        result = self.request('ls -p ' + quote(name))
        entries = []
        for line in result.splitlines():
            fields = line.split('/')
            if len(fields) >= 7 and fields[1].isdigit():
                child = fields[5]
                if child not in ('.', '..'):
                    entries.append((child, int(fields[2], 8)))
        return entries

    def remove_tree(self, name):
        info = self.info(name)
        if not info:
            return
        if 'Type: directory' in info:
            for child, _ in self.children(name):
                self.remove_tree(name.rstrip('/') + '/' + child)
            self.request('rmdir ' + quote(name), write=True)
        else:
            self.request('rm ' + quote(name), write=True)
        if self.info(name):
            raise ValueError('image removal failed: ' + name)

    def mkdir(self, name):
        partial = ''
        for part in name.split('/'):
            if not part:
                continue
            partial += '/' + part
            info = self.info(partial)
            if info:
                if 'Type: directory' not in info:
                    raise ValueError('image destination parent is not a directory: ' + partial)
            else:
                self.request('mkdir ' + quote(partial), write=True)
                if not self.info(partial):
                    raise ValueError('image mkdir failed: ' + partial)

    def write_file(self, source, destination, mode=None):
        require_regular(source)
        if not destination.startswith('/') or '..' in Path(destination).parts:
            raise ValueError('absolute image destination required')
        self.mkdir(posixpath.dirname(destination))
        self.remove_tree(destination)
        self.request('write ' + quote(source) + ' ' + quote(destination), write=True)
        self.request('sif ' + quote(destination) + ' uid 0', write=True)
        self.request('sif ' + quote(destination) + ' gid 0', write=True)
        permissions = stat.S_IMODE(Path(source).stat().st_mode) if mode is None else mode
        self.request('sif ' + quote(destination) + ' mode ' + ('0' + format(stat.S_IFREG | permissions, 'o')), write=True)
        actual = self.dump(destination)
        if sha(source) != sha(actual):
            raise ValueError('image write/readback mismatch: ' + destination)
        info = self.info(destination)
        match = re.search(r'Mode:\s+(0[0-7]+)', info)
        if not match or int(match[1], 8) != permissions:
            raise ValueError('image mode mismatch: ' + destination)
        return {'sha256': sha(source), 'mode': oct(permissions)}


def extract_root(image, output):
    offset, size = root_partition(image)
    with Path(image).open('rb') as source, Path(output).open('xb') as target:
        source.seek(offset); copy_region(source, target, size)
    return offset, size


def replace_root(baseline, rootfs, output, offset, size):
    # Entire source image is copied first. This changes only the original root
    # byte range: bootloader/boot partition/partition table/data geometry stay.
    with Path(output).open('xb') as target, Path(baseline).open('rb') as source:
        shutil.copyfileobj(source, target, length=1024*1024)
        target.seek(offset)
        with Path(rootfs).open('rb') as root:
            copy_region(root, target, size)
    with Path(baseline).open('rb') as before, Path(output).open('rb') as after:
        remaining = offset
        while remaining:
            n = min(remaining, 1024*1024)
            if before.read(n) != after.read(n):
                raise ValueError('boot region changed')
            remaining -= n
        before.seek(offset+size); after.seek(offset+size)
        while True:
            a, b = before.read(1024*1024), after.read(1024*1024)
            if a != b:
                raise ValueError('bytes outside root partition changed')
            if not a:
                break
