#!/usr/bin/env python3
"""Package a verified ARM half-duplex USB helper for the existing .2 image."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--engine-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    header = args.helper.read_bytes()[:20]
    if header[:6] != b'\x7fELF\x01\x01' or header[18:20] != b'\x28\x00':
        parser.error('ARM32 little-endian ELF required')
    root = Path(__file__).resolve().parents[2]
    here = Path(__file__).resolve().parent
    files = {name: here / name for name in ['Invoke-UsbHalfDuplex.ps1', 'usb_half_duplex.py',
             'usb_half_duplex.c', 'usb_headset_inventory.py', 'usb_headset_validate.py']}
    files['usb_half_duplex'] = args.helper
    manifest = dict(schema='x6200.usb-half-duplex-kit.v1', version='20261105.1',
                    helper_sha256=sha(args.helper), engine_archive_sha256=sha(args.engine_archive),
                    engine_source_sha256=sha(root / 'src/freedv/voice_engine.c'),
                    engine_header_sha256=sha(root / 'src/freedv/voice_engine.h'),
                    files={name: sha(path) for name, path in files.items()},
                    rf_commands=False, ptt_commands=False, client_rate=48000,
                    image_required='freedv-native-development-20261105.2',
                    scope='Three live half-duplex codec turns with retained USB streams; file stands in for RF channel.')
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items(): archive.write(path, name)
        archive.writestr('half-duplex-manifest.json', json.dumps(manifest, indent=2) + '\n')
    print(sha(args.output) + '  ' + args.output.name)


if __name__ == '__main__': main()
