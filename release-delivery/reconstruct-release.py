#!/usr/bin/env python3
"""Reconstruct the verified Linux release from public source and an exact raw image.

Input metadata is a checksummed, extracted transport directory. No original
outputs, checkout files, hardware, stock firmware or private Git history are
modified. Compression containers may differ across compressor versions; the
raw image, retained source bytes and original software verification may not.
"""
import argparse
import binascii
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

ORIGINAL_COMMIT = 'bbcd265e3f644f508067eafb157e4f31978adb11'
PUBLIC_COMMIT = 'e2d5cdc7c85f8bcfd74d1b2e2d20ce2d55757ca1'
RAW_SHA256 = 'fdfcc3791f68e1028751cc54591dfe96ef9e08ad12a3223bcf3e70cb39ef2c35'
RAW_BYTES = 571473920
SOFTWARE_SHA256 = '202b0c3b4e52a3a7084bc412617102427d0d2a8b224bde16de4ab09ef05906e0'
PUBLICATION_SHA256 = '09d404c4235b3a8012bc2d20e18c4e0dc36eceb65f43309509d1673b75151d9b'
# Set from the prepared transport after its complete file manifest is generated.
TRANSPORT_SUMS_SHA256 = '1dd0dbdb99408065ab825e1840ffd3e9e35d90b8db22939de369bb952621f453'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError('unsafe relative path: ' + name)
    return p


def input_path(root, name):
    safe_relative(name)
    path = root / name
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('missing or escaping input: ' + name)
    return path


def read_input(root, name):
    return input_path(root, name).read_bytes()


def validate_transport(root):
    sums = input_path(root, 'TRANSPORT-SHA256SUMS')
    if sha(sums) != TRANSPORT_SUMS_SHA256:
        raise ValueError('transport checksum manifest differs from the verified release input')
    names = set()
    for line in sums.read_text().splitlines():
        expected, name = line.split('  ', 1)
        if name in names or sha(input_path(root, name)) != expected:
            raise ValueError('transport file duplicate or checksum mismatch: ' + name)
        names.add(name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != names | {'TRANSPORT-SHA256SUMS'}:
        raise ValueError('transport contains missing or unlisted files')


def validate_scope(plan, files):
    if digest(files['software-verification.json']) != SOFTWARE_SHA256:
        raise ValueError('original software verification was modified')
    if digest(files['PUBLICATION-MANIFEST.json']) != PUBLICATION_SHA256:
        raise ValueError('publication input differs from the approved filtered release')
    verification = json.loads(files['software-verification.json'])
    publication = json.loads(files['PUBLICATION-MANIFEST.json'])
    if verification['source_commit'] != ORIGINAL_COMMIT or verification['status'] != 'PASS':
        raise ValueError('unexpected original source verification')
    for obj in (verification, publication):
        if (obj.get('hardware_status') != 'BLOCKED' or obj.get('hardware_qualified') is not False or
                obj.get('normal_mode_qualified') is not False or obj.get('ready_for_guided_hardware_tests') is not False):
            raise ValueError('development/hardware qualification scope was changed')
    if publication['public_source_commit'] != PUBLIC_COMMIT:
        raise ValueError('unexpected public corresponding-source snapshot')
    excluded = publication['excluded_regular_files']
    if set(excluded) != {name for name in verification['source_files'] if name.startswith('docs/freedv/baseband-research/')}:
        raise ValueError('only the incidental research document prefix may be omitted')
    source_rows = plan['source']['rows']
    retained = {row['path']: row['sha256'] for row in source_rows}
    if len(source_rows) != publication['retained_regular_file_count'] or len(retained) != len(source_rows) or retained != publication['public_source_files']:
        raise ValueError('retained corresponding-source manifest changed')
    expected = {name: value for name, value in verification['source_files'].items() if name not in excluded}
    if retained != expected:
        raise ValueError('public application source is not byte-identical to the built source')
    if publication['private_git_history_included'] or publication['stock_firmware_research_payloads_included']:
        raise ValueError('private history or stock payloads are outside publication scope')
    image_rows = [row for row in plan['zip']['rows'] if row['image']]
    if len(image_rows) != 1 or image_rows[0]['name'] != 'x6200-native-development.img':
        raise ValueError('Windows ZIP must contain exactly one full image')
    names = [row['name'] for row in plan['zip']['rows']]
    if len(names) != len(set(names)) or any('baseband-research/' in n or n.endswith('.bundle') for n in names):
        raise ValueError('unexpected research/private-history ZIP entry')
    if (plan['image']['sha256'] != RAW_SHA256 or plan['image']['bytes'] != RAW_BYTES or
            publication['image']['sha256'] != RAW_SHA256):
        raise ValueError('unexpected verified image identity')
    return publication


def reconstruct_source(plan, metadata, source, destination):
    layout = plan['source']
    scaffold = read_input(metadata, layout['scaffold'])
    raw_hash = hashlib.sha256()
    with lzma.open(destination, 'wb', format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6) as output:
        def emit(data):
            output.write(data)
            raw_hash.update(data)
        for row in layout['rows']:
            name = row['path']
            if name.startswith('docs/freedv/baseband-research/'):
                raise ValueError('research document requested in public source')
            data = input_path(source, name).read_bytes()
            if len(data) != row['size'] or digest(data) != row['sha256']:
                raise ValueError('public checkout file differs: ' + name)
            start, size = row['prefix_offset'], row['prefix_size']
            if start < 0 or size < 0 or start + size > len(scaffold):
                raise ValueError('invalid source scaffold range')
            emit(scaffold[start:start + size])
            emit(data)
        start, size = layout['suffix_offset'], layout['suffix_size']
        if start < 0 or size < 0 or start + size > len(scaffold):
            raise ValueError('invalid source suffix range')
        emit(scaffold[start:start + size])
    if raw_hash.hexdigest() != layout['raw_tar_sha256']:
        raise ValueError('reconstructed public tar stream differs from the verified source archive')


def deflate_image(image, destination):
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    checksum, raw_hash = 0, hashlib.sha256()
    with image.open('rb') as source, destination.open('xb') as output:
        while block := source.read(1024 * 1024):
            raw_hash.update(block)
            checksum = binascii.crc32(block, checksum)
            output.write(compressor.compress(block))
        output.write(compressor.flush())
    if raw_hash.hexdigest() != RAW_SHA256:
        raise ValueError('raw image changed during compression')
    return checksum & 0xffffffff


def deflate_bytes(data):
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def reconstruct_zip(plan, metadata, image_deflate, image_crc, files, destination):
    central = []
    with destination.open('xb') as output:
        for row in plan['zip']['rows']:
            name = row['name']
            safe_relative(name)
            local = bytearray(read_input(metadata, row['local_header']))
            directory = bytearray(read_input(metadata, row['central_header']))
            if local[:4] != b'PK\x03\x04' or directory[:4] != b'PK\x01\x02':
                raise ValueError('invalid preserved ZIP header')
            flags, method = struct.unpack_from('<HH', local, 6)
            if flags & 9 or method != 8:
                raise ValueError('encrypted, descriptor or non-deflate ZIP entries are unsupported')
            if row['image']:
                crc, size = image_crc, RAW_BYTES
                compressed_size = image_deflate.stat().st_size
                payload = None
            else:
                data = files[name]
                crc, size = binascii.crc32(data) & 0xffffffff, len(data)
                payload = (read_input(metadata, row['compressed_payload']) if digest(data) == row['sha256']
                           else deflate_bytes(data))
                compressed_size = len(payload)
            if max(size, compressed_size, output.tell()) >= 0xffffffff:
                raise ValueError('unexpected ZIP64 size')
            struct.pack_into('<III', local, 14, crc, compressed_size, size)
            struct.pack_into('<III', directory, 16, crc, compressed_size, size)
            struct.pack_into('<I', directory, 42, output.tell())
            output.write(local)
            if payload is None:
                with image_deflate.open('rb') as source:
                    shutil.copyfileobj(source, output)
            else:
                output.write(payload)
            central.append(directory)
        offset = output.tell()
        for entry in central:
            output.write(entry)
        size = output.tell() - offset
        end = bytearray(read_input(metadata, plan['zip']['eocd']))
        if end[:4] != b'PK\x05\x06':
            raise ValueError('invalid preserved ZIP end record')
        struct.pack_into('<HHII', end, 8, len(central), len(central), size, offset)
        output.write(end)


def verify_zip(path, files):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(files) | {'x6200-native-development.img'}:
            raise ValueError('reconstructed ZIP file set differs')
        for name in names:
            with archive.open(name) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            expected = RAW_SHA256 if name.endswith('.img') else digest(files[name])
            if actual != expected:
                raise ValueError('reconstructed ZIP member differs: ' + name)
        if archive.read('software-verification.json') != files['software-verification.json']:
            raise ValueError('software verification changed')
        for line in archive.read('SHA256SUMS').decode().splitlines():
            expected, name = line.split('  ', 1)
            actual = RAW_SHA256 if name.endswith('.img') else digest(files[name])
            if expected != actual:
                raise ValueError('reconstructed internal checksum differs: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'source', 'input', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    image, source, metadata, output = (getattr(args, n).resolve() for n in ('image', 'source', 'input', 'output'))
    if output.exists():
        parser.error('--output must be a new directory; existing assets are never overwritten')
    validate_transport(metadata)
    plan = json.loads(read_input(metadata, 'plan.json'))
    files = {}
    for row in plan['zip']['rows']:
        if not row['image']:
            data = read_input(metadata, 'files/' + row['name'])
            if len(data) != row['size'] or digest(data) != row['sha256']:
                raise ValueError('original ZIP metadata payload differs: ' + row['name'])
            files[row['name']] = data
    publication = validate_scope(plan, files)
    if image.stat().st_size != RAW_BYTES or sha(image) != RAW_SHA256:
        raise ValueError('raw image does not match the verified Linux build')
    xz = shutil.which('xz')
    if not xz:
        raise ValueError('xz is required for the original -T2 -6 container settings')
    output.mkdir(parents=True)
    source_archive = output / plan['source']['archive_name']
    reconstruct_source(plan, metadata, source, source_archive)
    image_xz = output / plan['image']['xz_filename']
    env = os.environ.copy()
    env.pop('XZ_OPT', None)
    env.pop('XZ_DEFAULTS', None)
    with image_xz.open('xb') as stream:
        subprocess.run([xz, '-T2', '-6', '--stdout', str(image)], stdout=stream, check=True, env=env)
    with lzma.open(image_xz, 'rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != RAW_SHA256:
            raise ValueError('XZ does not expand to the verified raw image')
    actual_xz, actual_source = sha(image_xz), sha(source_archive)
    container_changed = (actual_xz != plan['image']['xz_sha256'] or
                         actual_source != plan['source']['expected_archive_sha256'])
    tool_versions = {'python': sys.version.split()[0], 'zlib': zlib.ZLIB_RUNTIME_VERSION,
                     'xz': subprocess.check_output([xz, '--version'], text=True).strip()}
    if container_changed:
        publication['image']['xz_sha256'] = actual_xz
        publication['image']['xz_bytes'] = image_xz.stat().st_size
        publication['public_source_archive_sha256'] = actual_source
        publication['container_reconstruction'] = {
            'note': 'Compression containers differ; verified raw image and public source tar stream are unchanged.',
            'original_publication_manifest_sha256': PUBLICATION_SHA256,
            'original_xz_sha256': plan['image']['xz_sha256'],
            'original_source_archive_sha256': plan['source']['expected_archive_sha256'],
            'raw_image_sha256': RAW_SHA256, 'raw_public_source_tar_sha256': plan['source']['raw_tar_sha256'],
            'compressors': tool_versions}
        files['PUBLICATION-MANIFEST.json'] = (json.dumps(publication, indent=2) + '\n').encode()
        names = [line.split('  ', 1)[1] for line in files['SHA256SUMS'].decode().splitlines()]
        files['SHA256SUMS'] = ''.join((RAW_SHA256 if name.endswith('.img') else digest(files[name])) +
                                     '  ' + name + '\n' for name in names).encode()
    (output / 'PUBLICATION-MANIFEST.json').write_bytes(files['PUBLICATION-MANIFEST.json'])
    archive = output / plan['zip']['archive_name']
    with tempfile.TemporaryDirectory(prefix='reconstruct-', dir=output) as temporary:
        compressed = Path(temporary) / 'image.deflate'
        crc = deflate_image(image, compressed)
        reconstruct_zip(plan, metadata, compressed, crc, files, archive)
    verify_zip(archive, files)
    provenance = {
        'status': 'PASS', 'original_build_source_commit': ORIGINAL_COMMIT,
        'public_source_commit': PUBLIC_COMMIT, 'raw_image_sha256': RAW_SHA256,
        'retained_source_files_verified': len(plan['source']['rows']),
        'raw_public_source_tar_sha256': plan['source']['raw_tar_sha256'],
        'original_software_verification_sha256': SOFTWARE_SHA256,
        'transport_manifest_sha256': TRANSPORT_SUMS_SHA256, 'compressors': tool_versions,
        'container_sha256': {p.name: sha(p) for p in (image_xz, archive, source_archive)},
        'matches_original_containers': {
            image_xz.name: actual_xz == plan['image']['xz_sha256'],
            archive.name: sha(archive) == plan['zip']['expected_archive_sha256'],
            source_archive.name: actual_source == plan['source']['expected_archive_sha256']},
        'hardware_status': 'BLOCKED', 'hardware_qualified': False, 'normal_mode_qualified': False,
        'private_git_history_included': False, 'stock_firmware_research_payloads_included': False}
    (output / 'RECONSTRUCTION.json').write_text(json.dumps(provenance, indent=2) + '\n')
    assets = [image_xz, archive, source_archive, output / 'PUBLICATION-MANIFEST.json', output / 'RECONSTRUCTION.json']
    (output / 'DOWNLOAD-SHA256SUMS.txt').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in assets))
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()
