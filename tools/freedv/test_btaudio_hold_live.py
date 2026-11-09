#!/usr/bin/env python3
"""Actual C++ lease and production shell against an isolated PulseAudio graph.

Only null sinks/remapped monitor sources exist. No Bluetooth adapter, ALSA
device, radio, PTT or codec operation is accessed.
"""
import argparse
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time


def require(value,message):
    if not value:raise RuntimeError(message)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--hold-binary',required=True,type=Path);args=parser.parse_args()
    require(shutil.which('pulseaudio') and shutil.which('pactl'),'Private-Pulse test requires pulseaudio and pactl')
    source='alsa_input.platform-sound.stereo-fallback';sink='bluez_sink.VIRTUAL.a2dp_sink'
    with tempfile.TemporaryDirectory(prefix='bt-rx-hold-live-') as directory:
        root=Path(directory);runtime=root/'run';runtime.mkdir();tools=root/'bin';tools.mkdir()
        for name in ('hcitool','sync'):
            path=tools/name;path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o700)
        helper=root/'btaudio-ctl';text=(Path(__file__).resolve().parents[2]/'rootfs/usr/sbin/btaudio-ctl').read_text()
        text=text.replace('#!/bin/sh','#!/bin/bash',1)
        for before,after in [('CONF=/mnt/btaudio.conf',f'CONF={root}/conf'),('STATE=/tmp/btaudio-hp',f'STATE={root}/hp'),
                             ('PIDFILE=/tmp/btaudio-monitor.pid',f'PIDFILE={root}/pid'),('LST=/tmp/btaudio-lst',f'LST={root}/lst'),
                             ('RUNTIME=/run',f'RUNTIME={runtime}')]:
            require(before in text,'Helper path fixture drifted');text=text.replace(before,after,1)
        helper.write_text(text);helper.chmod(0o700)
        pref=b'BTAUDIO_RX="yes"\nBTAUDIO_MUTE="yes"\n';(root/'conf').write_bytes(pref)
        config=root/'pulse.pa';config.write_text(
            f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n'
            'load-module module-null-sink sink_name=radio_fixture rate=44100 channels=1 channel_map=mono\n'
            f'load-module module-remap-source master=radio_fixture.monitor source_name={source} channels=1 master_channel_map=mono channel_map=mono\n'
            f'load-module module-null-sink sink_name={sink} rate=16000 channels=1 channel_map=mono\n'
            'load-module module-null-sink sink_name=other_fixture rate=44100 channels=1 channel_map=mono\n')
        env=dict(os.environ,PATH=str(tools)+':'+os.environ['PATH'],PULSE_SERVER=f'unix:{root}/native',
                 PULSE_RUNTIME_PATH=str(runtime),XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(root/'config'),XDG_CACHE_HOME=str(root/'cache'))
        pulse_args=['pulseaudio','-nF',str(config),'--daemonize=no','--use-pid-file=no','--exit-idle-time=-1']
        modules=os.environ.get('TEST_PULSE_MODULES')
        if not modules:
            prefix=Path(shutil.which('pulseaudio')).resolve().parents[1]
            choices=list(prefix.glob('lib/pulse-*/modules'))
            if choices:modules=str(choices[0])
        if modules:
            pulse_args+=['--dl-search-path',modules]
            env['LD_LIBRARY_PATH']=modules+os.pathsep+env.get('LD_LIBRARY_PATH','')
        log=(root/'pulse.log').open('w');server=subprocess.Popen(pulse_args,env=env,stdout=log,stderr=log)
        child=None
        def command(*argv):
            result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=3)
            require(result.returncode==0,f'{argv}: {result.stdout}{result.stderr}');return result.stdout
        def modules():return command('pactl','list','short','modules').splitlines()
        def managed():return [row for row in modules() if 'application.id=openx6x00.native-bt-rx' in row]
        def wait(predicate,seconds):
            until=time.monotonic()+seconds
            while time.monotonic()<until:
                if predicate():return
                time.sleep(.02)
            raise RuntimeError('Private-Pulse graph did not settle before deadline')
        try:
            wait(lambda:(root/'native').exists() or server.poll() is not None,3)
            require(server.poll() is None,(root/'pulse.log').read_text())
            command(str(helper),'protocol');wait(lambda:len(managed())==1,2)
            unrelated=command('pactl','load-module','module-loopback',f'source={source}','sink=other_fixture').strip()
            child=subprocess.Popen([str(args.hold_binary),'--hold-only',str(helper),str(runtime/'btaudio-rx-hold.lock')],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            require(select.select([child.stdout],[],[],3)[0],'Actual GUI lease failed to become ready')
            require(child.stdout.readline()=='HELD\n','GUI lease did not acknowledge raw-route isolation')
            require(not managed(),'Managed radio -> A2DP route survived the hold')
            require(any(row.startswith(unrelated+'\t') for row in modules()),'Unrelated route was changed')
            command('pactl','set-sink-volume',sink,'30%');time.sleep(.1)
            require(not managed(),'A sink event recreated a managed route during the hold')
            child.kill();child.wait(timeout=1);wait(lambda:len(managed())==1,1.5)
            require(any(row.startswith(unrelated+'\t') for row in modules()),'Crash recovery changed an unrelated route')
            require((root/'conf').read_bytes()==pref,'Native Bluetooth preferences changed')
            print('PASS: actual C++ lease + real PulseAudio raw-route isolation, sink-event exclusion, crash restoration and unrelated-route preservation')
        finally:
            if child:
                if child.poll() is None:child.kill();child.wait()
                child.stdout.close();child.stderr.close()
            state=runtime/'btaudio-monitor-v2'
            if state.exists():
                pid=int(state.read_text().split()[1])
                try:os.kill(pid,signal.SIGTERM)
                except ProcessLookupError:pass
                until=time.monotonic()+1
                while state.exists() and time.monotonic()<until:time.sleep(.01)
                if state.exists():
                    try:os.killpg(pid,signal.SIGKILL)
                    except ProcessLookupError:pass
            server.terminate()
            try:server.wait(timeout=2)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            log.close()


if __name__=='__main__':main()
