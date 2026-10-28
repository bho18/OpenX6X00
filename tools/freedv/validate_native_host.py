#!/usr/bin/env python3
"""Build and exercise native diagnostic prerequisites before publishing images."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_TESTS = {'freedv_session_controller', 'freedv_audio_playback', 'freedv_capture_dispatch',
                  'freedv_writer_preferences', 'freedv_native_radio_backend', 'freedv_native_audio_adapter',
                  'freedv_input_fence', 'freedv_front_input', 'freedv_remote_input_fence'}


def run(*args, **kwargs):
    print('+', ' '.join(map(str,args)), flush=True)
    return subprocess.run(list(map(str,args)), check=True, **kwargs)


def smoke_gui(gui, output):
    runtime = output/'runtime'; runtime.mkdir(); runtime.chmod(0o700)
    pulse = runtime/'pulse'; pulse.mkdir(); pulse.chmod(0o700)
    config = runtime/'config'; config.mkdir()
    socket_path = pulse/'native'
    pulse_config = runtime/'default.pa'
    pulse_config.write_text(f'.fail\nload-module module-native-protocol-unix socket={socket_path}\n'
        'load-module module-null-sink sink_name=radio rate=44100 channels=2\n'
        'set-default-sink radio\nset-default-source radio.monitor\n')
    env = os.environ.copy()
    env.update(PULSE_RUNTIME_PATH=str(pulse),PULSE_STATE_PATH=str(pulse),
               PULSE_SERVER='unix:'+str(socket_path),XDG_CONFIG_HOME=str(config),SDL_VIDEODRIVER='dummy')
    processes = []
    logs = []
    try:
        log = (output/'pulse.log').open('w'); logs.append(log)
        pulse_args=['pulseaudio','--daemonize=no','--use-pid-file=no','--exit-idle-time=-1','--disable-shm']
        module_result=subprocess.run(['pkg-config','--variable=modlibexecdir','libpulse'],capture_output=True,text=True)
        module_dir=module_result.stdout.strip()
        if module_dir and Path(module_dir).is_dir():
            pulse_args.append('--dl-search-path='+module_dir)
            env['LD_LIBRARY_PATH']=module_dir+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
        processes.append(subprocess.Popen(pulse_args+['-nF',str(pulse_config)],env=env,stdout=log,stderr=log))
        until = time.monotonic()+15
        while not socket_path.exists():
            if processes[0].poll() is not None or time.monotonic()>until:
                raise RuntimeError('development PulseAudio did not become ready')
            time.sleep(.1)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',50002))
        log = (output/'gui.log').open('w'); logs.append(log)
        processes.append(subprocess.Popen([str(gui)],cwd=ROOT,env=env,stdout=log,stderr=log))
        spec = importlib.util.spec_from_file_location('native_image_ws',ROOT/'tools/tci/tci_client_test.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.PORT=50002
        until=time.monotonic()+20
        ws=None
        while ws is None:
            if processes[-1].poll() is not None or time.monotonic()>until:
                raise RuntimeError('GUI did not start; see gui.log')
            try:
                ws=module.WS()
            except OSError:
                time.sleep(.1)
        ws.s.settimeout(4); ws.text('ping'); ws.text('panel 1')
        seen=set(); state=None; until=time.monotonic()+12
        try:
            while time.monotonic()<until and len(seen)<5:
                opcode,payload=ws.frame()
                if opcode==1:
                    text=payload.decode()
                    if text.startswith('hello '): seen.add('greeting')
                    elif text=='size 800 480': seen.add('geometry')
                    elif text=='pong': seen.add('ping')
                    elif text.startswith('state '):
                        state=json.loads(text[6:]); assert state and not state['tx']; seen.add('state')
                elif opcode==2 and len(payload)>10:
                    width,height,_=struct.unpack('<HHI',payload[:8])
                    if width==800 and height==480 and payload[8:10]==b'\xff\xd8':
                        (output/'simulator.jpg').write_bytes(payload[8:]); seen.add('screen')
        finally:
            ws.s.close()
        if seen!={'greeting','geometry','ping','state','screen'}:
            raise RuntimeError('incomplete live GUI checks: '+str(seen))
        return {'status':'PASS','observations':sorted(seen),'state':state}
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        for log in logs: log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--x6200-control',type=Path,required=True)
    parser.add_argument('--x6100-control',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=2)
    a=parser.parse_args(); work=a.work.resolve(); work.mkdir(parents=True,exist_ok=False)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    pins=json.loads((ROOT/'tools/freedv/sources.json').read_text())
    for name,path in [('x6200_control',a.x6200_control),('x6100_control_host_check',a.x6100_control)]:
        actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=path,text=True).strip()
        if actual!=pins[name]['commit']: raise ValueError('control header pin differs: '+name)
    # The repository's supported host helper owns build-host/deps. Signed system
    # Boost headers avoid an unrelated archive download.
    boost=ROOT/'build-host/deps/include/boost'; boost.parent.mkdir(parents=True,exist_ok=True)
    if not boost.exists() and Path('/usr/include/boost').is_dir(): boost.symlink_to('/usr/include/boost')
    run('bash','host/build.sh','build',cwd=ROOT)
    on=ROOT/'build-host'
    run('cmake','-S',ROOT,'-B',on,'-DHOST_BUILD=ON','-DENABLE_FREEDV_DIAGNOSTICS=ON',
        '-DENABLE_FREEDV_NATIVE_CODEC=OFF','-DX6200_CONTROL_INCLUDE_DIR='+str(a.x6200_control.resolve()/'include'))
    run('cmake','--build',on,'-j',a.jobs)
    tests=json.loads(subprocess.check_output(['ctest','--test-dir',str(on),'-N','--show-only=json-v1'],text=True))
    selected={test['name'] for test in tests['tests'] if re.search('freedv',test['name'])}
    if REQUIRED_TESTS-selected: raise ValueError('missing mandatory integration tests: '+str(sorted(REQUIRED_TESTS-selected)))
    run('ctest','--test-dir',on,'-R','freedv','--output-on-failure','--output-junit',work/'diagnostic-tests.xml')
    smoke=smoke_gui(on/'src/x6100_gui',work)
    off=work/'host-off'
    run('cmake','-S',ROOT,'-B',off,'-DHOST_BUILD=ON','-DENABLE_FREEDV_DIAGNOSTICS=OFF',
        '-DENABLE_FREEDV_NATIVE_CODEC=OFF','-DCMAKE_BUILD_TYPE=RelWithDebInfo')
    run('cmake','--build',off,'--target','x6100_gui','-j',a.jobs)
    control=work/'x6100-control'
    run('cmake','-S',a.x6100_control.resolve(),'-B',control,'-DAETHER_X6100CTRL_USE_LTO=OFF')
    run('cc','-std=c11','-Wall','-Wextra','-Werror','-DENABLE_FREEDV_DIAGNOSTICS=1',
        '-I'+str(ROOT),'-I'+str(a.x6100_control.resolve()/'include'),'-I'+str(control/'include'),
        '-c',ROOT/'src/hw/x6100/hw_x6100.c','-o',work/'x6100-backend.o')
    record={'schema_version':1,'firmware_commit':revision,'status':'PASS','hardware_qualified':False,
            'host_diagnostics_on':'PASS','host_diagnostics_off':'PASS','x6100_backend_compile':'PASS',
            'required_tests':sorted(REQUIRED_TESTS),'executed_diagnostic_tests':sorted(selected),'gui_smoke':smoke}
    (work/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
    print('PASS: host builds, mandatory integration tests, live GUI and X6100 compile')


if __name__=='__main__': main()
