#!/usr/bin/env python3
"""Exercise the actual shell manager/monitor with fake PulseAudio, no radio I/O."""
import fcntl
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'rootfs/usr/sbin/btaudio-ctl'
SOURCE = 'alsa_input.platform-sound.stereo-fallback'
SINK = 'bluez_sink.HEADSET.a2dp_sink'
TAG = 'sink_input_properties=application.id=openx6x00.native-bt-rx'
HOLD_BINARY = None
if '--hold-binary' in sys.argv:
    at=sys.argv.index('--hold-binary');HOLD_BINARY=Path(sys.argv[at+1]);del sys.argv[at:at+2]

FAKE_PACTL = r'''#!/usr/bin/env python3
import fcntl,json,os,sys,time
from pathlib import Path
root=Path(os.environ['BT_TEST_ROOT']);args=sys.argv[1:]
if args==['subscribe']:
    position=0
    while True:
        with (root/'events').open() as stream:
            stream.seek(position)
            for line in stream: print(line.rstrip(),flush=True)
            position=stream.tell()
        time.sleep(.015)
with (root/'state-lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    state=json.loads((root/'state').read_text())
    if (root/'query-fails').exists() and args==['list','short','modules']:
        (root/'query-failed-seen').touch();sys.exit(1)
    if args==['list','short','sinks']:
        for i,name in enumerate(state['sinks']):print(f'{i}\t{name}\tmodule-bluez5-device.c\ts16le 1ch 16000Hz\tRUNNING')
    elif args==['list','short','modules']:
        for row in state['modules']:print(f'{row[0]}\tmodule-loopback\t{row[1]}\t0')
    elif args==['list','cards']:pass
    elif args[:2]==['load-module','module-loopback']:
        if (root/'load-delay').exists():
            (root/'load-started').touch();time.sleep(.35)
        number=state['next'];state['next']+=1;state['modules'].append([number,' '.join(args[2:])]);print(number)
        with (root/'events').open('a') as stream:stream.write("Event 'new' on module #1\n")
    elif args[:1]==['unload-module']:
        state['modules']=[row for row in state['modules'] if row[0]!=int(args[1])]
        with (root/'events').open('a') as stream:stream.write("Event 'remove' on module #1\n")
    elif args[:1] not in (['set-port-latency-offset'],['set-card-profile']):sys.exit(2)
    (root/'state').write_text(json.dumps(state))
'''


class BluetoothHoldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bt-rx-hold-')
        self.root=Path(self.temp.name);self.bin=self.root/'bin';self.bin.mkdir()
        self.runtime=self.root/'run';self.runtime.mkdir()
        self.script=self.root/'btaudio-ctl'
        text=SCRIPT.read_text().replace('#!/bin/sh','#!/bin/bash',1)
        for before,after in [('CONF=/mnt/btaudio.conf',f'CONF={self.root}/conf'),
                             ('STATE=/tmp/btaudio-hp',f'STATE={self.root}/hp'),
                             ('PIDFILE=/tmp/btaudio-monitor.pid',f'PIDFILE={self.root}/pid'),
                             ('LST=/tmp/btaudio-lst',f'LST={self.root}/lst'),
                             ('RUNTIME=/run',f'RUNTIME={self.runtime}')]:
            self.assertIn(before,text);text=text.replace(before,after,1)
        self.script.write_text(text);self.script.chmod(0o700)
        for name,contents in [('pactl',FAKE_PACTL),('hcitool','#!/bin/sh\nexit 0\n'),('sync','#!/bin/sh\nexit 0\n')]:
            path=self.bin/name;path.write_text(contents);path.chmod(0o700)
        (self.root/'conf').write_text('BTAUDIO_RX="yes"\nBTAUDIO_MUTE="yes"\n')
        (self.root/'events').touch()
        self.write_state({'sinks':[SINK],'modules':[], 'next':10})
        self.env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],BT_TEST_ROOT=str(self.root),PULSE_SERVER='unused-test-server')
        self.lease=None

    def tearDown(self):
        if self.lease:self.lease.close()
        state=self.runtime/'btaudio-monitor-v2'
        if state.exists():
            pid=int(state.read_text().split()[1])
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
            until=time.monotonic()+1
            while state.exists() and time.monotonic()<until:time.sleep(.01)
            if state.exists():
                try:os.killpg(pid,signal.SIGKILL)
                except ProcessLookupError:pass
        self.temp.cleanup()

    def command(self,*args,check=True):
        result=subprocess.run([str(self.script),*args],env=self.env,capture_output=True,text=True,timeout=3)
        if check:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def read_state(self):
        with (self.root/'state-lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            return json.loads((self.root/'state').read_text())

    def write_state(self,state):
        with (self.root/'state-lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);(self.root/'state').write_text(json.dumps(state))

    def wait(self,predicate,seconds=2):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            if predicate():return
            time.sleep(.02)
        self.fail('condition did not become true')

    def managed(self):
        return [row for row in self.read_state()['modules'] if TAG in row[1]]

    def hold(self):
        self.lease=(self.runtime/'btaudio-rx-hold.lock').open('a')
        until=time.monotonic()+2
        while True:
            try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB);return
            except BlockingIOError:
                self.assertLess(time.monotonic(),until);time.sleep(.01)

    def test_hold_isolates_only_managed_routes_and_crash_restores(self):
        pref=(self.root/'conf').read_bytes()
        ready=self.command('protocol').stdout;self.assertTrue(ready.startswith('BTAUDIO_RX_V2 READY '))
        self.wait(lambda:len(self.managed())==1)
        legacy=f'source={SOURCE} sink={SINK} latency_msec=400 adjust_time=1 max_latency_msec=700 fast_adjust_threshold_msec=600 source_dont_move=true sink_dont_move=true'
        state=self.read_state();state['modules'] += [[90,f'source=other_source sink={SINK} {TAG}'],[91,f'source={SOURCE} sink=other_sink {TAG}'],
            [92,f'source={SOURCE} sink={SINK} latency_msec=100'],
            [93,legacy],[94,legacy+' channels=2 remix=no'],[95,legacy+' latency_msec=400']]
        self.write_state(state)
        self.hold();self.assertTrue(self.command('hold-sync').stdout.startswith('BTAUDIO_RX_V2 QUIET '))
        self.assertFalse(self.managed() and any(row[0]<90 for row in self.managed()))
        self.assertEqual({row[0] for row in self.read_state()['modules']},{90,91,92,94,95})
        self.assertTrue((self.root/'hp').exists());self.assertEqual((self.root/'conf').read_bytes(),pref)
        with (self.root/'events').open('a') as stream:stream.write("Event 'new' on sink #7\n")
        time.sleep(.15);self.assertEqual({row[0] for row in self.read_state()['modules']},{90,91,92,94,95})
        # No release command: emulate the kernel closing the lease on GUI death.
        self.lease.close();self.lease=None
        self.wait(lambda:any(row[0]<90 for row in self.managed()),1.5)
        self.assertEqual((self.root/'conf').read_bytes(),pref)

    def test_release_reloads_current_preference(self):
        self.command('protocol');self.wait(lambda:len(self.managed())==1)
        self.hold();self.command('hold-sync')
        (self.root/'conf').write_text('BTAUDIO_RX="no"\nBTAUDIO_MUTE="yes"\n')
        self.lease.close();self.lease=None
        self.assertTrue(self.command('reconcile').stdout.startswith('BTAUDIO_RX_V2 RESTORED '))
        self.assertFalse(self.managed())
        self.assertIn('BTAUDIO_RX="no"',(self.root/'conf').read_text())

    def test_failed_release_retries_without_an_external_card_event(self):
        self.command('protocol');self.wait(lambda:len(self.managed())==1)
        self.hold();self.command('hold-sync');time.sleep(.1)
        (self.root/'query-fails').touch()
        self.wait(lambda:(self.root/'query-failed-seen').exists())
        self.lease.close();self.lease=None
        self.assertNotEqual(self.command('reconcile',check=False).returncode,0)
        # Allow the monitor to observe a failed attempt after lease release.
        time.sleep(1.1);self.assertFalse(self.managed())
        (self.root/'query-fails').unlink()
        self.wait(lambda:len(self.managed())==1,1.5)

    def test_live_legacy_monitor_requires_reboot_without_killing_it(self):
        old=subprocess.Popen(['sleep','20'])
        try:
            (self.root/'pid').write_text(str(old.pid))
            result=self.command('protocol',check=False)
            self.assertEqual(result.returncode,78);self.assertEqual(result.stdout,'BTAUDIO_RX_V2 REBOOT_REQUIRED\n')
            self.assertIsNone(old.poll());self.assertFalse((self.runtime/'btaudio-monitor-v2').exists())
        finally:old.terminate();old.wait()

    def test_protocol_is_singleton_and_unheld_quiet_is_rejected(self):
        first=self.command('protocol').stdout
        self.assertEqual(self.command('protocol').stdout,first)
        self.wait(lambda:len(self.managed())==1)
        self.assertNotEqual(self.command('hold-sync',check=False).returncode,0)
        self.assertEqual(len(self.managed()),1)

    def test_failed_query_cannot_acknowledge_quiet(self):
        self.command('protocol');self.wait(lambda:len(self.managed())==1);self.hold()
        (self.root/'query-fails').touch()
        result=self.command('hold-sync',check=False)
        self.assertNotEqual(result.returncode,0);self.assertNotIn(' QUIET ',result.stdout)

    def test_creation_holds_shared_lease_for_entire_transaction(self):
        self.command('protocol');self.wait(lambda:len(self.managed())==1)
        state=self.read_state();state['modules']=[];self.write_state(state)
        (self.root/'load-delay').touch()
        child=subprocess.Popen([str(self.script),'reconcile'],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.wait(lambda:(self.root/'load-started').exists())
        with (self.runtime/'btaudio-rx-hold.lock').open('a') as probe:
            with self.assertRaises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        output,error=child.communicate(timeout=3);self.assertEqual(child.returncode,0,output+error)
        self.hold();self.command('hold-sync');self.assertFalse(self.managed())

    @unittest.skipUnless(HOLD_BINARY, 'Supply --hold-binary for the C++/shell integration boundary')
    def test_actual_gui_lease_process_death_restores_native_rx(self):
        child=subprocess.Popen([str(HOLD_BINARY),'--hold-only',str(self.script),str(self.runtime/'btaudio-rx-hold.lock')],
            env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertTrue(select.select([child.stdout],[],[],3)[0], 'GUI lease did not become ready')
            self.assertEqual(child.stdout.readline(),'HELD\n')
            self.assertFalse(self.managed())
            child.kill();child.wait(timeout=1)
            self.wait(lambda:len(self.managed())==1,1.5)
        finally:
            if child.poll() is None:child.kill();child.wait()
            child.stdout.close();child.stderr.close()


if __name__=='__main__':unittest.main()
