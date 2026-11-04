#!/usr/bin/env python3
"""Release-internal transport only. Users receive one verified flashable image."""
import argparse,hashlib,json,shutil,subprocess,tarfile,zipfile,base64
from pathlib import Path

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()

def normal_tar(info):
 info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
 info.mode=0o755 if info.isdir() or info.mode&0o111 else 0o644
 return info

def zip_info(name):
 info=zipfile.ZipInfo(name,date_time=(2026,11,4,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
 info.external_attr=0o100644<<16;return info

def add_zip(z,path,name):
 with path.open('rb') as src,z.open(zip_info(name),'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst)

def main():
 p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--base-source',type=Path,required=True);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 a.output.mkdir(parents=True,exist_ok=False)
 spec=json.loads((a.input/'delivery-inputs.json').read_text())
 for name,digest in spec['files'].items():
  if sha(a.input/name)!=digest:raise ValueError('input mismatch: '+name)
 if sha(a.base)!=spec['base_image_sha256']:raise ValueError('baseline mismatch')
 delta=a.output/'image.patch.zst'
 with delta.open('xb') as f:
  for name in spec['delta_parts']:f.write(base64.b64decode((a.input/name).read_bytes(),validate=True))
 image=a.output/'x6200-freedv-route-review.img'
 subprocess.run(['zstd','-d','--patch-from='+str(a.base),'-M2048',str(delta),'-o',str(image)],check=True)
 delta.unlink()
 manifest=json.loads((a.input/'candidate-manifest.json').read_text())
 if sha(image)!=manifest['image_sha256'] or image.stat().st_size!=spec['image_bytes']:raise ValueError('reconstructed image mismatch')
 source=a.output/'source';source.mkdir()
 with tarfile.open(a.base_source) as tar:
  for m in tar:
   path=source/m.name
   if not path.resolve().is_relative_to(source.resolve()) or not (m.isdir() or m.isfile()):raise ValueError('unsafe source archive')
   if m.isdir():path.mkdir(parents=True,exist_ok=True)
   else:
    path.parent.mkdir(parents=True,exist_ok=True)
    with tar.extractfile(m) as src,path.open('wb') as dest:shutil.copyfileobj(src,dest)
    path.chmod(m.mode&0o777)
 tree=source/'OpenX6X00'
 for name in spec['source_updates']:
  target=tree/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(a.input/'source-updates'/name,target)
 verification=json.loads((a.input/'software-verification.json').read_text())
 for name,digest in verification['source_files'].items():
  if sha(tree/name)!=digest:raise ValueError('source tree mismatch: '+name)
 source_archive=a.output/'candidate-source.tar.xz'
 with tarfile.open(source_archive,'w:xz') as tar:tar.add(tree,arcname='OpenX6X00',filter=normal_tar)
 archive=a.output/'x6200-freedv-route-review.zip'
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
  add_zip(z,image,image.name);add_zip(z,source_archive,source_archive.name)
  for name in spec['include']:add_zip(z,a.input/name,name)
  z.writestr(zip_info('candidate-source.bundle'),b''.join(base64.b64decode((a.input/name).read_bytes(),validate=True) for name in spec['bundle_parts']))
 with zipfile.ZipFile(archive) as z:
  if z.testzip() is not None:raise ValueError('ZIP CRC failure')
  h=hashlib.sha256()
  with z.open(image.name) as f:
   for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
  if h.hexdigest()!=manifest['image_sha256']:raise ValueError('ZIP image mismatch')
 for name in ['START-HERE.md','candidate-manifest.json','software-verification.json']:
  shutil.copy2(a.input/name,a.output/name)
 result=dict(status='PASS',scope='Exact reconstruction of validated image; full source-file hashes; ZIP CRC and enclosed image SHA256 checked',image_sha256=manifest['image_sha256'],image_bytes=image.stat().st_size,zip_sha256=sha(archive),zip_bytes=archive.stat().st_size,hardware_qualified=False)
 (a.output/'delivery-verification.json').write_text(json.dumps(result,indent=2)+'\n')
 (a.output/'DOWNLOAD-SHA256SUMS.txt').write_text(''.join(sha(f)+'  '+f.name+'\n' for f in sorted(a.output.iterdir()) if f.is_file()))
 print(json.dumps(result))

if __name__=='__main__':main()
