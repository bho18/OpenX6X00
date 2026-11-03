#!/usr/bin/env python3
"""Collect existing evidence or run the adapter against isolated virtual devices.

Never sends a radio command, changes a mixer, opens the physical sound card,
arms RF, restarts the GUI, or restarts the system PulseAudio server.
"""
import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def command(args, timeout=5, env=None):
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=timeout, env=env)
        return {'command': args, 'exit': p.returncode, 'stdout': p.stdout[:262144], 'stderr': p.stderr[:16384]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'command': args, 'status': 'BLOCKED', 'reason': str(e)}


def input_activity(path):
    """PCM activity, not speech recognition. Exclude the route-switch transient.

    A near-constant ADC/DC input cannot establish a successful speech test.
    A varying signal still requires microphone identity and listening review.
    """
    with wave.open(str(path)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if (rate, channels, width) != (44100, 1, 2):
            return {'status': 'UNASSESSED', 'reason': 'Unsupported capture format'}
        frames = w.getnframes()
        if frames > rate * 600:
            return {'status': 'UNASSESSED', 'reason': 'Capture exceeds bounded analysis length'}
        raw = w.readframes(frames)
        if len(raw) != frames * width:
            raise ValueError('Truncated capture PCM')
    pcm = array('h'); pcm.frombytes(raw)
    if sys.byteorder != 'little': pcm.byteswap()
    steady = pcm[rate // 5:]
    if len(steady) < rate // 4:
        return {'status': 'UNASSESSED', 'reason': 'Too little post-transient capture', 'samples': frames}
    mean = sum(steady) / len(steady)
    rms = math.sqrt(sum((x - mean) ** 2 for x in steady) / len(steady))
    peak = max(abs(min(steady) - mean), abs(max(steady) - mean))
    return {'status': 'NEAR_CONSTANT' if rms <= 2 and peak <= 16 else 'ACTIVITY_PRESENT',
            'samples': frames, 'seconds': frames / rate, 'excluded_initial_ms': 200,
            'steady_mean': mean, 'steady_ac_rms': rms, 'steady_ac_peak': peak,
            'listening': 'UNASSESSED'}


def assess_voice_case(folder, case):
    audio = case.get('audio', {})
    if audio.get('kind') != 6:
        return {'status': 'UNASSESSED', 'reason': 'Not a persistent microphone TX case'}
    failures = []
    activity = {'status': 'UNASSESSED'}
    try:
        activity = input_activity(folder / 'capture.wav')
    except (OSError, EOFError, ValueError, wave.Error) as e:
        activity['reason'] = str(e)
    if activity['status'] == 'NEAR_CONSTANT': failures.append('No usable microphone activity in saved PCM')
    if case.get('audio_failures') or audio.get('first_audio_fault'):
        failures.append('Audio engine or adapter fault')
    generation = audio.get('generation')
    early = [e for e in audio.get('playback_underflow_events', [])
             if generation is not None and e.get('generation') == generation and
             e.get('drain_requested') is False and e.get('cancellation_requested') is False]
    if early: failures.append('Playback underflow before graceful drain')
    complete = (case.get('restoration_complete') is True and audio.get('persistent', {}).get('finished') == 1
                and case.get('audio_failures') == 0 and audio.get('first_audio_fault') == 0)
    return {'status': 'FAIL' if failures else 'INCONCLUSIVE',
            'mechanics': 'COMPLETED' if complete else 'INCOMPLETE_OR_FAILED',
            'failures': failures, 'input_activity': activity, 'pre_drain_underflows': early,
            'capture_longest_callback_gap_ms': audio.get('capture_longest_callback_gap_ms'),
            'pump_longest_gap_ms': audio.get('pump_longest_gap_ms'),
            'playback_observe_longest_ms': audio.get('playback_observe_longest_ms'),
            'speaker_level_before': case.get('radio_before', {}).get('speaker_level'),
            'listening': 'UNASSESSED', 'hardware_qualified': False}


def assess_collected(out):
    issues, builds, cases = [], set(), []
    for session in sorted((out/'radio/tmp').glob('freedv-native-*')):
        manifest = session/'build-manifest.json'
        if not manifest.is_file():
            issues.append(str(session.name)+': missing build identity')
        else:
            builds.add(json.loads(manifest.read_text()).get('firmware_commit', 'UNKNOWN'))
        for folder in sorted(session.glob('case-*')):
            result = folder/'coordinator.json'
            if not result.is_file():
                issues.append(folder.name+': missing completed coordinator result'); continue
            case = json.loads(result.read_text()); audio = case.get('audio', {})
            if not case.get('restoration_complete'):
                issues.append(folder.name+': restoration incomplete')
            required = ['capture.wav'] + (['output.wav'] if audio.get('kind') != 0 else [])
            for name in required:
                try:
                    with wave.open(str(folder/name)) as w:
                        if (w.getframerate(),w.getnchannels(),w.getsampwidth()) != (44100,1,2):
                            issues.append(folder.name+'/'+name+': wrong recording format')
                except (OSError, EOFError, wave.Error):
                    issues.append(folder.name+'/'+name+': missing or invalid recording')
            cases.append({'case': folder.name, 'name': case.get('name'), 'fault': audio.get('first_audio_fault'),
                          'native_failure_line': audio.get('native_failure_line'), 'failures': case.get('audio_failures'),
                          'microphone_ac_rms': audio.get('input_ac_rms'), 'listening': 'UNASSESSED',
                          'session': session.name, 'voice_assessment': assess_voice_case(folder, case)})
    if len(builds)>1 or 'UNKNOWN' in builds: issues.append('Mixed or unknown build identities; assess each build separately')
    return {'issues': issues, 'builds': sorted(builds), 'cases': cases}


def collect(out):
    records = []
    for args in (['uname', '-a'], ['uptime'], ['df', '-k', '/tmp', '/mnt'],
                 ['pactl', 'info'], ['pactl', 'list', 'sources'], ['pactl', 'list', 'sinks'],
                 ['pactl', 'list', 'sink-inputs'], ['pactl', 'list', 'source-outputs'], ['amixer', 'contents']):
        records.append(command(args))
    (out/'inventory.json').write_text(json.dumps(records, indent=2)+'\n')
    sources = [Path('/usr/share/x6100/freedv/build-manifest.json'), Path('/tmp/x6100_log.txt')]
    sources += sorted(Path('/mnt/app_logs').glob('x6100_log_*.txt'), key=lambda p: p.stat().st_mtime)[-5:]
    sources += list(Path('/tmp').glob('freedv-native-*/**/*'))
    sources += list(Path('/tmp').glob('freedv-capture-watch-*/**/*'))
    sources += list(Path('/tmp').glob('freedv-capture-source-watch-*/**/*'))
    sources += [Path('/tmp/freedv-capture-source-watch-start.log')]
    copied, skipped, total = [], [], 0
    for src in sorted(set(sources)):
        if src.is_symlink() or not src.is_file():
            continue
        size = src.stat().st_size
        if size > 16*1024*1024 or total+size > 256*1024*1024:
            skipped.append(str(src)); continue
        dst = out/'radio'/str(src).lstrip('/')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst); total += size
        copied.append({'path': str(dst.relative_to(out)), 'sha256': sha(dst), 'bytes': size})
    assessment=assess_collected(out)
    return {'status': 'COLLECTED' if not skipped and not assessment['issues'] else 'INCONCLUSIVE', 'assessment': assessment, 'files': copied,
            'skipped': skipped, 'listening': 'UNASSESSED', 'hardware_qualified': False}


def probe(out, expected_gui, expected_probe, probe_binary=None):
    binary = Path(probe_binary) if probe_binary else Path('/opt/freedv-benchmark/bin/freedv_voice_pulse_probe')
    manifest_path = Path('/usr/share/x6100/freedv/build-manifest.json')
    if not binary.is_file() or not manifest_path.is_file() or not shutil.which('pulseaudio'):
        return {'status': 'BLOCKED', 'reason': 'New candidate/probe/PulseAudio is missing'}
    manifest = json.loads(manifest_path.read_text())
    if not expected_gui or not expected_probe:
        return {'status': 'BLOCKED', 'reason': 'Provide hashes from the downloaded candidate-manifest.json'}
    if sha(Path('/usr/sbin/x6100_gui')) != expected_gui or sha(binary) != expected_probe or manifest.get('gui_sha256') != expected_gui:
        return {'status': 'BLOCKED', 'reason': 'Installed files do not match the selected candidate'}
    modules = command(['pulseaudio', '--dump-modules'])
    (out/'pulse-modules.json').write_text(json.dumps(modules, indent=2)+'\n')
    if any(name not in modules.get('stdout', '') for name in ('module-null-sink', 'module-null-source', 'module-native-protocol-unix')):
        return {'status': 'BLOCKED', 'reason': 'Private virtual-device modules are unavailable; no physical-device fallback'}
    if shutil.disk_usage('/tmp').free < 32*1024*1024:
        return {'status': 'BLOCKED', 'reason': 'Less than 32 MiB free in /tmp'}
    with tempfile.TemporaryDirectory(prefix='freedv-pulse-probe-', dir='/tmp') as temp:
        private = Path(temp); (private/'config').mkdir()
        conf = private/'default.pa'
        # The default null sink renders a two-second block before any stream
        # requests lower latency. Cold playback can then stall at tlength and
        # trip the real engine's unchanged 500 ms backlog protection. The
        # documented PA 16.1 norewinds option caps this virtual fixture at 50 ms.
        conf.write_text('.fail\n'
            f'load-module module-native-protocol-unix socket={private}/native auth-anonymous=1\n'
            'load-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2 norewinds=1\n'
            'load-module module-null-source source_name=freedv_probe_source rate=44100 channels=2\n'
            'set-default-sink freedv_probe_sink\nset-default-source freedv_probe_source\n')
        env = dict(os.environ, PULSE_SERVER=f'unix:{private}/native', PULSE_RUNTIME_PATH=str(private),
                   PULSE_STATE_PATH=str(private), XDG_CONFIG_HOME=str(private/'config'),
                   LD_LIBRARY_PATH='/opt/freedv-benchmark/lib:/usr/lib/pulseaudio')
        with (out/'private-pulse.log').open('w') as log:
            server = subprocess.Popen(['pulseaudio', '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1',
                '--disable-shm', '--realtime=no', '--high-priority=no', '-nF', str(conf)], env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic()+5
                while not (private/'native').exists() and server.poll() is None and time.monotonic()<deadline:
                    time.sleep(.05)
                if server.poll() is not None or not (private/'native').exists():
                    return {'status': 'BLOCKED', 'reason': 'Private PulseAudio could not start; see log'}
                (out/'fixture.json').write_text(json.dumps({'sink_module': 'module-null-sink',
                    'norewinds': True, 'maximum_block_ms': 50, 'sample_rate': 44100,
                    'engine_backlog_limit_ms': 500, 'probe_binary': str(binary),
                    'pulse_version': command(['pulseaudio', '--version']),
                    'private_sinks': command(['pactl', 'list', 'sinks'], env=env),
                    'private_sources': command(['pactl', 'list', 'sources'], env=env)}, indent=2)+'\n')
                with (out/'adapter.log').open('w') as testlog:
                    try:
                        test = subprocess.run([str(binary), str(out)], env=env, stdout=testlog,
                                              stderr=subprocess.STDOUT, timeout=55)
                    except subprocess.TimeoutExpired:
                        return {'status': 'FAIL', 'reason': 'Adapter probe exceeded 55 seconds'}
                turns = [json.loads(p.read_text()) for p in sorted(out.glob('turn-*/result.json'))]
                passed = test.returncode == 0 and len(turns) == 4 and all(
                    t['failures'] == 0 and t['eoo'] == t['turn'] and t['rade_creations'] == 1 and
                    t['input_samples'] > 300000 and t['output_samples'] > 300000 and t['flush_acknowledged'] for t in turns)
                return {'status': 'PASS' if passed else 'FAIL', 'scope': 'Four retained turns on private virtual devices',
                        'exit': test.returncode, 'turns': turns, 'gui_sha256': expected_gui, 'probe_sha256': expected_probe,
                        'hardware_qualified': False, 'speech_intelligibility': 'UNASSESSED'}
            finally:
                if server.poll() is None:
                    server.terminate()
                    try: server.wait(timeout=3)
                    except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('collect', 'probe'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--gui-sha256'); p.add_argument('--probe-sha256')
    p.add_argument('--probe-binary', type=Path, help='Explicit staged probe; never replaces the installed GUI or probe')
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=False)
    try:
        result = collect(a.output) if a.action == 'collect' else probe(a.output, a.gui_sha256, a.probe_sha256, a.probe_binary)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        result = {'status': 'INCONCLUSIVE', 'reason': str(e), 'hardware_qualified': False}
    (a.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    (a.output/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.relative_to(a.output).as_posix()+'\n'
        for f in sorted(a.output.rglob('*')) if f.is_file() and f.name != 'SHA256SUMS'))
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('PASS', 'COLLECTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
