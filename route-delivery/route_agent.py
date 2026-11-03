#!/usr/bin/env python3
"""Local diagnostic control. No remote Acquire, Arm, Run, tuning or register writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from radio_retest import collect, assess_collected, command, sha

CONTROL=Path('/tmp/freedv-control')
CASES={'record-hand-zero':(23,4),'record-hand-saved':(24,4),
       'record-ordinary-zero':(25,4),'record-ordinary-saved':(26,4),
       'modem-prekey-gain':(27,6),'modem-waveform':(20,6),'live-speech':(21,6),'receive':(22,4)}

def atomic(path, value):
    temp=path.with_name(path.name+'.new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:f.write(value)
    os.replace(temp,path)

def preflight(manifest):
    expected=manifest['gui_sha256']
    installed=Path('/usr/share/x6100/freedv/build-manifest.json')
    if not installed.is_file() or sha(Path('/usr/sbin/x6100_gui'))!=expected:
        raise ValueError('Installed GUI does not match candidate manifest')
    identity=json.loads(installed.read_text())
    if identity['firmware_commit']!=manifest['firmware_commit'] or identity['gui_sha256']!=expected:
        raise ValueError('Installed source/build identities do not match')
    if shutil.disk_usage('/tmp').free<96*1024*1024:raise ValueError('Need 96 MiB free for bounded capture and collection')
    inventory={name:command(args) for name,args in {
        'kernel':['uname','-a'],'audio':['pactl','list','sources'],
        'mixer':['amixer','contents'],'inputs':['cat','/proc/bus/input/devices'],
        'sound':['cat','/proc/asound/pcm'],'processes':['ps']}.items()}
    if inventory['audio'].get('exit')!=0 or inventory['mixer'].get('exit')!=0:
        raise ValueError('Physical audio source/mixer inventory unavailable')
    return {'status':'READY','firmware_commit':identity['firmware_commit'],'gui_sha256':expected,
            'inventory':inventory,'hand_ptt_independent':'UNSUPPORTED by pinned API',
            'hardware_qualified':False}

def stage(case,repeat):
    if case not in CASES:raise ValueError('Unknown named case')
    c,route=CASES[case]
    if repeat and c not in range(23,27):raise ValueError('Only unkeyed recording cases may repeat')
    if not CONTROL.is_dir() or not (CONTROL/'status.json').is_file():
        raise ValueError('Open FreeDV test locally before staging')
    status=json.loads((CONTROL/'status.json').read_text())
    if status['state'] not in (0,1):raise ValueError('Finish current case and restoration before staging')
    atomic(CONTROL/'stage',f'1 {c} {route} 0 0 0\n')
    if repeat:atomic(CONTROL/'repeat-five','5\n')
    else:(CONTROL/'repeat-five').unlink(missing_ok=True)
    return {'status':'STAGED','case':case,'repeat':5 if repeat else 1,
        'local_action':'Run' if c in range(23,27) else 'Arm then Run' if c in (20,21,27) else 'Run',
        'ptt':'Both physical PTT controls released throughout', 'rf_started':False}

def next_case(assessment):
    cases=assessment.get('cases',[])
    if not cases:return {'status':'BLOCKED','next':'record-ordinary-zero','reason':'No completed captures'}
    latest=cases[-1];voice=latest.get('voice_assessment',{})
    if voice.get('input_activity',{}).get('status') in ('NEAR_CONSTANT','BRIEF_TRANSIENTS_ONLY'):
        return {'status':'FAIL','next':'inspect source PCM and control-trace; compare recording conventions before another RF case'}
    return {'status':'INCONCLUSIVE','next':'listening assessment of input, local decode and RF decode',
            'reason':'Activity and mechanics do not establish speech intelligibility'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['preflight','stage','status','events','abort','collect','assess'])
    p.add_argument('--manifest',type=Path);p.add_argument('--case',choices=CASES)
    p.add_argument('--repeat-five',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args()
    try:
        if a.operation in ('preflight','stage'):
            if not a.manifest:raise ValueError('Candidate manifest is required')
            checked=preflight(json.loads(a.manifest.read_text()))
        if a.operation=='preflight':result=checked
        elif a.operation=='stage':result=stage(a.case,a.repeat_five)
        elif a.operation=='status':result=json.loads((CONTROL/'status.json').read_text())
        elif a.operation=='abort':
            if not CONTROL.is_dir():raise ValueError('Diagnostic interface is not open')
            atomic(CONTROL/'abort','abort\n');result={'status':'ABORT_REQUESTED'}
        elif a.operation=='events':
            status=json.loads((CONTROL/'status.json').read_text());folder=Path(status['directory'])
            if not str(folder).startswith('/tmp/freedv-native-'):raise ValueError('No diagnostic session')
            result={'status':'OBSERVED','events':(folder/'events.jsonl').read_text()[-65536:]}
        elif a.operation=='collect':
            if not a.output:raise ValueError('--output is required')
            a.output.mkdir(parents=True,exist_ok=True);result=collect(a.output)
            atomic(a.output/'result.json',json.dumps(result,indent=2)+'\n')
            files={str(x.relative_to(a.output)):sha(x) for x in a.output.rglob('*') if x.is_file() and x.name!='transfer-manifest.json'}
            atomic(a.output/'transfer-manifest.json',json.dumps(files,indent=2)+'\n')
        else:
            if not a.output:raise ValueError('--output is required')
            assessment=assess_collected(a.output);result={'assessment':assessment,'decision':next_case(assessment)}
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        print(json.dumps({'status':'BLOCKED','reason':str(e)}));return 1

if __name__=='__main__':raise SystemExit(main())
