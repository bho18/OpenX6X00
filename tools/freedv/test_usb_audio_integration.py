#!/usr/bin/env python3
"""Actual codec/four-stream integration, physical rates, fake radio transport.

No radio is contacted. Each case retains all evidence, including on failure.
The test-only PA interposer affects only the test executable, not Pulse itself.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

CASES = (
    'normal', 'rx-signal', 'block-key', 'stop-during-key', 'release-during-key',
    'pa-delay', 'pa-reject', 'pa-missing', 'inactive-hole', 'active-hole',
    'inactive-stale', 'inactive-radio-hole', 'inactive-radio-stale', 'prekey-stale', 'capture-stale', 'stale-status', 'disconnect', 'stall', 'tx-stall',
    'rf-disabled', 'qualification-physical-tx', 'qualification-held-ptt',
    'cold-resume', 'cold-recovery-fails', 'cold-stop', 'cold-release', 'cold-qualification', 'cold-prekey-unexpected-tx', 'cold-rx-resume',
)
SPEECH_CASES = {'normal', 'rx-signal', 'block-key', 'pa-delay', 'inactive-hole', 'inactive-stale', 'inactive-radio-hole', 'inactive-radio-stale', 'rf-disabled'}
COLD_CASES = {'cold-resume', 'cold-recovery-fails', 'cold-stop', 'cold-release', 'cold-qualification', 'cold-prekey-unexpected-tx', 'cold-rx-resume'}
INJECT_CASES = COLD_CASES | {'pa-delay', 'pa-reject', 'pa-missing', 'inactive-hole', 'active-hole', 'inactive-stale', 'inactive-radio-hole', 'inactive-radio-stale', 'prekey-stale', 'capture-stale'}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def injection_environment(case, root, library):
    if case not in INJECT_CASES:
        return {}
    if not library or not library.is_file():
        raise ValueError('This case requires --fault-inject-so')
    result = {'LD_PRELOAD': str(library)}
    if case in COLD_CASES:
        result.update(USB_TEST_PA_MODE='delay', USB_TEST_PA_DEVICE='radio_sink_fixture',
                      USB_TEST_PA_ACTION='uncork', USB_TEST_PA_DELAY_MS='760', USB_TEST_COLD_CAPTURE='1',
                      USB_TEST_COLD_RESUME_MS='200' if case in {'cold-stop', 'cold-release'} else '30')
        if case == 'cold-qualification':
            result.update(USB_TEST_STALE_DEVICE='alsa_input.usb-fixture', USB_TEST_STALE_TRIGGER=str(root / 'inject-stale'))
        if case == 'cold-rx-resume':
            result.update(USB_TEST_PA_ACTION='cork', USB_TEST_PA_MATCH='3', USB_TEST_COLD_RESUME_MS='1000')
        if case == 'cold-recovery-fails':
            result['USB_TEST_COLD_NEVER'] = '1'
    elif case.startswith('pa-'):
        result.update(USB_TEST_PA_MODE={'pa-delay': 'delay', 'pa-reject': 'reject', 'pa-missing': 'missing'}[case],
                      USB_TEST_PA_DEVICE='radio_sink_fixture', USB_TEST_PA_ACTION='flush', USB_TEST_PA_DELAY_MS='650')
    elif case.endswith('hole'):
        result.update(USB_TEST_HOLE_DEVICE='radio_source_fixture' if case == 'inactive-radio-hole' else 'alsa_input.usb-fixture', USB_TEST_HOLE_TRIGGER=str(root / 'inject-hole'))
    elif case.endswith('stale'):
        result.update(USB_TEST_STALE_DEVICE='radio_source_fixture' if case == 'inactive-radio-stale' else 'alsa_input.usb-fixture', USB_TEST_STALE_TRIGGER=str(root / 'inject-stale'))
    return result


def run_case(args):
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='freedv-usb-test-'))
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'result').exists():
        raise ValueError('Case output must be fresh: ' + str(root))
    (root / 'result').mkdir()
    print('Evidence:', root, flush=True)
    record = {'schema': 'x6200.usb.integration.case.v2', 'status': 'FAIL', 'case': args.case,
              'scope': 'Real codec and Pulse streams at representative rates; fake radio control, no hardware RF',
              'radio_accessed': False, 'test_binary_sha256': sha(args.binary)}
    env = dict(os.environ, PULSE_SERVER=f'unix:{root}/native', PULSE_RUNTIME_PATH=str(root),
               PULSE_STATE_PATH=str(root), XDG_CONFIG_HOME=str(root))
    conf = root / 'default.pa'
    # An ordinary GUI capture client is also retained against the radio source.
    # The microphone physical side is 16k mono, headphone side 48k stereo/24bit,
    # and both radio directions 44.1k stereo. The controller's own client specs
    # remain 48k mono USB and 44.1k mono radio, exercising Pulse conversion.
    conf.write_text('.fail\n' + f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n' +
        'load-module module-null-sink sink_name=mic_fixture rate=16000 channels=1 format=s16le norewinds=1\n' +
        'load-module module-remap-source source_name=alsa_input.usb-fixture master=mic_fixture.monitor rate=16000 channels=1\n' +
        'load-module module-null-sink sink_name=rf_fixture rate=44100 channels=2 format=s16le norewinds=1\n' +
        'load-module module-remap-source source_name=radio_source_fixture master=rf_fixture.monitor rate=44100 channels=2\n' +
        'load-module module-null-sink sink_name=alsa_output.usb-fixture rate=48000 channels=2 format=s24le norewinds=1\n' +
        'load-module module-null-sink sink_name=radio_sink_fixture rate=44100 channels=2 format=s16le norewinds=1\n' +
        'load-module module-null-sink sink_name=unrelated_sink norewinds=1\nload-module module-rescue-streams\n')
    pulse_args = ['pulseaudio', '-nF', str(conf), '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1']
    modules = os.environ.get('TEST_PULSE_MODULES')
    if not modules:
        choices = list(Path(shutil.which('pulseaudio')).resolve().parents[1].glob('lib/pulse-*/modules'))
        if choices:
            modules = str(choices[0])
    if modules:
        pulse_args += ['--dl-search-path', modules]
        env['LD_LIBRARY_PATH'] = modules + os.pathsep + env.get('LD_LIBRARY_PATH', '')
    stop = threading.Event()
    producers, threads = [], []
    recorder = legacy_capture = server = None
    recording = None

    def produce(sink, file, raw_rate=None):
        while not stop.is_set():
            command = ['paplay', '--device=' + sink]
            if raw_rate:
                command += ['--raw', '--rate=' + str(raw_rate), '--format=s16le', '--channels=1']
            process = subprocess.Popen(command + [str(file)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            producers.append(process)
            process.wait()

    def endpoints(name):
        text = ''
        for kind in ('sources', 'sinks'):
            result = subprocess.run(['pactl', 'list', 'short', kind], env=env, capture_output=True, text=True, check=True)
            text += kind + '\n' + result.stdout
        (root / name).write_text(text)
        return text

    try:
        with (root / 'pulse.log').open('w') as log:
            server = subprocess.Popen(pulse_args, env=env, stdout=log, stderr=log)
            for _ in range(150):
                if server.poll() is not None:
                    raise RuntimeError((root / 'pulse.log').read_text())
                if subprocess.run(['pactl', 'info'], env=env, capture_output=True).returncode == 0:
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('Private Pulse did not become ready')
            legacy_capture = subprocess.Popen(['parec', '--device=radio_source_fixture', '--rate=44100', '--channels=2',
                                               '--format=s16le', '--latency-msec=10'], env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            silence = root / 'unsynchronized-radio.raw'
            silence.write_bytes(b'\0' * (44100 * 2 * 3))
            radio = args.modem if args.case == 'rx-signal' else silence
            for sink, file, rate in [('mic_fixture', args.speech, None),
                                     ('rf_fixture', radio, 48000 if args.case == 'rx-signal' else 44100)]:
                thread = threading.Thread(target=produce, args=(sink, file, rate), daemon=True)
                threads.append(thread)
                thread.start()
            time.sleep(.15)
            before = endpoints('endpoints-before.txt')
            assert re.search(r'alsa_input\.usb-fixture\s+[^\n]*1ch 16000Hz', before), before
            assert re.search(r'alsa_output\.usb-fixture\s+[^\n]*2ch 48000Hz', before), before
            assert re.search(r'radio_sink_fixture\s+[^\n]*2ch 44100Hz', before), before
            recording = (root / 'radio-output.raw').open('wb')
            recorder = subprocess.Popen(['parec', '--device=radio_sink_fixture.monitor', '--rate=44100', '--format=s16le',
                                         '--channels=1', '--latency-msec=20'], env=env, stdout=recording, stderr=subprocess.DEVNULL)
            child_env = dict(env, **injection_environment(args.case, root, args.fault_inject_so))
            with (root / 'test.log').open('w') as output:
                test = subprocess.Popen([str(args.binary), str(root / 'result'), '--' + args.case], env=child_env,
                                        stdout=output, stderr=subprocess.STDOUT)
                until = time.monotonic() + 75
                changed = False
                while test.poll() is None:
                    if time.monotonic() > until:
                        test.kill()
                        test.wait()
                        raise RuntimeError('Controller scenario timed out; retained evidence: ' + str(root))
                    text = (root / 'test.log').read_text()
                    if not changed and 'TX_ACTIVE' in text:
                        endpoints('endpoints-active.txt')
                        changed = True
                        if args.case == 'disconnect':
                            subprocess.run(['pactl', 'unload-module', 'module-remap-source'], env=env, check=True)
                    time.sleep(.02)
                code = test.wait()
            print((root / 'test.log').read_text(), flush=True)
            if code:
                raise RuntimeError('Controller scenario failed with exit ' + str(code))
            harness = json.loads((root / 'result/harness-result.json').read_text())
            assert harness['status'] == 'PASS' and not harness['physical_tx'], harness
            rows = [json.loads(line) for line in (root / 'result/session.jsonl').read_text().splitlines() if line.strip()]
            status_rows = [row for row in rows if 'state' in row]
            assert status_rows and status_rows[-1]['state'] in (5, 6), status_rows[-1:]  # FAULT/DONE
            # Unknown/stale telemetry must not be misrepresented as confirmed RX.
            if status_rows[-1].get('rf_valid'):
                assert not status_rows[-1]['rf_tx'], status_rows[-1]
            if args.case in INJECT_CASES:
                assert 'INJECT ' in (root / 'test.log').read_text(), 'Fault injector did not exercise requested path'
            if args.case in COLD_CASES:
                log = (root / 'test.log').read_text()
                assert 'INJECT cold ACK success=1' in log, log
                assert 'callback starvation device=alsa_input.usb-fixture' in log, log
                assert 'callback starvation device=radio_source_fixture' in log, log
                if args.case == 'cold-qualification':
                    samples = [row for row in rows if row.get('kind') == 'sample' and row.get('qualification_complete')]
                    assert samples, 'Qualification did not publish rate-accounting evidence'
                    last = samples[-1]
                    assert all(value >= 760 for value in last['preparation_excluded_ms']), last
                    assert all(value > 0 for value in last['preparation_excluded_frames']), last
                    # MIC delivery stopped before preparation; that earlier gap
                    # must remain in the rate window rather than be excluded.
                    assert harness['mic_max_gap_ms'] >= harness['rx_max_gap_ms'] + 150, harness
                    assert abs(last['preparation_excluded_ms'][0] - last['preparation_excluded_ms'][1]) < 100, last
                    assert last['mic_rate_window_ms'] >= 3000 and last['rx_rate_window_ms'] >= 3000, last
                    assert 45600 <= last['mic_rate_hz'] <= 50400 and 41895 <= last['rx_rate_hz'] <= 46305, last
                    record['preparation_accounting'] = {key: last[key] for key in
                        ('preparation_excluded_ms', 'preparation_excluded_frames', 'mic_rate_hz', 'rx_rate_hz',
                         'mic_rate_window_ms', 'rx_rate_window_ms')}
            record['harness'] = harness
            record['event_count'] = len(rows)
            record['status_row_count'] = len(status_rows)
            if recorder:
                recorder.terminate()
                recorder.wait(timeout=5)
                recorder = None
                recording.close()
                recording = None
            runner = args.runner or (Path(os.environ['USB_RF_REFERENCE_RUNNER']) if os.environ.get('USB_RF_REFERENCE_RUNNER') else None)
            if runner and args.case in SPEECH_CASES:
                pcm = (root / 'radio-output.raw').read_bytes()
                seconds = math.ceil(len(pcm) / 88200) + 1
                pcm += b'\0' * (seconds * 88200 - len(pcm))
                padded = root / 'radio-padded.raw'
                padded.write_bytes(pcm)
                decoded = root / 'decoded-radio-output'
                decoded.mkdir()
                with (root / 'loopback-decode.log').open('w') as log:
                    subprocess.run([str(runner), '--rx', str(padded), '--rate', '44100', '--seconds', str(seconds),
                                    '--output', str(decoded)], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=75)
                stats = json.loads((decoded / 'engine-stats.json').read_text())
                assert not stats['failures'] and stats['synchronized_blocks'] > 0 and stats['synthesis_blocks'] > 0, stats
                record['actual_sink_decode'] = stats
                print('PASS actual radio modem sink audio decoded:', stats['synthesis_blocks'], 'speech blocks', flush=True)
            record['status'] = 'PASS'
            print('PASS scenario:', args.case, flush=True)
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        for process in (recorder, legacy_capture):
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        if recording:
            recording.close()
        stop.set()
        for process in producers:
            if process.poll() is None:
                process.terminate()
        for thread in threads:
            thread.join(timeout=3)
        if server and server.poll() is None:
            server.terminate()
            server.wait(timeout=5)
        save(root / 'case-result.json', record)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('speech', type=Path)
    parser.add_argument('modem', type=Path)
    parser.add_argument('--case', choices=CASES, default='normal')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--runner', type=Path)
    parser.add_argument('--fault-inject-so', type=Path)
    parser.add_argument('--disconnect', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--stall', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ('binary', 'speech', 'modem', 'runner', 'fault_inject_so'):
        value = getattr(args, name)
        if value:
            setattr(args, name, value.resolve())
    if args.disconnect:
        args.case = 'disconnect'
    if args.stall:
        args.case = 'stall'
    run_case(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
