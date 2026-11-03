#!/usr/bin/env python3
"""Record the physical PulseAudio input independently; never play audio or control RF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import wave

SOURCE = 'alsa_input.platform-sound.stereo-fallback'
RATE = 44100

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()

def inventory(out, prefix):
    for kind in ['sources','source-outputs']:
        try:
            r=subprocess.run(['pactl','list',kind],capture_output=True,text=True,timeout=5)
            value={'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
        except (OSError,subprocess.TimeoutExpired) as e:value={'status':'BLOCKED','reason':str(e)}
        (out/(prefix+'-'+kind+'.json')).write_text(json.dumps(value,indent=2)+'\n')

def record(command, out, seconds):
    total=0; first=None; last=None; largest_gap=0; reason=None
    started=time.monotonic_ns(); deadline=time.monotonic()+seconds
    with (out/'parec.log').open('wb') as log, (out/'capture.raw').open('xb') as raw, (out/'reads.jsonl').open('x') as trace:
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=log)
        try:
            with selectors.DefaultSelector() as poll:
                poll.register(proc.stdout,selectors.EVENT_READ)
                while time.monotonic()<deadline:
                    if not poll.select(min(.2,max(0,deadline-time.monotonic()))):continue
                    block=os.read(proc.stdout.fileno(),4096)
                    if not block:
                        reason='Capture process ended before the observation deadline';break
                    at=time.monotonic_ns()
                    if first is None:first=at
                    if last is not None:largest_gap=max(largest_gap,at-last)
                    last=at;total+=len(block)
                    raw.write(block)
                    trace.write(json.dumps({'monotonic_ns':at,'bytes':len(block),'total_bytes':total})+'\n')
                    if total>RATE*2*(seconds+2):
                        reason='Unexpected unpaced input exceeds bounded recording budget';break
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
            proc.stdout.close()
    if not total:reason=reason or 'No capture samples arrived'
    if total%2:reason=reason or 'Incomplete final PCM sample; raw evidence retained'
    if total and not total%2:
        with wave.open(str(out/'capture.wav'),'wb') as w, (out/'capture.raw').open('rb') as f:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(RATE)
            for block in iter(lambda:f.read(65536),b''):w.writeframesraw(block)
    return {'status':'INCONCLUSIVE' if reason else 'COLLECTED','reason':reason,'bytes':total,'samples':total//2,
            'requested_seconds':seconds,'started_monotonic_ns':started,'first_read_monotonic_ns':first,
            'last_read_monotonic_ns':last,'largest_read_gap_ms':largest_gap/1e6,
            'scope':'Independent raw source recording; pipe delivery timestamps, not ADC or RF timestamps',
            'listening':'UNASSESSED','hardware_qualified':False}

def record_chunks(command, out, seconds, channels):
    """Preserve native channels in ten-second WAV chunks without duplicate raw files."""
    started=time.monotonic_ns();deadline=time.monotonic()+seconds
    total=0;first=last=None;largest=0;reason=None;writer=None;frames=0;index=0;pending=b''
    with (out/'parec.log').open('wb') as log, (out/'reads.jsonl').open('x') as trace:
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=log)
        try:
            with selectors.DefaultSelector() as poll:
                poll.register(proc.stdout,selectors.EVENT_READ)
                while time.monotonic()<deadline:
                    if not poll.select(min(.2,max(0,deadline-time.monotonic()))):continue
                    block=os.read(proc.stdout.fileno(),4096)
                    if not block:reason='Capture process ended early';break
                    at=time.monotonic_ns()
                    if first is None:first=at
                    if last is not None:largest=max(largest,at-last)
                    last=at;pending+=block
                    aligned=len(pending)//(channels*2)*(channels*2)
                    data,pending=pending[:aligned],pending[aligned:]
                    while data:
                        if writer is None:
                            if shutil.disk_usage(out).free<8*1024*1024:reason='Storage reserve reached';break
                            index+=1;writer=wave.open(str(out/f'capture-{index:04}.wav'),'wb')
                            writer.setnchannels(channels);writer.setsampwidth(2);writer.setframerate(RATE);frames=0
                        n=min(len(data),(10*RATE-frames)*channels*2)
                        writer.writeframesraw(data[:n]);data=data[n:];total+=n;frames+=n//(channels*2)
                        if frames==10*RATE:writer.close();writer=None
                    trace.write(json.dumps({'monotonic_ns':at,'total_bytes':total,'chunk':index})+'\n')
                    if reason:break
                    if total>RATE*channels*2*(seconds+2):reason='Unpaced capture';break
        finally:
            if writer:writer.close()
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
            proc.stdout.close()
    if pending:reason=reason or 'Partial final PCM frame'
    if not total:reason=reason or 'No capture data'
    return {'status':'INCONCLUSIVE' if reason else 'COLLECTED','reason':reason,'bytes':total,
        'samples':total//(channels*2),'channels':channels,'chunks':index,'requested_seconds':seconds,
        'started_monotonic_ns':started,'first_read_monotonic_ns':first,'last_read_monotonic_ns':last,
        'largest_read_gap_ms':largest/1e6,'channel_independence':'UNASSESSED: mixer may already combine channels',
        'listening':'UNASSESSED','hardware_qualified':False}

def preflight(expected):
    for command in ['parec','pactl']:
        if not shutil.which(command):raise ValueError('Missing '+command)
    path=Path('/usr/sbin/x6100_gui')
    if not path.is_file() or sha(path)!=expected:raise ValueError('Installed GUI hash differs from selected candidate')
    r=subprocess.run(['pactl','list','short','sources'],capture_output=True,text=True,timeout=5)
    if r.returncode or not any(SOURCE in line.split() for line in r.stdout.splitlines()):
        raise ValueError('Expected physical PulseAudio source is unavailable')
    if shutil.disk_usage('/tmp').free<32*1024*1024:raise ValueError('Need 32 MiB free in /tmp')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gui-sha256',required=True);p.add_argument('--check-only',action='store_true')
    p.add_argument('--seconds',type=int,default=60);p.add_argument('--channels',type=int,choices=(1,2),default=1);p.add_argument('--chunks',action='store_true');a=p.parse_args()
    if not 1<=a.seconds<=600:raise ValueError('Observation must be 1–600 seconds')
    try:preflight(a.gui_sha256)
    except (ValueError,OSError,subprocess.TimeoutExpired) as e:
        print(json.dumps({'status':'BLOCKED','reason':str(e)}));return 1
    if a.check_only:print('PASS: GUI hash, capture utility, source and storage');return 0
    out=Path('/tmp')/('freedv-capture-source-watch-'+str(time.time_ns()));out.mkdir(mode=0o700)
    command=['parec','--record','--raw','--format=s16le','--rate=44100','--channels='+str(a.channels),
             '--latency-msec=100','--process-time-msec=10','--device='+SOURCE,
             '--client-name=FreeDV independent capture check','--stream-name=FreeDV capture reference']
    (out/'identity.json').write_text(json.dumps({'gui_sha256':a.gui_sha256,'source':SOURCE,'command':command,
        'seconds':a.seconds,'scope':'Additional recording stream; no RF, playback, route or mixer commands'},indent=2)+'\n')
    inventory(out,'before')
    try:result=record_chunks(command,out,a.seconds,a.channels) if a.chunks or a.channels==2 else record(command,out,a.seconds)
    except (OSError,subprocess.SubprocessError) as e:result={'status':'INCONCLUSIVE','reason':str(e)}
    inventory(out,'after')
    (out/'complete.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.name+'\n' for f in sorted(out.iterdir()) if f.is_file() and f.name!='SHA256SUMS'))
    print(json.dumps(result));return 0 if result['status']=='COLLECTED' else 1

if __name__=='__main__':raise SystemExit(main())
