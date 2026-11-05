#!/usr/bin/env python3
"""Reconstruct the previously verified image; never run or change its contents."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-zip', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads((args.input / 'image-transport.json').read_text())
    if sha(args.baseline_zip) != spec['baseline_archive_sha256']:
        raise ValueError('Published baseline archive SHA256 mismatch')
    args.work.mkdir(parents=True, exist_ok=False)
    baseline = args.work / 'baseline.img'
    with zipfile.ZipFile(args.baseline_zip) as archive:
        matches = [entry for entry in archive.infolist()
                   if Path(entry.filename).name == spec['baseline_member_basename']]
        if len(matches) != 1 or matches[0].file_size != spec['image_size_bytes']:
            raise ValueError('Baseline image member missing, duplicated or wrong size')
        with archive.open(matches[0]) as source, baseline.open('xb') as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
    if sha(baseline) != spec['baseline_image_sha256']:
        raise ValueError('Extracted baseline image SHA256 mismatch')
    patch = args.work / 'image.patch.zst'
    with patch.open('xb') as output:
        for part in spec['parts']:
            path = args.input / part['path']
            if path.stat().st_size != part['encoded_bytes'] or sha(path) != part['sha256']:
                raise ValueError('Transport part mismatch: ' + part['path'])
            output.write(base64.b64decode(path.read_bytes(), validate=True))
    if sha(patch) != spec['delta_sha256']:
        raise ValueError('Decoded delta SHA256 mismatch')
    image = args.work / 'x6200-native-development.img'
    subprocess.run(['zstd', '-d', '--patch-from=' + str(baseline), '-M2048',
                    str(patch), '-o', str(image)], check=True)
    if image.stat().st_size != spec['image_size_bytes'] or sha(image) != spec['image_sha256']:
        raise ValueError('Reconstructed image differs from the verified build')
    (args.work / 'reconstruction-verification.json').write_text(json.dumps({
        'status': 'PASS', 'image_sha256': spec['image_sha256'],
        'image_size_bytes': spec['image_size_bytes'], 'hardware_qualified': False,
        'scope': 'Exact byte reconstruction of the existing verified Linux development image'
    }, indent=2) + '\n')
    print('PASS: reconstructed image is byte-identical to the verified build')

if __name__ == '__main__':
    main()
