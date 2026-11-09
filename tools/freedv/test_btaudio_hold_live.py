#!/usr/bin/env python3
"""Actual C++ lease and production shell against an isolated PulseAudio graph.

Only null sinks/remapped monitor sources exist. No Bluetooth adapter, ALSA
device, radio, PTT or codec operation is accessed.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
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


@contextmanager
def retained_fixture(case,evidence_root):
    root=Path(tempfile.mkdtemp(prefix='bt-rx-hold-live-'));passed=False
    try:
        yield str(root)
        passed=True
    finally:
        destination=evidence_root/(case+'-'+root.name);destination.mkdir(parents=True,exist_ok=True)
        for path in root.rglob('*'):
            if path.is_file():
                target=destination/path.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        print(('PASS evidence: ' if passed else 'FAIL evidence: ')+str(destination),flush=True)
        if passed:shutil.rmtree(root)
        else:print('Failed fixture retained: '+str(root),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--hold-binary',required=True,type=Path)
    parser.add_argument('--evidence-root',type=Path,default=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'freedv-btaudio-live-evidence')
    parser.add_argument('--restore-delay-ms',type=int,choices=(0,1650),default=0)
    args=parser.parse_args();args.hold_binary=args.hold_binary.resolve();case='normal' if not args.restore_delay_ms else 'delayed-restore'

    require(shutil.which('pulseaudio') and shutil.which('pactl'),'Private-Pulse test requires pulseaudio and pactl')
    source='alsa_input.platform-sound.stereo-fallback';sink='bluez_sink.VIRTUAL.a2dp_sink'
    with retained_fixture(case,args.evidence_root) as directory:
        root=Path(directory);runtime=root/'run';runtime.mkdir();tools=root/'bin';tools.mkdir()
        receipt={'case':case,'status':'RUNNING','started_monotonic':time.monotonic(),'lease_observation_budget_seconds':1.5,
                 'restore_service_budget_seconds':2,'total_crash_recovery_budget_seconds':3.5,'injected_restore_delay_ms':args.restore_delay_ms}
        phase='prepare';active_deadline=None;commands=[];last_graph=[]

        for name in ('hcitool','sync'):
            path=tools/name;path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o700)
        helper_source=Path(__file__).resolve().parents[2]/'rootfs/usr/sbin/btaudio-ctl'
        receipt['production_sha256']={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in
            (helper_source,Path(__file__).resolve().parents[2]/'src/btaudio_rx_hold.inc',args.hold_binary)}
        helper=root/'btaudio-ctl';text=helper_source.read_text()
        text=text.replace('#!/bin/sh','#!/bin/bash',1)
        for before,after in [('CONF=/mnt/btaudio.conf',f'CONF={root}/conf'),('STATE=/tmp/btaudio-hp',f'STATE={root}/hp'),
                             ('PIDFILE=/tmp/btaudio-monitor.pid',f'PIDFILE={root}/pid'),('LST=/tmp/btaudio-lst',f'LST={root}/lst'),
                             ('RUNTIME=/run',f'RUNTIME={runtime}')]:
            require(before in text,'Helper path fixture drifted');text=text.replace(before,after,1)
        # Fixture-only observation of the MONITOR recognizing release, never a
        # test-side flock. Original production bytes are hashed above.
        observer='[ ! -e "$RUNTIME/gui-crashed" ] || [ -e "$RUNTIME/release-observed" ] || printf "%s\\n" "$EPOCHREALTIME" > "$RUNTIME/release-observed"'
        require('flock -sn 6 && exit 1' in text and 'if flock -sn 9; then shared=yes; fi' in text,'Monitor observation fixture drifted')
        text=text.replace('flock -sn 6 && exit 1',f'if flock -sn 6; then {observer}; exit 1; fi',1)
        text=text.replace('if flock -sn 9; then shared=yes; fi',f'if flock -sn 9; then shared=yes; {observer}; fi',1)
        text=text.replace('\nmonitor() {','\nmonitor() {\n exec 4>>"$RUNTIME/monitor.trace"; BASH_XTRACEFD=4; PS4=\'+$EPOCHREALTIME [$BASHPID] \'; set -x',1)
        background='setsid "$0" monitor </dev/null >/dev/null 2>&1 5>&- &'
        require(background in text,'Monitor logging fixture drifted')
        text=text.replace(background,'setsid "$0" monitor </dev/null >"$RUNTIME/monitor.log" 2>&1 5>&- &',1)
        if args.restore_delay_ms:
            load='pactl load-module module-loopback source=$SOURCE';require(load in text,'Controlled restore fixture drifted')
            text=text.replace(load,'if [ -e "$RUNTIME/gui-crashed" ]; then sleep 1.65; fi\n\t\t\t'+load,1)
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
        child=None;lease_line=''
        def command(*argv):
            started=time.monotonic();row={'argv':list(argv),'phase':phase,'started_monotonic':started}
            try:
                limit=3 if active_deadline is None else min(3,max(.001,active_deadline-time.monotonic()))
                result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=limit)
                row.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
                require(result.returncode==0,f'{argv}: {result.stdout}{result.stderr}');return result.stdout
            except BaseException as error:row['error']=repr(error);raise
            finally:
                row['elapsed_seconds']=time.monotonic()-started;commands.append(row)
                (root/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        def modules():
            nonlocal last_graph
            last_graph=command('pactl','list','short','modules').splitlines();return last_graph
        def managed():return [row for row in modules() if 'application.id=openx6x00.native-bt-rx' in row]
        def wait(predicate,seconds,label='graph readiness',deadline=None):
            nonlocal phase,active_deadline
            phase=label;until=deadline if deadline is not None else time.monotonic()+seconds;active_deadline=until
            try:
                while time.monotonic()<until:
                    if predicate():return
                    time.sleep(min(.02,max(0,until-time.monotonic())))
                raise RuntimeError(label+': private-Pulse graph did not settle before deadline')
            finally:active_deadline=None
        try:
            wait(lambda:(root/'native').exists() or server.poll() is not None,3,'server ready')
            require(server.poll() is None,(root/'pulse.log').read_text())
            command(str(helper),'protocol');wait(lambda:len(managed())==1,2,'initial raw route')
            unrelated=command('pactl','load-module','module-loopback',f'source={source}','sink=other_fixture').strip()
            phase='hold admission'
            child=subprocess.Popen([str(args.hold_binary),'--hold-only',str(helper),str(runtime/'btaudio-rx-hold.lock')],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            receipt['lease_pid']=child.pid
            require(select.select([child.stdout],[],[],3)[0],'Actual GUI lease failed to become ready')
            lease_line=child.stdout.readline()
            require(lease_line=='HELD\n','GUI lease did not acknowledge raw-route isolation')
            phase='held isolation'
            require(not managed(),'Managed radio -> A2DP route survived the hold')
            require(any(row.startswith(unrelated+'\t') for row in modules()),'Unrelated route was changed')
            phase='sink event isolation'
            command('pactl','set-sink-volume',sink,'30%');time.sleep(.1)
            require(not managed(),'A sink event recreated a managed route during the hold')
            crashed=time.monotonic();receipt['crash_monotonic']=crashed;(runtime/'gui-crashed').touch()
            child.kill();child.wait(timeout=1)
            wait(lambda:(runtime/'release-observed').exists(),0,'monitor lease-release observation',crashed+1.5)
            observed=time.monotonic();receipt['lease_observed_seconds']=observed-crashed
            receipt['lease_observed_wall']=(runtime/'release-observed').read_text().strip()
            wait(lambda:len(managed())==1,0,'raw-route restoration service',min(crashed+3.5,observed+2))
            receipt['graph_restored_seconds']=time.monotonic()-crashed
            if args.restore_delay_ms:require(receipt['graph_restored_seconds']>=1.65,'Controlled restoration delay was not exercised')
            require(any(row.startswith(unrelated+'\t') for row in modules()),'Crash recovery changed an unrelated route')
            require((root/'conf').read_bytes()==pref,'Native Bluetooth preferences changed')
            receipt['status']='PASS'
            print(f"PASS: {case}: monitor release {receipt['lease_observed_seconds']:.3f}s; raw route {receipt['graph_restored_seconds']:.3f}s; unrelated route and preferences preserved")
        except BaseException as error:
            receipt.update(status='FAIL',error=repr(error));raise
        finally:
            receipt.update(phase=phase,elapsed_seconds=time.monotonic()-receipt['started_monotonic'],last_module_graph=last_graph)
            # Capture private runtime state before teardown, including failures.
            for name in ('btaudio-monitor-v2','btaudio-monitor-held-v2','release-observed'):
                path=runtime/name
                if path.exists():receipt[name]=path.read_text()
            for what in ('modules','sinks'):
                try:
                    snapshot=subprocess.run(['pactl','list','short',what],env=env,capture_output=True,text=True,timeout=1)
                    (root/('final-'+what+'.json')).write_text(json.dumps({'returncode':snapshot.returncode,'stdout':snapshot.stdout,'stderr':snapshot.stderr},indent=2)+'\n')
                except BaseException as error:receipt['final_'+what+'_error']=repr(error)
            if child:
                if child.poll() is None:child.kill();child.wait()
                out,err=child.communicate(timeout=1);receipt['lease_returncode']=child.returncode
                (root/'lease.stdout').write_text(lease_line+out);(root/'lease.stderr').write_text(err)
            state=runtime/'btaudio-monitor-v2'
            if state.exists():
                pid=int(state.read_text().split()[1])
                for name in ('stat','status','wchan','cmdline'):
                    try:(root/('monitor-'+name+'.txt')).write_bytes(Path(f'/proc/{pid}/{name}').read_bytes())
                    except OSError as error:receipt['monitor_'+name+'_error']=repr(error)
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
            (root/'result.json').write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':main()
