#!/usr/bin/env python3
"""Observe burst delivery through a private PulseAudio null sink; no hardware qualification."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True, help='prebuilt host freedv_playback_frame_probe')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    probe = args.probe.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='freedv-cadence-', dir='/tmp') as name:
        runtime = Path(name); runtime.chmod(0o700)
        state = runtime/'state'; state.mkdir(); config = runtime/'config'; config.mkdir()
        socket = state/'native'
        script = runtime/'server.pa'
        script.write_text(f'.fail\nload-module module-native-protocol-unix socket={socket}\n'
            'load-module module-null-sink sink_name=freedv_probe rate=44100 channels=1\n'
            'set-default-sink freedv_probe\nset-default-source freedv_probe.monitor\n')
        env = os.environ.copy()
        env.update(PULSE_RUNTIME_PATH=str(state), PULSE_STATE_PATH=str(state), XDG_CONFIG_HOME=str(config),
                   PULSE_SERVER='unix:'+str(socket), FREEDV_TEST_PULSE_SERVER='unix:'+str(socket))
        modules = subprocess.check_output(['pkg-config','--variable=modlibexecdir','libpulse'], text=True).strip()
        command = ['pulseaudio','--daemonize=no','--use-pid-file=no','--exit-idle-time=-1','--disable-shm']
        if modules and Path(modules).is_dir():
            command.append('--dl-search-path='+modules)
            env['LD_LIBRARY_PATH']=modules+':'+env.get('LD_LIBRARY_PATH','')
        processes=[]
        with (args.output/'server.log').open('w') as log:
            try:
                server=subprocess.Popen(command+['-nF',str(script)],env=env,stdout=log,stderr=log);processes.append(server)
                deadline=time.monotonic()+15
                while not socket.exists():
                    if server.poll() is not None or time.monotonic()>deadline: raise RuntimeError('private null-sink server failed')
                    time.sleep(.05)
                # Keep the virtual device clock running, analogous to the GUI's
                # ordinary stream. /dev/zero supplies generated silence only.
                background=subprocess.Popen(['pacat','--playback','--raw','--rate=44100','--channels=1',
                    '--latency-msec=80','--device=freedv_probe','/dev/zero'],env=env,stdout=log,stderr=log)
                processes.append(background);time.sleep(2.5)
                rows=[]
                for lead in ('0','1'):
                    result=subprocess.run([str(probe),lead],env=env,capture_output=True,text=True,timeout=20)
                    (args.output/('lead-'+lead+'.log')).write_text(result.stdout+result.stderr)
                    if result.returncode: raise RuntimeError('cadence probe failed: '+result.stderr)
                    row=json.loads(result.stdout)
                    if row['accepted_samples']!=105840 or row['maxlength_bytes']>17640: raise RuntimeError('sample/buffer contract failed')
                    rows.append(row)
                record={'schema_version':1,'hardware_qualified':False,'radio_control':False,
                    'measurement':'host synthetic 120ms bursts with 6ms arrival jitter; not radio performance',
                    'results':rows,'underflow_counts_are_observations_not_ci_performance_gates':True}
                (args.output/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
                print(json.dumps(record,indent=2))
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__=='__main__':
    main()
