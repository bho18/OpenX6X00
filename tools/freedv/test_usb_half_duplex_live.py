#!/usr/bin/env python3
"""Real codec + private Pulse fixture, alternating TX/RX. No physical devices.
Usage: test_usb_half_duplex_live.py BINARY SPEECH_WAV [--disconnect]
TEST_PULSE_MODULES supports extracted Pulse binaries; log/results are retained.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


def main():
    binary, speech = (Path(p).resolve() for p in sys.argv[1:3])
    lost = '--disconnect' in sys.argv[3:]
    seconds, turns = (8, 3) if '--full' in sys.argv[3:] else (2, 2)
    root = Path(tempfile.mkdtemp(prefix='usb-half-duplex-check-'))
    result = root / 'result'
    result.mkdir()
    print('Retained test evidence:', root, flush=True)
    env = dict(os.environ, PULSE_SERVER=f'unix:{root}/native', PULSE_RUNTIME_PATH=str(root),
               PULSE_STATE_PATH=str(root), XDG_CONFIG_HOME=str(root))
    conf = root / 'test.pa'
    conf.write_text('.fail\n'
        f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n'
        'load-module module-null-sink sink_name=capture_fixture rate=48000 norewinds=1\n'
        'load-module module-remap-source source_name=alsa_input.usb-fixture master=capture_fixture.monitor\n'
        'load-module module-null-sink sink_name=alsa_output.usb-fixture rate=48000 norewinds=1\n'
        'load-module module-null-sink sink_name=unrelated_radio_fixture norewinds=1\n'
        'load-module module-rescue-streams\n')
    args = ['pulseaudio', '-nF', str(conf), '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1']
    if os.environ.get('TEST_PULSE_MODULES'):
        args += ['--dl-search-path', os.environ['TEST_PULSE_MODULES']]
    stop = threading.Event()
    producer_process = []
    def produce():
        while not stop.is_set():
            p = subprocess.Popen(['paplay', '--device=capture_fixture', str(speech)], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            producer_process[:] = [p]
            p.wait()
    with (root / 'pulse.log').open('w') as log:
        server = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        producer = None
        try:
            for i in range(100):
                if server.poll() is not None: raise RuntimeError((root / 'pulse.log').read_text())
                ready = subprocess.run(['pactl', 'info'], env=env, capture_output=True)
                if ready.returncode == 0: break
                time.sleep(.05)
            else: raise RuntimeError('Private Pulse not ready')
            producer = threading.Thread(target=produce)
            producer.start()
            with (root / 'helper.log').open('w') as output:
                helper = subprocess.Popen([str(binary), 'alsa_input.usb-fixture', 'alsa_output.usb-fixture',
                                           str(result), str(seconds), str(turns)], env=env, stdout=output, stderr=output)
                if lost:
                    deadline = time.monotonic() + 15
                    while 'GO - turn 1' not in (root / 'helper.log').read_text():
                        if helper.poll() is not None or time.monotonic() > deadline: raise RuntimeError('Helper did not reach TX')
                        time.sleep(.05)
                    subprocess.run(['pactl', 'unload-module', 'module-remap-source'], env=env, check=True)
                helper.wait(timeout=120)
            report = json.loads((result / 'session.json').read_text())
            if lost:
                assert helper.returncode == 1 and not report['ok'], report
                assert 'stream' in report['fault'].lower() or 'binding' in report['fault'].lower(), report
            else:
                assert helper.returncode == 0 and report['ok'], ((root / 'helper.log').read_text(), report)
                assert report['turns_completed'] == turns and report['eoo_count'] == turns, report
                assert report['stream_creations'] == 2 and report['rade_creations'] == 1, report
                assert report['encoder_creations'] == 1 and report['decoder_creations'] == 1, report
                assert report['synthesis_blocks'] > 0 and report['holes'] == 0, report
                assert 47520 <= report['read_rate_hz'] <= 48480, report
                assert not report['rf_tested'] and not report['ptt_tested'], report
                for n in range(1, turns + 1):
                    assert (result / f'turn-{n}-microphone.raw').stat().st_size == seconds * 48000 * 2
                    assert (result / f'turn-{n}-decoded.raw').stat().st_size > 48000
            print(json.dumps(report, indent=2))
            print('PASS:', 'disconnect fails with alternate sink present' if lost else 'real codec half-duplex turns with retained engine/streams; virtual endpoints only')
        finally:
            stop.set()
            if producer_process and producer_process[0].poll() is None:
                producer_process[0].terminate()
            if producer:
                producer.join(timeout=5)
            server.terminate()
            server.wait(timeout=5)


if __name__ == '__main__': main()
