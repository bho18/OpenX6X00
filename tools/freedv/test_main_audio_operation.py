#!/usr/bin/env python3
"""Real private PulseAudio; null devices only, no radio or codec control."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True,type=Path);args=parser.parse_args()
    if not shutil.which('pulseaudio') or not shutil.which('pactl'):
        raise RuntimeError('Real main-audio recovery tests require pulseaudio and pactl')
    modules=os.environ.get('TEST_PULSE_MODULES')
    if not modules:
        found=subprocess.run(['pkg-config','--variable=modlibexecdir','libpulse'],capture_output=True,text=True,timeout=3)
        candidate=found.stdout.strip()
        if found.returncode==0 and candidate and Path(candidate).is_dir():modules=candidate
    for case in ('cycles','partial-timeout','partial-reject','stalled-lock','sink-loss','server-loss'):
        with tempfile.TemporaryDirectory(prefix='main-audio-operation-') as tmp:
            root=Path(tmp);runtime=root/'run';runtime.mkdir()
            config=root/'pulse.pa';config.write_text(
                f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n'
                'load-module module-null-sink sink_name=radio_fixture rate=44100 channels=1 channel_map=mono norewinds=1\n'
                'load-module module-null-sink sink_name=decoy_fixture rate=44100 channels=1 channel_map=mono norewinds=1\n')
            env=dict(os.environ,PULSE_SERVER=f'unix:{root}/native',PULSE_RUNTIME_PATH=str(runtime),XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(root/"config"),PULSE_STATE_PATH=str(root/"state"))
            with (root/'pulse.log').open('w') as log:
                pulse_args=['pulseaudio','-nF',str(config),'--daemonize=no','--use-pid-file=no','--exit-idle-time=-1']
                if modules:
                    pulse_args+=['--dl-search-path',modules]
                    env['LD_LIBRARY_PATH']=modules+':'+env.get('LD_LIBRARY_PATH','')
                server=subprocess.Popen(pulse_args,env=env,stdout=log,stderr=log)
                try:
                    until=time.monotonic()+3
                    while not (root/'native').exists() and server.poll() is None and time.monotonic()<until:time.sleep(.01)
                    if not (root/'native').exists() or server.poll() is not None:raise RuntimeError((root/'pulse.log').read_text())
                    extra=[]
                    if case=='sink-loss':
                        result=subprocess.run(['pactl','list','short','modules'],env=env,capture_output=True,text=True,check=True,timeout=3)
                        extra=[next(row.split('\t')[0] for row in result.stdout.splitlines() if 'sink_name=radio_fixture ' in row)]
                    if case=='server-loss':extra=[str(server.pid)]
                    result=subprocess.run([str(args.binary.resolve()),case,*extra],env=env,capture_output=True,text=True,timeout=10)
                    if result.returncode:raise RuntimeError(f'{case} failed ({result.returncode}):\n{result.stdout}{result.stderr}\n{(root/"pulse.log").read_text()}')
                    print(result.stdout,end='')
                finally:
                    if server.poll() is None:server.terminate()
                    try:server.wait(timeout=2)
                    except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':main()
