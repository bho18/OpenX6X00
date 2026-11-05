#!/usr/bin/env python3
"""Package a separately cross-compiled ARM helper with bounded test scripts."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pcm-helper', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    header = args.pcm_helper.read_bytes()[:20]
    if header[:6] != b'\x7fELF\x01\x01' or header[18:20] != b'\x28\x00':
        parser.error('An ARM32 little-endian ELF helper is required.')
    here = Path(__file__).resolve().parent
    files = {name: here / name for name in ('usb_headset_validate.py', 'usb_headset_inventory.py',
             'Invoke-UsbHeadsetValidation.ps1', 'usb_headset_pcm.c')}
    files['usb_headset_pcm'] = args.pcm_helper
    manifest = {'schema': 'x6200.usb-headset-validation-kit.v1',
                'version': '20261105.1', 'pcm_helper_sha256': digest(args.pcm_helper),
                'files': {name: digest(path) for name, path in files.items()},
                'rf_commands': False, 'image_required': 'freedv-native-development-20261105.2',
                'scope': 'USB PCM duplex and recorded actual-codec round trip; listening required.'}
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        archive.writestr('validation-manifest.json', json.dumps(manifest, indent=2) + '\n')
    print(digest(args.output) + '  ' + args.output.name)


if __name__ == '__main__': main()
