#!/usr/bin/env python3
"""Exercise the compiled helper on private virtual Pulse devices, never RF.

Usage: python test_usb_headset_pcm.py /path/to/host/usb_headset_pcm
Requires pulseaudio and pactl on PATH. Module overrides support extracted tools.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='usb-pcm-test-') as temp:
        root = Path(temp)
        env = dict(os.environ, PULSE_SERVER=f'unix:{root}/native', PULSE_RUNTIME_PATH=temp,
                   PULSE_STATE_PATH=temp, XDG_CONFIG_HOME=temp)
        conf = root / 'test.pa'
        conf.write_text('.fail\n'
            f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n'
            'load-module module-null-sink sink_name=unrelated_radio_fixture norewinds=1\n'
            'load-module module-null-source source_name=unrelated_radio_fixture\n'
            'load-module module-rescue-streams\n')
        server_args = ['pulseaudio', '-nF', str(conf), '--daemonize=no', '--use-pid-file=no',
                       '--exit-idle-time=-1']
        if os.environ.get('TEST_PULSE_MODULES'):
            server_args.extend(['--dl-search-path', os.environ['TEST_PULSE_MODULES']])
        with (root / 'pulse.log').open('w') as log:
            server = subprocess.Popen(server_args, env=env, stdout=log, stderr=log)
            def pactl(*args):
                return subprocess.run(['pactl', *args], env=env, capture_output=True,
                                      text=True, timeout=5, check=True).stdout.strip()
            try:
                for attempt in range(100):
                    if server.poll() is not None: raise RuntimeError((root / 'pulse.log').read_text())
                    if (root / 'native').exists():
                        try:
                            pactl('info')
                            break
                        except subprocess.CalledProcessError:
                            pass
                    time.sleep(.05)
                else:
                    raise RuntimeError((root / 'pulse.log').read_text())
                mic = 'alsa_input.usb-synthetic_fixture'
                ear = 'alsa_output.usb-synthetic_fixture'
                pactl('load-module', 'module-null-sink', 'sink_name=capture_fixture', 'rate=44100', 'norewinds=1')
                def microphone():
                    return pactl('load-module', 'module-remap-source', f'source_name={mic}',
                                 'master=capture_fixture.monitor')
                def devices():
                    source = microphone()
                    sink = pactl('load-module', 'module-null-sink', f'sink_name={ear}', 'rate=44100', 'norewinds=1')
                    return source, sink
                source, sink = devices()
                play = root / 'play.raw'
                play.write_bytes(b'\x10\x00' * 44100 * 3)
                def start(name, seconds):
                    return subprocess.Popen([str(binary), mic, ear, str(root / (name+'.raw')),
                        str(play), str(root / (name+'.json')), str(seconds)], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                process = start('success', 3)
                stdout, stderr = process.communicate(timeout=15)
                assert process.returncode == 0, (stderr, (root / 'success.json').read_text())
                report = json.loads((root / 'success.json').read_text())
                assert report['captured_bytes'] == 3 * 88200 and report['drained']
                assert report['dont_move'] and report['sink'] == ear and report['holes'] == 0
                for name, module in [('lost_mic', source), ('lost_sink', sink)]:
                    process = start(name, 3)
                    time.sleep(.7)
                    pactl('unload-module', module)
                    stdout, stderr = process.communicate(timeout=5)
                    assert process.returncode != 0, name
                    report = json.loads((root / (name+'.json')).read_text())
                    assert not report['ok'] and report['fault'], name
                    assert report['source'] == mic and report['sink'] == ear and report['dont_move']
                    if name == 'lost_mic':
                        source = microphone()
                # Bad names fail before any PCM stream or output artifact is opened.
                bad = subprocess.run([str(binary), 'alsa_input.platform-sound', ear,
                    str(root / 'bad.raw'), str(play), str(root / 'bad.json'), '1'], env=env)
                assert bad.returncode == 2 and not (root / 'bad.raw').exists()
                print('PASS: real Pulse duplex, exact capture length, source/sink removal with rescue module present, radio-name rejection. Virtual devices only.')
            finally:
                server.terminate()
                server.wait(timeout=5)


if __name__ == '__main__': main()
