#!/usr/bin/env python3
import hashlib,json,re,sys
from pathlib import Path
if sys.flags.optimize: raise RuntimeError('Optimized Python is forbidden for transport verification')
root=Path(__file__).resolve().parent
m=json.loads((root/'transport-manifest.json').read_text())
assert m['repository']=='bho18/OpenX6X00'
assert re.fullmatch('[0-9a-f]{40}',m['source_commit'])
assert re.fullmatch(r'freedv-operational-beta-[0-9]{8}\.[1-9][0-9]*',m['tag'])
expected={'candidate-manifest.json','software-verification.json','candidate-source.bundle','candidate-source.tar.xz','image-abi.json','START-HERE.md','usb-rf-test-kit.zip','x6200-usb-rf-windows.zip','SHA256SUMS'}
assert set(m['assets'])==expected
output=Path(__import__('sys').argv[1]);output.mkdir(parents=True,exist_ok=False)
for name,entry in m['assets'].items():
    h=hashlib.sha256();count=0
    with (output/name).open('xb') as target:
        for part in entry['parts']:
            assert re.fullmatch(r'part-[0-9]{4}',part['name'])
            path=root/'assets'/part['name'];data=path.read_bytes()
            assert len(data)<=48*1024*1024 and len(data)==part['bytes']
            assert hashlib.sha256(data).hexdigest()==part['sha256']
            target.write(data);h.update(data);count+=len(data)
    assert count==entry['bytes'] and h.hexdigest()==entry['sha256']
notes=(root/'release-notes.md').read_bytes()
assert hashlib.sha256(notes).hexdigest()==m['notes_sha256']
print('Reconstructed all nine exact verified assets')
