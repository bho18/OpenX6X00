#!/usr/bin/env python3
"""Package the already verified candidate; never rebuild or alter its image."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import shutil
import zipfile

IMAGE = 'x6200-persistent-voice-test.img'
XZ_SHA = '9f1cf21ddc48e62aa94dd439fcc8d5f84db8d277dcadc19a0ce6b7ed25287aa9'
IMAGE_SHA = '6de54712b899d5faae35b37fa7dab7bc8157d1bbde20b5d4c25d1af66d4399b4'
IMAGE_BYTES = 571473920


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((source / 'download-complete.json').read_text())
    require(manifest['complete'] and not manifest['hardware_qualified'], 'Wrong candidate status')
    require(manifest['compressed_image_sha256'] == XZ_SHA, 'Wrong image identity')
    require([s['name'] for s in manifest['segments']] ==
            [f'{IMAGE}.xz.part{i:02}' for i in range(1, 6)], 'Wrong segment list')
    compressed = output / (IMAGE + '.xz')
    with compressed.open('xb') as target:
        for segment in manifest['segments']:
            path = source / segment['name']
            require(path.stat().st_size == segment['bytes'] and digest(path) == segment['sha256'],
                    'Invalid image segment: ' + path.name)
            with path.open('rb') as stream:
                shutil.copyfileobj(stream, target)
    require(compressed.stat().st_size == manifest['image_bytes'] and digest(compressed) == XZ_SHA,
            'Assembled compressed image mismatch')
    raw = output / IMAGE
    with lzma.open(compressed, 'rb') as stream, raw.open('xb') as target:
        shutil.copyfileobj(stream, target)
    require(raw.stat().st_size == IMAGE_BYTES and digest(raw) == IMAGE_SHA,
            'Uncompressed image mismatch')

    # Carry the existing source, evidence and operator tools into one ordinary
    # ZIP. The image is one .img file; no parts or assembly helper are included.
    payload = {}
    for line in (source / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        if name == IMAGE + '.xz':
            require(expected == XZ_SHA, 'Checksum list has another image')
            continue
        path = source / name
        require(path.is_file() and digest(path) == expected, 'Payload checksum mismatch: ' + name)
        payload[name] = path
    payload[IMAGE] = raw
    sums = ''.join(f'{digest(path)}  {name}\n' for name, path in sorted(payload.items()))
    archive = output / 'x6200-persistent-voice-test.zip'
    prefix = 'freedv-persistent-test-20261102/'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, path in sorted(payload.items()):
            z.write(path, prefix + name)
        z.writestr(prefix + 'SHA256SUMS', sums)
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None, 'ZIP CRC failure')
        images = [n for n in z.namelist() if n.endswith('.img')]
        require(images == [prefix + IMAGE], 'Expected exactly one flashable image')
        require(not any('.part0' in n or 'Assemble-Image' in n for n in z.namelist()),
                'Split-image files must not be in the delivery ZIP')
        with z.open(prefix + IMAGE) as stream:
            h = hashlib.sha256()
            for data in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(data)
        require(h.hexdigest() == IMAGE_SHA, 'Image inside ZIP mismatch')
    shutil.copyfile(source / 'START-HERE.md', output / 'START-HERE.md')
    record = {
        'status': 'PASS', 'hardware_qualified': False,
        'source_commit': manifest['source_commit'],
        'image_file': IMAGE, 'image_bytes': IMAGE_BYTES, 'image_sha256': IMAGE_SHA,
        'compressed_image_sha256': XZ_SHA,
        'zip_file': archive.name, 'zip_bytes': archive.stat().st_size,
        'zip_sha256': digest(archive), 'zip_crc': 'PASS',
        'image_in_zip_hash_verified': True, 'firmware_changed': False,
        'payload_files': len(payload),
    }
    (output / 'delivery-verification.json').write_text(json.dumps(record, indent=2) + '\n')
    assets = [archive, compressed, output / 'START-HERE.md', output / 'delivery-verification.json']
    (output / 'DOWNLOAD-SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in assets))
    print(json.dumps(record, indent=2), flush=True)


if __name__ == '__main__':
    main()
