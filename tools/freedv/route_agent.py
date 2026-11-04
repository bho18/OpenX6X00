#!/usr/bin/env python3
"""Local diagnostic control. No remote Acquire, Arm, Run, tuning or register writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import time
from radio_retest import collect, assess_collected, command, sha

CONTROL=Path('/tmp/freedv-control')
STAGES=Path('/tmp/freedv-agent-stages')
STATUS_MAX_AGE_MS=1500
CASES={'record-hand-zero':(23,4),'record-hand-saved':(24,4),
       'record-ordinary-zero':(25,4),'record-ordinary-saved':(26,4),
       'record-then-modem':(28,6),'modem-prekey-gain':(27,6),'modem-waveform':(20,6),'live-speech':(21,6),'receive':(22,4)}

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
    fixture=Path('/usr/share/x6100/freedv/fixtures/rade-continuous.raw')
    expected_fixture=manifest.get('modem_fixture_sha256')
    if not expected_fixture or identity.get('modem_fixture_sha256')!=expected_fixture or not fixture.is_file() or sha(fixture)!=expected_fixture:
        raise ValueError('Installed modem waveform does not match candidate manifest')
    if shutil.disk_usage('/tmp').free<96*1024*1024:raise ValueError('Need 96 MiB free for bounded capture and collection')
    inventory={name:command(args) for name,args in {
        'kernel':['uname','-a'],'audio':['pactl','list','sources'],
        'mixer':['amixer','contents'],'inputs':['cat','/proc/bus/input/devices'],
        'sound':['cat','/proc/asound/pcm'],'processes':['ps']}.items()}
    if inventory['audio'].get('exit')!=0 or inventory['mixer'].get('exit')!=0:
        raise ValueError('Physical audio source/mixer inventory unavailable')
    return {'status':'READY','firmware_commit':identity['firmware_commit'],'gui_sha256':expected,'modem_fixture_sha256':expected_fixture,
            'inventory':inventory,'hand_ptt_independent':'UNSUPPORTED by pinned API',
            'hardware_qualified':False}

def read_status():
    status=json.loads((CONTROL/'status.json').read_text())
    age=time.monotonic()*1000-status.get('monotonic_ms',-1)
    if age < -100 or age > STATUS_MAX_AGE_MS:
        raise ValueError('Diagnostic status is stale or from an incompatible GUI; reopen FreeDV test')
    return status

def stage(case,repeat,manifest=None,fixture_sha256=None):
    if case not in CASES:raise ValueError('Unknown named case')
    c,route=CASES[case]
    if repeat and c not in range(23,27):raise ValueError('Only unkeyed recording cases may repeat')
    if not CONTROL.is_dir() or not (CONTROL/'status.json').is_file():
        raise ValueError('Open FreeDV test locally before staging')
    status=read_status()
    if status['state'] not in (0,1):raise ValueError('Finish current case and restoration before staging')
    request_id=secrets.randbits(63) or 1
    selection={'case':c,'route':route,'microphone_index':0,'capture_channel':0,'mixer_choice':0,'repeat':5 if repeat else 1}
    # One atomic request binds repetition to this particular case. A stale
    # repeat marker can never spill over into a later RF selection.
    atomic(CONTROL/'stage',f"2 {request_id} {c} {route} 0 0 0 {selection['repeat']}\n")
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        status=read_status()
        if status.get('stage_request_id')==request_id:
            if status.get('stage_accepted') is not True:
                raise ValueError('GUI rejected staging: '+str(status.get('stage_rejection','unspecified')))
            if status.get('selected')!=selection:
                raise ValueError('GUI acknowledged different case/settings; do not run')
            break
        time.sleep(.05)
    else:raise ValueError('GUI did not acknowledge staging; no case was started')
    result={'status':'STAGED','request_id':request_id,'case':case,'selected':selection,
        'repeat':selection['repeat'],'firmware_commit':(manifest or {}).get('firmware_commit'),
        'gui_sha256':(manifest or {}).get('gui_sha256'),'fixture_sha256':fixture_sha256,
        'modem_fixture_sha256':(manifest or {}).get('modem_fixture_sha256'),
        'fixture_listening':'UNASSESSED','staged_monotonic_ms':status['monotonic_ms'],
        'local_action':'Run' if c in range(23,27) else 'Arm then Run' if c in (20,21,27,28) else 'Run',
        'ptt':'Both physical PTT controls released throughout', 'rf_started':False}
    STAGES.mkdir(mode=0o700,parents=True,exist_ok=True)
    atomic(STAGES/(str(request_id)+'.json'),json.dumps(result,indent=2)+'\n')
    return result

def next_case(assessment):
    cases=assessment.get('cases',[])
    if assessment.get('issues'):
        return {'status':'INCONCLUSIVE','next':'Resolve evidence completeness or identity issues before another case','issues':assessment['issues']}
    if not cases:return {'status':'INCONCLUSIVE','next':'record-ordinary-zero','reason':'No completed captures'}
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
    p.add_argument('--fixture-sha256')
    a=p.parse_args()
    try:
        manifest=json.loads(a.manifest.read_text()) if a.manifest else None
        if a.fixture_sha256 and (len(a.fixture_sha256)!=64 or any(c not in '0123456789abcdef' for c in a.fixture_sha256)):
            raise ValueError('Fixture SHA256 must contain 64 lowercase hex digits')
        if a.operation in ('preflight','stage'):
            if not a.manifest:raise ValueError('Candidate manifest is required')
            checked=preflight(manifest)
        if a.operation=='preflight':result=checked
        elif a.operation=='stage':result=stage(a.case,a.repeat_five,manifest,a.fixture_sha256)
        elif a.operation=='status':result=read_status()
        elif a.operation=='abort':
            if not CONTROL.is_dir():raise ValueError('Diagnostic interface is not open')
            atomic(CONTROL/'abort','abort\n');result={'status':'ABORT_REQUESTED'}
        elif a.operation=='events':
            status=read_status();folder=Path(status['directory'])
            if folder.parent!=Path('/tmp') or not folder.name.startswith('freedv-native-'):raise ValueError('No diagnostic session')
            result={'status':'OBSERVED','events':(folder/'events.jsonl').read_text()[-65536:]}
        elif a.operation=='collect':
            if not a.output:raise ValueError('--output is required')
            a.output.mkdir(parents=True,exist_ok=True);result=collect(a.output,manifest)
            atomic(a.output/'result.json',json.dumps(result,indent=2)+'\n')
            files={str(x.relative_to(a.output)):sha(x) for x in a.output.rglob('*') if x.is_file() and x.name!='transfer-manifest.json' and not x.name.endswith('.partial')}
            atomic(a.output/'transfer-manifest.json',json.dumps(files,indent=2)+'\n')
        else:
            if not a.output:raise ValueError('--output is required')
            assessment=assess_collected(a.output,manifest);result={'assessment':assessment,'decision':next_case(assessment)}
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        print(json.dumps({'status':'BLOCKED','reason':str(e)}));return 1

if __name__=='__main__':raise SystemExit(main())
