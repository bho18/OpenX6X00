#!/usr/bin/env python3
"""Read-only endpoint inventory and historical collection; old route staging is withdrawn."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from radio_retest import collect, assess_collected, command, sha

CONTROL=Path('/tmp/freedv-control')
STATUS_MAX_AGE_MS=1500
WITHDRAWN = ('The recorder/gain/MODEM trial workflow is withdrawn. Collect existing evidence or run '
             'read-only Preflight for receive-only endpoint/PTT availability mapping. Native TX remains '
             'blocked until the measured board mapping, raw PTT, applied-route acknowledgement and '
             'independent native lease interfaces exist; see native-half-duplex-qualification.md.')

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
    inventory={name:command(args) for name,args in {
        'kernel':['uname','-a'],'audio':['pactl','list','sources'],
        'playback':['pactl','list','sinks'],
        'mixer':['amixer','contents'],'inputs':['cat','/proc/bus/input/devices'],
        'sound':['cat','/proc/asound/pcm'],'sound_cards':['cat','/proc/asound/cards'],
        'processes':['ps']}.items()}
    missing=[name for name,result in inventory.items()
             if result.get('exit')!=0 or not isinstance(result.get('stdout'),str) or not result['stdout'].strip()]
    return {'status':'INVENTORY_INCOMPLETE' if missing else 'INVENTORIED',
            'scope':'read_only_receive_endpoint_inventory',
            'inventory_complete':not missing,'missing_observations':missing,
            'firmware_commit':identity['firmware_commit'],'gui_sha256':expected,
            'inventory':inventory,'hand_ptt_independent':'UNSUPPORTED by pinned API',
            'hardware_qualified':False,'native_voice_available':False,
            'legacy_route_staging':'WITHDRAWN','route_requested':False,
            'effective_route_state':'UNKNOWN','physical_endpoint_mapping':'UNQUALIFIED',
            'next':'Receive-only endpoint/PTT availability mapping; this inventory grants no TX'}

def read_status():
    status=json.loads((CONTROL/'status.json').read_text())
    age=time.monotonic()*1000-status.get('monotonic_ms',-1)
    if age < -100 or age > STATUS_MAX_AGE_MS:
        raise ValueError('Diagnostic status is stale or from an incompatible GUI; reopen FreeDV test')
    return status

def stage(case,repeat,manifest=None,fixture_sha256=None):
    # Keep the callable/CLI for old automation, but reject before touching the
    # GUI control directory. Historic evidence collection remains supported.
    raise ValueError(WITHDRAWN)

def next_case(assessment):
    cases=assessment.get('cases',[])
    if assessment.get('issues'):
        return {'status':'INCONCLUSIVE','next':'Resolve evidence completeness or identity issues before another case','issues':assessment['issues']}
    if not cases:return {'status':'BLOCKED','next':'receive-only endpoint/PTT availability mapping',
                         'reason':'No completed captures; legacy route staging is withdrawn'}
    latest=cases[-1];voice=latest.get('voice_assessment',{})
    if voice.get('input_activity',{}).get('status') in ('NEAR_CONSTANT','BRIEF_TRANSIENTS_ONLY'):
        return {'status':'FAIL','next':'inspect existing source PCM and control trace against measured board endpoints and the lower-layer contract; no new route/gain trial'}
    return {'status':'INCONCLUSIVE','next':'listening assessment of input, local decode and RF decode',
            'reason':'Activity and mechanics do not establish speech intelligibility'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['preflight','stage','status','events','abort','collect','assess'])
    p.add_argument('--manifest',type=Path);p.add_argument('--case',help=argparse.SUPPRESS)
    p.add_argument('--repeat-five',action='store_true',help=argparse.SUPPRESS);p.add_argument('--output',type=Path)
    p.add_argument('--fixture-sha256')
    a=p.parse_args()
    try:
        if a.operation=='stage':raise ValueError(WITHDRAWN)
        manifest=json.loads(a.manifest.read_text()) if a.manifest else None
        if a.fixture_sha256 and (len(a.fixture_sha256)!=64 or any(c not in '0123456789abcdef' for c in a.fixture_sha256)):
            raise ValueError('Fixture SHA256 must contain 64 lowercase hex digits')
        if a.operation=='preflight':
            if not a.manifest:raise ValueError('Candidate manifest is required')
            checked=preflight(manifest)
        if a.operation=='preflight':result=checked
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
