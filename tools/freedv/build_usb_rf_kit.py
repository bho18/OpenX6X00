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
    if (type(image.get('usb_startup_contract_version')) is not int or image['usb_startup_contract_version'] not in (3, 4) or
            image.get('on_device_readiness_required') is not True):
        parser.error('A verified startup contract 3 diagnostic or 4 operational image with mandatory readiness is required')
    if type(image.get('usb_output_contract_version')) is not int or image['usb_output_contract_version'] != 1:
        parser.error('The verified USB output completion contract version 1 image is required')
    if type(image.get('usb_capture_open_contract_version')) is not int or image['usb_capture_open_contract_version']!=1:
        parser.error('Verified capture opening contract version1 is required')
    if image['usb_startup_contract_version']==4 and (type(image.get('usb_refinement_contract_version')) is not int or image['usb_refinement_contract_version']!=2):
        parser.error('Operational images require verified USB refinement contract2')
    if not isinstance(image.get('build_id'), str) or not image['build_id']:
        parser.error('A build identity is required for the session/completion contract')
    here=Path(__file__).resolve().parent
    files={n:here/n for n in ['Invoke-UsbRfEvidence.ps1','usb_rf_evidence.py','usb_output_contract.py','usb_capture_open_contract.py','usb_refinement_contract.py','usb_half_duplex.py','usb_headset_validate.py','usb_headset_inventory.py']}
    files['START-HERE.md']=here.parents[1]/'docs/freedv/usb-rf-end-to-end.md'
    manifest={k:image[k] for k in ['gui_sha256','runner_sha256','rade_sha256','image_sha256','firmware_commit','build_id']}
    manifest.update(schema='x6200.usb-rf-kit.v2',usb_startup_contract_version=image['usb_startup_contract_version'],on_device_readiness_required=True,
                    usb_output_contract_version=1,usb_capture_open_contract_version=1,
                    files={n:sha(p) for n,p in files.items()},rf_commands=False)
    if image['usb_startup_contract_version']==4:manifest['usb_refinement_contract_version']=2
    with zipfile.ZipFile(args.output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for n,p in files.items():archive.write(p,n)
        archive.writestr('rf-test-manifest.json',json.dumps(manifest,indent=2)+'\n')
    print(sha(args.output)+'  '+args.output.name)


if __name__=='__main__': main()
