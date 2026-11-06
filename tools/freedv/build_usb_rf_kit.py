#!/usr/bin/env python3
"""Bind RF evidence helpers to an exact verified USB development image."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    image=json.loads(args.image_manifest.read_text())
    if image.get('usb_rf_test_ready') is not True or image.get('software_gates_passed') is not True:
        parser.error('An explicitly verified USB RF development image is required')
    if image.get('usb_startup_contract_version') != 2 or image.get('on_device_readiness_required') is not True:
        parser.error('The USB startup v2 image and mandatory on-device RF-disabled readiness gate are required')
    here=Path(__file__).resolve().parent
    files={n:here/n for n in ['Invoke-UsbRfEvidence.ps1','usb_rf_evidence.py','usb_half_duplex.py','usb_headset_validate.py','usb_headset_inventory.py']}
    files['START-HERE.md']=here.parents[1]/'docs/freedv/usb-rf-end-to-end.md'
    manifest={k:image[k] for k in ['gui_sha256','runner_sha256','rade_sha256','image_sha256','firmware_commit']}
    manifest.update(schema='x6200.usb-rf-kit.v1',usb_startup_contract_version=2,on_device_readiness_required=True,
                    files={n:sha(p) for n,p in files.items()},rf_commands=False)
    with zipfile.ZipFile(args.output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for n,p in files.items():archive.write(p,n)
        archive.writestr('rf-test-manifest.json',json.dumps(manifest,indent=2)+'\n')
    print(sha(args.output)+'  '+args.output.name)


if __name__=='__main__': main()
