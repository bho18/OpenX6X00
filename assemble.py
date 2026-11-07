from pathlib import Path
import hashlib,json
src=Path('assets');out=Path('release');out.mkdir()
checks={}
for line in (src/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1)
 if name in checks or '/' in name or name in ('','.','..'):raise ValueError('Invalid checksum entry')
 checks[name]=digest
 target=out/name
 pieces=sorted(src.glob(name+'.part*')) or [src/name]
 with target.open('wb') as stream:
  for p in pieces:
   with p.open('rb') as piece:
    while chunk:=piece.read(1024*1024):stream.write(chunk)
 with target.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
 if actual!=digest:raise ValueError('Cache asset hash mismatch: '+name)
(out/'SHA256SUMS').write_bytes((src/'SHA256SUMS').read_bytes())
origin=json.loads((out/'ORIGIN.json').read_text())
if origin['sha256']!='4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453':raise ValueError('Unexpected model')
if (out/origin['filename']).stat().st_size!=183514217:raise ValueError('Wrong model size')
if set(checks)!={origin['filename'],'ORIGIN.json','COPYING'}:raise ValueError('Unexpected cache asset set')
print('PASS: byte-identical pinned public model reassembled and SHA-verified')
