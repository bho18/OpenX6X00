#!/usr/bin/env python3
"""Require GitHub's stored asset digests to match every intended release file."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

NAMES = {
    'x6200-native-development-windows.zip',
    'x6200-native-development.img.xz',
    'x6200-native-development-public-source.tar.xz',
    'PUBLICATION-MANIFEST.json',
    'RECONSTRUCTION.json',
    'DOWNLOAD-SHA256SUMS.txt',
}
release = json.loads(subprocess.check_output([
    'gh', 'api', 'repos/bho18/OpenX6X00/releases/434793318'
]))
if (release['id'] != 434793318 or release['draft'] is not True or
        release['tag_name'] != 'freedv-native-development-20261105' or
        release['target_commitish'] != '1a2aad375dadc8a14f2b12c6c939b369cc30004d'):
    raise ValueError('Release identity, draft state or source target changed')
assets = {asset['name']: asset for asset in release['assets']}
if set(assets) != NAMES or len(release['assets']) != len(NAMES):
    raise ValueError('Missing, duplicate or unexpected release assets')
for name in sorted(NAMES):
    path = Path(sys.argv[1]) / name
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    asset = assets[name]
    if asset['state'] != 'uploaded' or asset['size'] != path.stat().st_size:
        raise ValueError('GitHub asset upload state/size mismatch: ' + name)
    if asset.get('digest') != 'sha256:' + digest:
        raise ValueError('GitHub asset digest mismatch: ' + name)
    print('PASS: GitHub stored digest ' + name)
