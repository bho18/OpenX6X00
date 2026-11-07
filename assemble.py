from pathlib import Path
import hashlib,json,zipfile
src=Path('assets');out=Path('release');out.mkdir()
for line in (src/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1)
 target=out/name
 pieces=sorted(src.glob(name+'.part*')) or [src/name]
 with target.open('wb') as f:
  for p in pieces:
   with p.open('rb') as chunk:
    while data:=chunk.read(1024*1024):f.write(data)
 with target.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==digest,name
(out/'SHA256SUMS').write_bytes((src/'SHA256SUMS').read_bytes())
m=json.loads((out/'candidate-manifest.json').read_text())
assert m['firmware_commit']=='20cf0e65fecb6459fc8c7d6d40d65b514b82a11c' and m['usb_rf_test_ready']
with zipfile.ZipFile(out/'x6200-usb-rf-windows.zip') as z:
 with z.open('x6200-usb-rf.img') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==m['image_sha256']
print('PASS: reassembled release and image hashes match the verified cloud candidate')
