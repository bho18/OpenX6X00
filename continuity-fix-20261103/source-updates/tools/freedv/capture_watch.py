#!/usr/bin/env python3
"""Bounded read-only observation alongside locally operated RF cases. No control writes."""
import argparse, hashlib, json, subprocess, time
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gui-sha256',required=True);p.add_argument('--seconds',type=int,default=120);p.add_argument('--check-only',action='store_true')
    a=p.parse_args()
    if not 1<=a.seconds<=180:raise ValueError('Duration must be 1–180 seconds')
    digest=hashlib.sha256(Path('/usr/sbin/x6100_gui').read_bytes()).hexdigest()
    if digest!=a.gui_sha256:raise ValueError('Installed GUI does not match candidate')
    ids=[]
    for q in Path('/proc').glob('[0-9]*/comm'):
        try:
            if q.read_text().strip()=='x6100_gui':ids.append(q.parent.name)
        except OSError:continue
    if len(ids)!=1:raise ValueError('Need exactly one running GUI')
    if a.check_only:
        print('PASS: candidate GUI hash and running process');return
    pid=ids[0];out=Path('/tmp')/('freedv-capture-watch-'+str(time.time_ns()));out.mkdir(mode=0o700)
    (out/'identity.json').write_text(json.dumps({'gui_sha256':digest,'pid':pid,'seconds':a.seconds,'scope':'Read-only process scheduling and PulseAudio observations; RF operated locally'}))
    for kind in ['sources','source-outputs','sink-inputs']:
        r=subprocess.run(['pactl','list',kind],capture_output=True,text=True,timeout=5)
        (out/('before-'+kind+'.txt')).write_text(r.stdout+r.stderr)
    end=time.monotonic()+a.seconds
    with (out/'threads.jsonl').open('w',buffering=1) as f:
        while time.monotonic()<end:
            rows=[]
            for task in Path('/proc',pid,'task').glob('[0-9]*'):
                try:rows.append({'tid':task.name,'stat':(task/'stat').read_text().strip(),'wchan':(task/'wchan').read_text().strip()})
                except OSError:continue
            f.write(json.dumps({'monotonic_ms':time.monotonic_ns()//1000000,'threads':rows})+'\n')
            time.sleep(.5)
    for kind in ['sources','source-outputs','sink-inputs']:
        r=subprocess.run(['pactl','list',kind],capture_output=True,text=True,timeout=5)
        (out/('after-'+kind+'.txt')).write_text(r.stdout+r.stderr)
    (out/'complete.json').write_text(json.dumps({'status':'COLLECTED','hardware_qualified':False}))
    print(out)

if __name__=='__main__':main()
