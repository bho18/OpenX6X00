#!/usr/bin/env python3
"""Summary-only real codec/PA contract; simulated radio, no hardware or RF."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from usb_test_support import ProcessSupervisor, PulseTestRuntime, require


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True,type=Path);args=parser.parse_args()
    for tool in ('pulseaudio','pactl','pacat'):require(shutil.which(tool),tool+' is required')
    modules=os.environ.get('TEST_PULSE_MODULES')
    if not modules:
        found=subprocess.run(['pkg-config','--variable=modlibexecdir','libpulse'],capture_output=True,text=True,timeout=3)
        candidate=found.stdout.strip()
        if found.returncode==0 and candidate and Path(candidate).is_dir():modules=candidate
    for case in ('operational-summary','operational-summary-abort'):
        with tempfile.TemporaryDirectory(prefix='usb-summary-capture-') as temp:
            root=Path(temp);runtime=PulseTestRuntime(root);result=root/'result';result.mkdir()
            env=dict(os.environ,**runtime.environment())
            if modules:env['LD_LIBRARY_PATH']=modules+':'+env.get('LD_LIBRARY_PATH','')
            supervisor=ProcessSupervisor(root,env)
            try:
                config=root/'pulse.pa';config.write_text('.fail\n'+f'load-module module-native-protocol-unix socket={runtime.socket} auth-anonymous=1\n'+
                    'load-module module-null-sink sink_name=mic_fixture rate=16000 channels=1 format=s16le norewinds=1\n'
                    'load-module module-remap-source source_name=alsa_input.usb-fixture master=mic_fixture.monitor rate=16000 channels=1\n'
                    'load-module module-null-sink sink_name=rf_fixture rate=44100 channels=2 format=s16le norewinds=1\n'
                    'load-module module-remap-source source_name=radio_source_fixture master=rf_fixture.monitor rate=44100 channels=2\n'
                    'load-module module-null-sink sink_name=alsa_output.usb-fixture rate=48000 channels=2 format=s24le norewinds=1\n'
                    'load-module module-null-sink sink_name=radio_sink_fixture rate=44100 channels=2 format=s16le norewinds=1\n')
                pulse=['pulseaudio','-nF',str(config),'--daemonize=no','--use-pid-file=no','--exit-idle-time=-1']
                if modules:pulse+=['--dl-search-path',modules]
                supervisor.start('pulse',pulse)
                until=time.monotonic()+4
                while time.monotonic()<until:
                    supervisor.require_running()
                    if runtime.socket.exists():
                        probe=subprocess.run(['pactl','info'],env=env,capture_output=True,timeout=1)
                        if not probe.returncode:break
                    time.sleep(.02)
                else:raise RuntimeError((root/'pulse.log').read_text())
                for name,rate,channels in (('mic_fixture',16000,1),('rf_fixture',44100,2)):
                    supervisor.start(name,['pacat','--playback','--raw','--format=s16le','--rate='+str(rate),'--channels='+str(channels),'--device='+name,'/dev/zero'])
                time.sleep(.15);supervisor.require_running()
                controller=supervisor.start('controller',[str(args.binary.resolve()),'.','--'+case],cwd=result)
                require(controller.wait(timeout=35)==0,(root/'controller.log').read_text())
                current=json.loads((result/'freedv-usb-current.json').read_text())
                completion=json.loads((result/current['completion_basename']).read_text())
                readiness=json.loads((result/current['readiness_basename']).read_text())
                harness=json.loads((result/'harness-result.json').read_text())
                require(readiness['status']=='PASS' and readiness['captures_fresh'] and readiness['buffers_ready'],'Mandatory readiness proof missing')
                require(completion['capture_level']=='summary' and completion['pcm_recorded'] is False,'Summary metadata missing')
                require(completion['trace_complete'] and not completion['trace_history_truncated'] and completion['rf_clear_confirmed'],'Final control/trace proof missing')
                require(completion['capture_open_completed_intervals']>=1 and completion['playback_tail_completed_intervals']>=1,'Summary lost completed capture/tail counters')
                require(harness['turns']==1 and harness['eoo']==1 and harness['submitted_mic']>48000,'Completed turn disappeared')
                require(not list(result.glob('*.raw')),'Summary recorded PCM')
                traces=list(result.glob('freedv-usb-session.jsonl'));require(len(traces)==1,'Session metadata missing')
                rows=[json.loads(row) for row in traces[0].read_text().splitlines()]
                require([row['kind'] for row in rows]==['session','startup-readiness','sample'],'Summary retained verbose event/PCM audit rows: '+str([row['kind'] for row in rows]))
                require(rows[0]['capture_level']=='summary' and rows[-1]['fault']==completion['fault'],'Final sample does not match completion')
                if case.endswith('-abort'):
                    require(completion['fault']==1 and harness['first_fault_origin']=='process-deadline-abort','Worker did not independently observe deadline publication')
                    fault=json.loads((result/'freedv-usb-session-fault.json').read_text())
                    recent=json.loads((result/'freedv-usb-session-recent-fault.json').read_text())
                    require(fault['first_fault']['origin']=='process-deadline-abort' and recent['frozen_at_first_fault'],'Bounded fault evidence missing')
                else:require(completion['fault']==0,'Graceful summary exit faulted')
                print('PASS: real codec/PA '+case+' without PCM; mandatory readiness, completion, capture/tail counters and fault evidence retained',flush=True)
            finally:
                supervisor.close();runtime.close()


if __name__=='__main__':main()
