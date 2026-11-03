#!/usr/bin/env python3
"""Collect existing evidence or run the adapter against isolated virtual devices.

Never sends a radio command, changes a mixer, opens the physical sound card,
arms RF, restarts the GUI, or restarts the system PulseAudio server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
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
                          'microphone_ac_rms': audio.get('input_ac_rms'), 'listening': 'UNASSESSED'})
    if len(builds)>1 or 'UNKNOWN' in builds: issues.append('Mixed or unknown build identities; assess each build separately')
    return {'issues': issues, 'builds': sorted(builds), 'cases': cases}


def collect(out):
    records = []
    for args in (['uname', '-a'], ['uptime'], ['df', '-k', '/tmp', '/mnt'],
                 ['pactl', 'info'], ['pactl', 'list', 'sources'], ['pactl', 'list', 'sinks'],
                 ['pactl', 'list', 'sink-inputs'], ['amixer', 'contents']):
        records.append(command(args))
    (out/'inventory.json').write_text(json.dumps(records, indent=2)+'\n')
    sources = [Path('/usr/share/x6100/freedv/build-manifest.json'), Path('/tmp/x6100_log.txt')]
    sources += sorted(Path('/mnt/app_logs').glob('x6100_log_*.txt'), key=lambda p: p.stat().st_mtime)[-5:]
    sources += list(Path('/tmp').glob('freedv-native-*/**/*'))
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


def probe(out, expected_gui, expected_probe):
    binary = Path('/opt/freedv-benchmark/bin/freedv_voice_pulse_probe')
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
        conf.write_text('.fail\n'
            f'load-module module-native-protocol-unix socket={private}/native auth-anonymous=1\n'
            'load-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2\n'
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
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=False)
    try:
        result = collect(a.output) if a.action == 'collect' else probe(a.output, a.gui_sha256, a.probe_sha256)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        result = {'status': 'INCONCLUSIVE', 'reason': str(e), 'hardware_qualified': False}
    (a.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    (a.output/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.relative_to(a.output).as_posix()+'\n'
        for f in sorted(a.output.rglob('*')) if f.is_file() and f.name != 'SHA256SUMS'))
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('PASS', 'COLLECTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
