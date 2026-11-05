#!/usr/bin/env python3
"""Run actual codec/four-stream controller on private Pulse endpoints; fake RF.
Arguments: TEST_BINARY SPEECH_WAV MODEM_RAW_48K (both public reference fixtures).
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import threading
import time
import json
import math
import wave


def main():
    binary, speech, modem = map(lambda p: Path(p).resolve(), sys.argv[1:4])
    mode=sys.argv[4:]
    root = Path(tempfile.mkdtemp(prefix='freedv-usb-test-'))
    (root / 'result').mkdir()
    print('Evidence:', root, flush=True)
    env = dict(os.environ, PULSE_SERVER=f'unix:{root}/native', PULSE_RUNTIME_PATH=str(root), PULSE_STATE_PATH=str(root), XDG_CONFIG_HOME=str(root))
    conf = root / 'default.pa'
    conf.write_text('.fail\n' + f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n' +
        'load-module module-null-sink sink_name=mic_fixture rate=48000 norewinds=1\n' +
        'load-module module-remap-source source_name=alsa_input.usb-fixture master=mic_fixture.monitor\n' +
        'load-module module-null-sink sink_name=rf_fixture rate=48000 norewinds=1\n' +
        'load-module module-remap-source source_name=radio_source_fixture master=rf_fixture.monitor\n' +
        'load-module module-null-sink sink_name=alsa_output.usb-fixture rate=48000 norewinds=1\n' +
        'load-module module-null-sink sink_name=radio_sink_fixture rate=44100 norewinds=1\n' +
        'load-module module-null-sink sink_name=unrelated_sink norewinds=1\nload-module module-rescue-streams\n')
    args = ['pulseaudio', '-nF', str(conf), '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1']
    modules=os.environ.get('TEST_PULSE_MODULES')
    if not modules:
        choices=list(Path(shutil.which('pulseaudio')).resolve().parents[1].glob('lib/pulse-*/modules'))
        if choices: modules=str(choices[0])
    if modules:
        args += ['--dl-search-path', modules]
        env['LD_LIBRARY_PATH']=modules+os.pathsep+env.get('LD_LIBRARY_PATH','')
    stop = threading.Event()
    producers = []
    def produce(sink, file, raw):
        while not stop.is_set():
            command = ['paplay', '--device=' + sink]
            if raw: command += ['--raw', '--rate=48000', '--format=s16le', '--channels=1']
            p = subprocess.Popen(command + [str(file)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            producers.append(p)
            p.wait()
    with (root / 'pulse.log').open('w') as log:
        server = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        threads = []
        recorder=None
        try:
            for _ in range(100):
                if server.poll() is not None: raise RuntimeError((root / 'pulse.log').read_text())
                if subprocess.run(['pactl', 'info'], env=env, capture_output=True).returncode == 0: break
                time.sleep(.05)
            else: raise RuntimeError('Private Pulse not ready')
            for sink, file, raw in [('mic_fixture', speech, False), ('rf_fixture', modem, True)]:
                t = threading.Thread(target=produce, args=(sink, file, raw), daemon=True);threads.append(t);t.start()
            recording=(root/'radio-output.raw').open('wb')
            recorder=subprocess.Popen(['parec','--device=radio_sink_fixture.monitor','--rate=48000','--format=s16le','--channels=1','--latency-msec=20'],env=env,stdout=recording,stderr=subprocess.DEVNULL)
            with (root / 'test.log').open('w') as output:
                test = subprocess.Popen([str(binary), str(root / 'result'),*mode], env=env, stdout=output, stderr=subprocess.STDOUT)
                if '--disconnect' in mode:
                    until=time.monotonic()+20
                    while 'TX_ACTIVE' not in (root/'test.log').read_text():
                        if test.poll() is not None or time.monotonic()>until: raise RuntimeError('TX fixture did not start')
                        time.sleep(.02)
                    subprocess.run(['pactl','unload-module','module-remap-source'],env=env,check=True)
                test.wait(timeout=60)
            print((root / 'test.log').read_text())
            assert test.returncode == 0, 'Four-stream test failed; see retained evidence'
            rows=[json.loads(line) for line in (root/'result/session.jsonl').read_text().splitlines()]
            assert rows[-1]['state'] in (5,6) and not rows[-1]['rf_tx'], 'Final stopped/RX evidence missing'
            assert rows[-1]['fault'] == (11 if '--stall' in mode else 2 if '--disconnect' in mode else 1), rows[-1]
            recorder.terminate();recorder.wait(timeout=5);recorder=None;recording.close()
            runner=os.environ.get('USB_RF_REFERENCE_RUNNER')
            if runner and not mode:
                pcm=(root/'radio-output.raw').read_bytes()
                seconds=math.ceil(len(pcm)/96000)+1
                pcm+=b'\0'*(seconds*96000-len(pcm))
                padded=root/'radio-padded.raw';padded.write_bytes(pcm)
                decoded=root/'decoded-radio-output';decoded.mkdir()
                with (root/'loopback-decode.log').open('w') as log:
                    subprocess.run([runner,'--rx',str(padded),'--rate','48000','--seconds',str(seconds),'--output',str(decoded)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
                stats=json.loads((decoded/'engine-stats.json').read_text())
                assert not stats['failures'] and stats['synchronized_blocks']>0 and stats['synthesis_blocks']>0,stats
                print('PASS actual modem sink loopback decodes through real receiver:',stats['synthesis_blocks'],'speech blocks')
        finally:
            if recorder:
                recorder.terminate();recorder.wait(timeout=5)
            stop.set()
            for p in producers:
                if p.poll() is None: p.terminate()
            for t in threads: t.join(timeout=2)
            server.terminate();server.wait(timeout=5)


if __name__ == '__main__': main()
