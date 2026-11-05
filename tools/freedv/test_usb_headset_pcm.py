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
                process = subprocess.Popen([str(binary), '-', ear, '-', str(play),
                    str(root / 'playback-only.json'), '0'], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate(timeout=15)
                assert process.returncode == 0, (stderr, (root / 'playback-only.json').read_text())
                report = json.loads((root / 'playback-only.json').read_text())
                assert report['captured_bytes'] == 0 and report['drained'] and report['playback_bytes'] == play.stat().st_size
                assert report['phase'] == 'complete' and report['playback_prebuffer_bytes'] > 0
                # Exercise EOF below the prebuffer threshold after an idle/suspend
                # transition: draining must release the short tail, not wait for
                # another write to fill the buffer.
                pactl('suspend-sink', ear, '1')
                pactl('suspend-sink', ear, '0')
                short = root / 'short.raw'
                short.write_bytes(b'\x10\x00' * 441)
                process = subprocess.Popen([str(binary), '-', ear, '-', str(short),
                    str(root / 'short-tail.json'), '0'], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate(timeout=10)
                assert process.returncode == 0, (stderr, (root / 'short-tail.json').read_text())
                report = json.loads((root / 'short-tail.json').read_text())
                assert report['drained'] and report['playback_bytes'] == short.stat().st_size
                if os.environ.get('TEST_PULSE_DROP_DRAIN_ACK_SO'):
                    # Playback is real; the test interposer withholds only the
                    # callback, reproducing the returned client's observation.
                    stalled_env = dict(env, LD_PRELOAD=os.environ['TEST_PULSE_DROP_DRAIN_ACK_SO'])
                    process = subprocess.Popen([str(binary), '-', ear, '-', str(short),
                        str(root / 'missing-drain.json'), '0'], env=stalled_env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout, stderr = process.communicate(timeout=10)
                    report = json.loads((root / 'missing-drain.json').read_text())
                    assert process.returncode == 1 and report['phase'] == 'drain', (stderr, report)
                    assert report['playback_bytes'] == short.stat().st_size and not report['drained']
                    assert not report['ok'] and report['fault'] == 'Audio operation deadline exceeded'
                    assert report['timing_valid'] and report['drain_wait_ms'] >= 5000
                    print('PASS: missing drain callback remains a failure with EOF timing diagnostics.')
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
                print('PASS: real Pulse duplex, playback-only, short EOF after suspend/resume, source/sink removal with rescue module present, radio-name rejection. Virtual devices only.')
            finally:
                server.terminate()
                server.wait(timeout=5)


if __name__ == '__main__': main()
