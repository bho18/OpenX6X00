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
import threading
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
    # A single route/PTT transient can dominate whole-turn RMS. Describe its
    # occupancy rather than treating one large spike as sustained mic input.
    window = rate // 100
    quiet, windows, active_run, longest_active_run = 0, 0, 0, 0
    for start in range(0, len(steady), window):
        chunk = steady[start:start + window]
        if len(chunk) != window: break
        center = sum(chunk) / window
        variance = sum((x - center) ** 2 for x in chunk) / window
        windows += 1
        if variance <= 4:
            quiet += 1; active_run = 0
        else:
            active_run += 1; longest_active_run = max(longest_active_run, active_run)
    status = 'NEAR_CONSTANT' if rms <= 2 and peak <= 16 else 'ACTIVITY_PRESENT'
    if status == 'ACTIVITY_PRESENT' and quiet / windows >= .98 and longest_active_run < 10:
        status = 'BRIEF_TRANSIENTS_ONLY'
    return {'status': status,
            'samples': frames, 'seconds': frames / rate, 'excluded_initial_ms': 200,
            'steady_mean': mean, 'steady_ac_rms': rms, 'steady_ac_peak': peak,
            'activity_window_ms': 10, 'quiet_window_fraction': quiet / windows,
            'longest_active_run_ms': longest_active_run * 10,
            'listening': 'UNASSESSED'}


def assess_voice_case(folder, case):
    audio = case.get('audio', {})
    capture_case=audio.get('kind')==0 and (case.get('selected_case') in range(23,27) or case.get('name','').startswith('Record '))
    if audio.get('kind') != 6 and not capture_case:
        return {'status': 'UNASSESSED', 'reason': 'Not a persistent microphone TX case'}
    failures = []
    activity = {'status': 'UNASSESSED'}
    try:
        activity = input_activity(folder / 'capture.wav')
    except (OSError, EOFError, ValueError, wave.Error) as e:
        activity['reason'] = str(e)
    if activity['status'] == 'NEAR_CONSTANT': failures.append('No usable microphone activity in saved PCM')
    if activity['status'] == 'BRIEF_TRANSIENTS_ONLY':
        failures.append('Only brief transients over near-constant input; no sustained microphone activity')
    if case.get('audio_failures') or audio.get('first_audio_fault'):
        failures.append('Audio engine or adapter fault')
    generation = audio.get('generation')
    early = [e for e in audio.get('playback_underflow_events', [])
             if generation is not None and e.get('generation') == generation and
             e.get('drain_requested') is False and e.get('cancellation_requested') is False]
    if early: failures.append('Playback underflow before graceful drain')
    complete = (case.get('restoration_complete') is True and (capture_case or audio.get('persistent', {}).get('finished') == 1)
                and case.get('audio_failures') == 0 and audio.get('first_audio_fault') == 0)
    return {'status': 'FAIL' if failures else 'INCONCLUSIVE',
            'mechanics': 'COMPLETED' if complete else 'INCOMPLETE_OR_FAILED',
            'failures': failures, 'input_activity': activity, 'pre_drain_underflows': early,
            'capture_longest_callback_gap_ms': audio.get('capture_longest_callback_gap_ms'),
            'pump_longest_gap_ms': audio.get('pump_longest_gap_ms'),
            'playback_observe_longest_ms': audio.get('playback_observe_longest_ms'),
            'speaker_level_before': case.get('radio_before', {}).get('speaker_level'),
            'listening': 'UNASSESSED', 'hardware_qualified': False}


def read_json_evidence(path, issues):
    try:return json.loads(path.read_text())
    except (OSError,ValueError) as e:
        issues.append(str(path.name)+': missing or invalid JSON evidence ('+str(e)+')')
        return {}


def assess_control_trace(folder, case):
    issues=[];failures=[]
    try:
        rows=[json.loads(line) for line in (folder/'control-trace.jsonl').read_text().splitlines() if line.strip()]
    except (OSError,ValueError) as e:
        return {'status':'INCONCLUSIVE','issues':['Missing or invalid control trace: '+str(e)],'failures':[]}
    if not rows or rows[-1].get('trace_complete') is not True or rows[-1].get('discarded')!=0:
        issues.append('Control trace is incomplete or contains discarded events')
    for row in rows:
        if row.get('kind') in ('control_write','full_register_keepalive_or_explicit_resend') or row.get('phase') in ('command','bulk_resend'):
            if row.get('transport_success') is False:failures.append(row)
            elif row.get('transport_success') is not True:issues.append('Control transport result is missing')
    if case.get('selected_case') in range(23,27) and case.get('record_stop_exact') is not True:
        issues.append('Recorder case did not establish exact stop without recovery fallback')
    return {'status':'FAIL' if failures else 'INCONCLUSIVE' if issues else 'COMPLETE',
            'issues':issues,'failures':failures,'physical_readback':False}


def watch_recordings_complete(watch, completion, issues):
    paths=sorted(watch.glob('capture*.wav'))
    count=completion.get('chunks',1);samples=completion.get('samples');channels=completion.get('channels',1)
    if not isinstance(count,int) or not 1<=count<=61 or not isinstance(samples,int) or not 0<samples<=44100*602 or channels not in (1,2):
        issues.append(watch.name+': invalid source sample accounting');return False
    expected_names=[f'capture-{i:04}.wav' for i in range(1,count+1)] if 'chunks' in completion else ['capture.wav']
    valid=True;total=0
    if [p.name for p in paths]!=expected_names or samples is None:
        issues.append(watch.name+': source recording missing chunks or sample accounting');return False
    for path in paths:
        try:
            with wave.open(str(path)) as w:
                frames=w.getnframes();total+=frames
                if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(44100,channels,2):valid=False
                remaining=frames
                while remaining:
                    n=min(65536,remaining)
                    if len(w.readframes(n))!=n*channels*2:valid=False;break
                    remaining-=n
        except (OSError,EOFError,wave.Error):valid=False
    if total!=samples:valid=False
    if not valid:issues.append(watch.name+': source recording format, truncation or sample-accounting mismatch')
    return valid


def assess_collected(out, expected=None):
    issues, builds, cases, profiles = [], set(), [], set()
    stages=out/'radio/tmp/freedv-agent-stages'
    watchers=[]
    for watch in sorted((out/'radio/tmp').glob('freedv-capture-source-watch-*')):
        completion=read_json_evidence(watch/'complete.json',issues)
        identity=read_json_evidence(watch/'identity.json',issues)
        if completion.get('status')!='COLLECTED':issues.append(watch.name+': source recording incomplete')
        elif not watch_recordings_complete(watch,completion,issues):pass
        elif expected and identity.get('gui_sha256')!=expected.get('gui_sha256'):
            issues.append(watch.name+': source recording belongs to another candidate')
        else:watchers.append((watch.name,completion,identity))
    for session in sorted((out/'radio/tmp').glob('freedv-native-*')):
        identity=read_json_evidence(session/'build-manifest.json',issues)
        build=(identity.get('firmware_commit','UNKNOWN'),identity.get('gui_sha256','UNKNOWN'))
        builds.add(build)
        if expected and any(identity.get(k)!=expected.get(k) for k in ('firmware_commit','gui_sha256')):
            issues.append(session.name+': build differs from selected candidate')
        for folder in sorted(session.glob('case-*')):
            result = folder/'coordinator.json'
            if not result.is_file():
                issues.append(folder.name+': missing completed coordinator result'); continue
            case=read_json_evidence(result,issues);audio=case.get('audio',{})
            control=assess_control_trace(folder,case)
            issues.extend(folder.name+': '+item for item in control['issues'])
            if control['failures']:issues.append(folder.name+': radio command transport failed')
            if not case.get('restoration_complete'):
                issues.append(folder.name+': restoration incomplete')
            if case.get('preparation_failed') or case.get('run_started') is False:
                issues.append(folder.name+': preparation failed or run never started')
            if case.get('audio_failures') or audio.get('first_audio_fault'):
                issues.append(folder.name+': audio case failed')
            required = ['capture.wav'] + (['output.wav'] if audio.get('kind') != 0 else [])
            for name in required:
                try:
                    with wave.open(str(folder/name)) as w:
                        if (w.getframerate(),w.getnchannels(),w.getsampwidth()) != (44100,1,2):
                            issues.append(folder.name+'/'+name+': wrong recording format')
                        else:
                            remaining=w.getnframes()
                            if not remaining:issues.append(folder.name+'/'+name+': empty PCM')
                            while remaining:
                                count=min(remaining,65536);block=w.readframes(count)
                                if len(block)!=count*2:
                                    issues.append(folder.name+'/'+name+': empty or truncated PCM');break
                                remaining-=count
                except (OSError, EOFError, wave.Error):
                    issues.append(folder.name+'/'+name+': missing or invalid recording')
            request=case.get('stage_request_id',0)
            if request:
                staged=read_json_evidence(stages/(str(request)+'.json'),issues)
                if any(staged.get(k)!=identity.get(k) for k in ('firmware_commit','gui_sha256')):
                    issues.append(folder.name+': staging build identity differs from case')
                expected_selection=staged.get('selected',{})
                actual={'case':case.get('selected_case'),'route':case.get('matrix_case'),
                        'microphone_index':case.get('selected_microphone_index'),
                        'capture_channel':case.get('capture_channel'),'mixer_choice':case.get('mixer_choice')}
                if any(expected_selection.get(k)!=v for k,v in actual.items()):
                    issues.append(folder.name+': executed case/settings differ from acknowledged stage')
            # Several intentionally distinct profiles may coexist in a bundle;
            # report them separately and never pool them into one qualification.
            before=case.get('radio_before',{});vfo=before.get('active_vfo')
            profile={'mode':before.get('mode_a' if vfo==0 else 'mode_b'),
                'frequency_hz':before.get('vfo_a_hz' if vfo==0 else 'vfo_b_hz'),
                'route':case.get('matrix_case'),'microphone':case.get('microphone'),
                'capture_channel':case.get('capture_channel'),'mixer_choice':case.get('mixer_choice')}
            profile_key=json.dumps(profile,sort_keys=True);profiles.add(profile_key)
            coverage=[]
            if case.get('selected_case')==28:
                begin=case.get('record_baseline_started_monotonic_ms')
                modem=case.get('modem_started_monotonic_ms')
                end=case.get('restoration_completed_monotonic_ms')
                if begin and modem and end and begin<=modem<=end:
                    coverage=[name for name,w,ident in watchers if ident.get('gui_sha256')==identity.get('gui_sha256') and
                        w.get('first_read_monotonic_ns') is not None and w.get('last_read_monotonic_ns') is not None and
                        w['first_read_monotonic_ns']<=begin*1000000 and w['last_read_monotonic_ns']>=end*1000000]
                if not coverage:
                    issues.append(folder.name+': MODEM comparison lacks a completed matching source watch covering unkeyed baseline through restoration')
            voice=assess_voice_case(folder,case)
            if control['failures']:
                voice['status']='FAIL';voice.setdefault('failures',[]).append('Radio command transport failed')
            cases.append({'case':folder.name,'name':case.get('name'),'fault':audio.get('first_audio_fault'),
                'native_failure_line':audio.get('native_failure_line'),'failures':case.get('audio_failures'),
                'microphone_ac_rms':audio.get('input_ac_rms'),'listening':'UNASSESSED',
                'profile':profile,'stage_request_id':request,'continuous_source_watch':coverage,'control_trace':control,
                'session':session.name,'voice_assessment':voice})
    if not cases:issues.append('No completed diagnostic cases; evidence retrieval is not test completion')
    if len(builds)>1 or any('UNKNOWN' in build for build in builds):
        issues.append('Mixed or unknown build identities; assess each build separately')
    return {'issues':issues,'builds':[{'firmware_commit':b[0],'gui_sha256':b[1]} for b in sorted(builds)],
            'profiles':[json.loads(p) for p in sorted(profiles)],'cases':cases}


def copy_evidence(src, dst, storage):
    """Reuse already verified files even when there is no room for another copy."""
    size=src.stat().st_size;source_hash=sha(src)
    if not dst.is_file() or source_hash!=sha(dst):
        if shutil.disk_usage(storage).free < size + 8*1024*1024:return False
        dst.parent.mkdir(parents=True,exist_ok=True)
        partial=dst.with_name(dst.name+'.partial');shutil.copyfile(src,partial)
        if source_hash!=sha(partial) or source_hash!=sha(src):
            partial.unlink(missing_ok=True)
            raise ValueError('Source changed during collection: '+str(src))
        os.replace(partial,dst)
    return True


def collect(out, expected=None):
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
    sources += list(Path('/tmp/freedv-agent-stages').glob('*.json'))
    sources += list(Path('/tmp').glob('freedv-agent-probe-*/**/*'))
    sources += list(Path('/tmp/freedv-control').glob('*.json'))
    copied, skipped, total = [], [], 0
    for src in sorted(set(sources)):
        if src.is_symlink() or not src.is_file():
            continue
        size = src.stat().st_size
        # Incremental collection: capacity is checked against actual remaining
        # space, not a silent per-file or session-size exclusion.
        dst = out/'radio'/str(src).lstrip('/')
        try:
            if not copy_evidence(src,dst,out):
                skipped.append(str(src)+': insufficient storage');continue
        except (OSError,ValueError) as e:
            skipped.append(str(src)+': '+str(e));continue
        total += size
        copied.append({'path': str(dst.relative_to(out)), 'sha256': sha(dst), 'bytes': size})
    assessment=assess_collected(out,expected)
    return {'status': 'COLLECTED' if not skipped and not assessment['issues'] else 'INCONCLUSIVE', 'assessment': assessment, 'files': copied,
            'skipped': skipped, 'listening': 'UNASSESSED', 'hardware_qualified': False}


def probe(out, expected_gui, expected_probe, probe_binary=None, speech=None, turns=4, cycles=False, duration_ms=8000):
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
    if shutil.disk_usage(out).free < (32+2*turns)*1024*1024:
        return {'status': 'BLOCKED', 'reason': 'Insufficient space for retained-turn recordings'}
    with tempfile.TemporaryDirectory(prefix='freedv-pulse-probe-', dir='/tmp') as temp:
        private = Path(temp); (private/'config').mkdir()
        conf = private/'default.pa'
        # The default null sink renders a two-second block before any stream
        # requests lower latency. Cold playback can then stall at tlength and
        # trip the real engine's unchanged 500 ms backlog protection. The
        # documented PA 16.1 norewinds option caps this virtual fixture at 50 ms.
        conf.write_text('.fail\n'
            f'load-module module-native-protocol-unix socket={private}/native auth-anonymous=1\n'
            'load-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2 norewinds=1\n' +
            ('load-module module-null-sink sink_name=freedv_probe_input rate=44100 channels=1 norewinds=1\n' if speech else 'load-module module-null-source source_name=freedv_probe_source rate=44100 channels=2\n') +
            'set-default-sink freedv_probe_sink\nset-default-source '+('freedv_probe_input.monitor' if speech else 'freedv_probe_source')+'\n')
        env = dict(os.environ, PULSE_SERVER=f'unix:{private}/native', PULSE_RUNTIME_PATH=str(private),
                   PULSE_STATE_PATH=str(private), XDG_CONFIG_HOME=str(private/'config'),
                   LD_LIBRARY_PATH='/opt/freedv-benchmark/lib:/usr/lib/pulseaudio')
        env['FREEDV_PROBE_TURNS']=str(turns)
        env['FREEDV_PROBE_DURATION_MS']=str(duration_ms)
        if cycles:env['FREEDV_PROBE_CYCLES']='1'
        if speech:env['FREEDV_PROBE_SOURCE']='freedv_probe_input.monitor'
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
                feeder=None
                if speech:
                    with wave.open(str(speech)) as w:
                        if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(44100,1,2):
                            return {'status':'BLOCKED','reason':'Speech fixture must be 44.1kHz mono PCM16 WAV'}
                        pcm=w.readframes(w.getnframes())
                    if not pcm:return {'status':'BLOCKED','reason':'Empty speech fixture'}
                    (out/'speech-fixture.json').write_text(json.dumps({'sha256':sha(speech),'bytes':len(pcm),'listening':'UNASSESSED'})+'\n')
                    feeder=subprocess.Popen(['pacat','--playback','--raw','--format=s16le','--rate=44100','--channels=1','--device=freedv_probe_input'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
                    def feed():
                        try:
                            while feeder.poll() is None:feeder.stdin.write(pcm);feeder.stdin.flush()
                        except (OSError,ValueError):pass
                    threading.Thread(target=feed,daemon=True).start()
                with (out/'adapter.log').open('w') as testlog:
                    try:
                        test = subprocess.run([str(binary), str(out)], env=env, stdout=testlog,
                                              stderr=subprocess.STDOUT, timeout=turns*13+10)
                    except subprocess.TimeoutExpired:
                        return {'status': 'FAIL', 'reason': 'Adapter probe exceeded its bounded per-turn deadline'}
                turns = [json.loads(p.read_text()) for p in sorted(out.glob('turn-*/result.json'))]
                passed = test.returncode == 0 and len(turns) == int(env['FREEDV_PROBE_TURNS']) and all(
                    t['failures'] == 0 and t['eoo'] == (1 if cycles else t['turn']) and t['rade_creations'] == 1 and
                    t['input_samples'] > max(1000,(duration_ms-100)*44100//1000) and t['output_samples'] > 1000 and t['flush_acknowledged'] for t in turns)
                return {'status': 'PASS' if passed else 'FAIL', 'scope': 'Retained turns on private virtual devices; speech supplied' if speech else 'Retained turns on silent virtual source',
                        'exit': test.returncode, 'turns': turns, 'gui_sha256': expected_gui, 'probe_sha256': expected_probe,
                        'hardware_qualified': False, 'speech_intelligibility': 'UNASSESSED'}
            finally:
                if 'feeder' in locals() and feeder is not None and feeder.poll() is None:
                    feeder.terminate();feeder.wait(timeout=5)
                if server.poll() is None:
                    server.terminate()
                    try: server.wait(timeout=3)
                    except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('collect', 'probe'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--gui-sha256'); p.add_argument('--probe-sha256')
    p.add_argument('--cycles',action='store_true');p.add_argument('--duration-ms',type=int,choices=range(100,8001),default=8000)
    p.add_argument('--speech-wav',type=Path);p.add_argument('--turns',type=int,default=4,choices=range(1,101))
    p.add_argument('--probe-binary', type=Path, help='Explicit staged probe; never replaces the installed GUI or probe')
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=False)
    try:
        result = collect(a.output) if a.action == 'collect' else probe(a.output, a.gui_sha256, a.probe_sha256, a.probe_binary, a.speech_wav, a.turns, a.cycles, a.duration_ms)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        result = {'status': 'INCONCLUSIVE', 'reason': str(e), 'hardware_qualified': False}
    (a.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    (a.output/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.relative_to(a.output).as_posix()+'\n'
        for f in sorted(a.output.rglob('*')) if f.is_file() and f.name != 'SHA256SUMS'))
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('PASS', 'COLLECTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
