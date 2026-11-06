#!/usr/bin/env python3
"""Actual codec/four-stream integration with bounded, supervised fixtures.

No radio is contacted. Failures retain the original cause and partial evidence.
Every completed modem turn is checked against its own controller reference.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import subprocess
import tempfile
import time
import wave

from usb_test_support import (ProcessSupervisor, completed_turns, require,
                              run_bounded, save_json, validate_turn_decode, validate_session_identity, modem_waveform_match,
                              validate_operation_phase, microphone_frame_budget, PulseTestRuntime, pulse_startup_failure,
                              audio_buffer_contract, planned_fixture_eof, validate_drain_loss_wait, validate_tail_backpressure)
from usb_collector_contract import validate_collector_contract

CASES = (
    'normal', 'rx-signal', 'block-key', 'stop-during-key', 'release-during-key',
    'pa-delay', 'pa-reject', 'pa-missing', 'inactive-hole', 'active-hole',
    'inactive-stale', 'inactive-radio-hole', 'inactive-radio-stale', 'prekey-stale', 'capture-stale', 'stale-status', 'disconnect', 'stall', 'tx-stall',
    'rf-disabled', 'qualification-physical-tx', 'qualification-held-ptt',
    'cold-resume', 'cold-recovery-fails', 'cold-stop', 'cold-release', 'cold-qualification', 'cold-prekey-unexpected-tx', 'cold-rx-resume',
    'many-turns', 'reopen', 'cold-idle', 'drain-tx-loss',
    'initial-pa-delay', 'initial-pa-reject', 'initial-pa-missing',
    'return-pa-delay', 'return-pa-reject', 'return-pa-missing',
    'storage-start-fails', 'storage-completion-fails',
    'mic-valid-drop300',
    'full-session',
    'io-pause150', 'buffer-contract-reject', 'rx-pause150', 'short-turn200',
    'eoo-backpressure',
)
DURABILITY_CASES = ('many-turns', 'reopen', 'cold-idle', 'drain-tx-loss')
SPEECH_CASES = {'normal', 'rx-signal', 'block-key', 'pa-delay', 'inactive-hole', 'inactive-stale',
                'inactive-radio-hole', 'inactive-radio-stale', 'rf-disabled', 'cold-resume',
                'cold-rx-resume', 'cold-qualification', 'many-turns', 'reopen', 'initial-pa-delay', 'return-pa-delay', 'full-session', 'io-pause150', 'eoo-backpressure'}
WAVEFORM_CASES = SPEECH_CASES | {'short-turn200'}
COLD_CASES = {'cold-resume', 'cold-recovery-fails', 'cold-stop', 'cold-release', 'cold-qualification', 'cold-prekey-unexpected-tx', 'cold-rx-resume'}
PA_CASES = {prefix+'pa-'+mode: {'phase':phase, 'mode':mode}
            for prefix, phase in [('', 'tx'), ('initial-', 'initial-rx'), ('return-', 'rx-return')]
            for mode in ('delay', 'reject', 'missing')}
INJECT_CASES = COLD_CASES | set(PA_CASES) | {'inactive-hole', 'active-hole', 'inactive-stale', 'inactive-radio-hole', 'inactive-radio-stale', 'prekey-stale', 'capture-stale', 'mic-valid-drop300', 'io-pause150', 'rx-pause150', 'buffer-contract-reject', 'drain-tx-loss', 'eoo-backpressure'}
CASE_TIMEOUTS = {'many-turns': 120, 'reopen': 180, 'cold-idle': 100, 'drain-tx-loss': 60, 'full-session':120}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def injection_environment(case, root, library):
    if case not in INJECT_CASES:
        return {}
    require(library and library.is_file(), 'This case requires --fault-inject-so')
    result = {'LD_PRELOAD': str(library)}
    if case=='eoo-backpressure':
        result.update(USB_TEST_RENDER_HOLD_TRIGGER=str(root/'result/inject-render-hold'),USB_TEST_RENDER_HOLD_MS='150')
    elif case=='drain-tx-loss':
        result.update(USB_TEST_PA_MODE='delay',USB_TEST_PA_DEVICE='radio_sink_fixture',
                      USB_TEST_PA_ACTION='trigger',USB_TEST_PA_PHASE='tx',USB_TEST_PA_DELAY_MS='650')
    elif case in {'io-pause150','rx-pause150'}:
        result.update(USB_TEST_IO_PAUSE_DEVICE='radio_sink_fixture' if case=='io-pause150' else 'alsa_output.usb-fixture',
                      USB_TEST_IO_PAUSE_MS='150',USB_TEST_IO_PAUSE_AFTER_MS='600')
        if case=='rx-pause150':
            result['USB_TEST_RX_FIXTURE_MS']='30000'
    elif case=='buffer-contract-reject':
        result['USB_TEST_BUFFER_REJECT']='1'
    elif case in COLD_CASES:
        result.update(USB_TEST_PA_MODE='delay', USB_TEST_PA_DEVICE='radio_sink_fixture',
                      USB_TEST_PA_ACTION='uncork', USB_TEST_PA_PHASE='tx', USB_TEST_PA_DELAY_MS='760',
                      USB_TEST_COLD_CAPTURE='1', USB_TEST_COLD_RESUME_MS='200' if case in {'cold-stop', 'cold-release'} else '30')
        if case == 'cold-qualification':
            result.update(USB_TEST_STALE_DEVICE='alsa_input.usb-fixture', USB_TEST_STALE_TRIGGER=str(root / 'inject-stale'))
        if case == 'cold-rx-resume':
            result.update(USB_TEST_PA_ACTION='cork', USB_TEST_PA_PHASE='rx-return', USB_TEST_COLD_RESUME_MS='1000')
        if case == 'cold-recovery-fails':
            result['USB_TEST_COLD_NEVER'] = '1'
    elif case == 'mic-valid-drop300':
        result.update(USB_TEST_STALE_DEVICE='alsa_input.usb-fixture', USB_TEST_STALE_TRIGGER=str(root/'inject-stale'),
                      USB_TEST_STALE_DURATION_MS='300')
    elif case in PA_CASES:
        result.update(USB_TEST_PA_MODE=PA_CASES[case]['mode'],
                      USB_TEST_PA_DEVICE='radio_sink_fixture', USB_TEST_PA_ACTION='flush',
                      USB_TEST_PA_PHASE=PA_CASES[case]['phase'], USB_TEST_PA_DELAY_MS='650')
    elif case.endswith('hole'):
        result.update(USB_TEST_HOLE_DEVICE='radio_source_fixture' if case == 'inactive-radio-hole' else 'alsa_input.usb-fixture', USB_TEST_HOLE_TRIGGER=str(root / 'inject-hole'))
    elif case.endswith('stale'):
        result.update(USB_TEST_STALE_DEVICE='radio_source_fixture' if case == 'inactive-radio-stale' else 'alsa_input.usb-fixture', USB_TEST_STALE_TRIGGER=str(root / 'inject-stale'))
    return result


def continuous_fixture(path, pcm, rate, channels, width, seconds):
    """One persistent paplay process avoids process-restart holes at EOF."""
    require(pcm and len(pcm) % (channels * width) == 0, 'Fixture PCM is empty or misaligned')
    target = math.ceil(seconds * rate) * channels * width
    with path.open('wb') as output:
        while target:
            chunk = pcm[:target]
            output.write(chunk)
            target -= len(chunk)
    return ['--raw', '--rate=' + str(rate), '--format=' + {1: 'u8', 2: 's16le', 3: 's24le', 4: 's32le'}[width],
            '--channels=' + str(channels), str(path)]


def check_command(command, root, name, commands, env, timeout=3, check=True):
    result = run_bounded(command, root / (name + '.log'), timeout, env=env)
    commands.append(result)
    require(not result['timed_out'], 'Fixture command timed out: ' + name)
    if check:
        require(result['exit_code'] == 0, 'Fixture command failed: ' + name + '; inspect its retained log')
    return result, (root / (name + '.log')).read_text()


def decode_pcm(runner, pcm, rate, directory, commands, env):
    directory.mkdir()
    seconds = math.ceil(len(pcm) / (rate * 2)) + 1
    raw = directory / 'modem-padded.raw'
    raw.write_bytes(pcm + b'\0' * (seconds * rate * 2 - len(pcm)))
    result = run_bounded([runner, '--rx', raw, '--rate', rate, '--seconds', seconds, '--unpaced', '--output', directory],
                         directory / 'decode.log', 45, env=env)
    commands.append(result)
    require(not result['timed_out'] and result['exit_code'] == 0,
            'Modem decoder failed; inspect ' + str(directory / 'decode.log'))
    stats = json.loads((directory / 'engine-stats.json').read_text())
    stats['decoded_seconds'] = (directory / 'decoded-s16le.raw').stat().st_size / (2 * rate)
    return stats


def sink_turn_oracle(root, trace_paths, recording_started, runner, commands, env, expected_turns, turn_records=(), require_speech=True):
    """Check every sink turn; an earlier successful burst cannot mask a lost turn.

    The monitor is timestamped at launch. A 200 ms crop margin covers negotiated
    PA monitor buffering. Reference samples come from the controller modem tap;
    explicit harness offsets locate durable-case 48k modem tap boundaries. For
    legacy short cases, cumulative 44.1k transport counts locate boundaries to
    within a resampler tail, covered by 128 samples of context. Comparison allows
    one 120 ms block, not an arbitrary partial successful recording.
    """
    pcm = (root / 'radio-output.raw').read_bytes()
    results = []
    for trace_path in trace_paths:
        rows = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
        turns = completed_turns(rows)
        if not turns:
            continue
        samples = [row for row in rows if row.get('kind') == 'sample']
        reference = trace_path.with_name(trace_path.stem + '-modem.raw').read_bytes()
        microphone = trace_path.with_name(trace_path.stem + '-mic.raw').read_bytes()
        require(len(microphone)//2 == samples[-1]['submitted_mic'], 'Microphone tap is incomplete or inconsistent with accepted samples')
        descriptor = next((row for row in rows if row.get('kind') == 'stream' and row.get('stream') == 'mic'), None)
        require(descriptor and descriptor['rate'] == 48000 and descriptor['channels'] == 1,
                'Source frame oracle requires the negotiated 48k mono microphone stream')
        fragments = descriptor['fragsize']//2
        reference_start = 0
        microphone_start = 0
        for index, turn in enumerate(turns):
            after = next((row for row in samples if row['ms'] >= turn['end_ms']), None)
            require(after is not None, 'Completed turn lacks a final counter snapshot')
            exact = next((row for row in turn_records if row['session'] == trace_path.stem and row['turn'] == index+1), None)
            if exact:
                reference_start, reference_end = exact['modem_tap_start'], exact['modem_tap_end']
                require(reference_end*2 <= len(reference), 'Controller modem reference was truncated')
                reference_turn = reference[reference_start*2:reference_end*2]
            else:
                reference_end = len(reference) // 2 if index == len(turns)-1 else round(after['rendered_modem'] * 48000 / 44100)
                reference_turn = reference[max(0, reference_start-128)*2:min(len(reference), (reference_end+128)*2)]
            accepted = exact['accepted_mic_samples'] if exact else after['submitted_mic']-microphone_start
            microphone_start = after['submitted_mic']
            source_budget = microphone_frame_budget(turn['drain_ms']-turn['start_ms'], accepted, fragments)
            reference_start = reference_end
            begin = max(0, math.floor((turn['start_ms']/1000 - recording_started - .2) * 44100))
            end = math.ceil((turn['end_ms']/1000 - recording_started + .2) * 44100)
            require(end > begin and end * 2 <= len(pcm) + 44100,
                    'Monitor recording is missing the complete turn interval')
            observed = pcm[begin*2:min(len(pcm), end*2)]
            number = len(results) + 1
            reference_stats = decode_pcm(runner, reference_turn, 48000, root / ('turn-%03d-reference' % number), commands, env)
            observed_stats = decode_pcm(runner, observed, 44100, root / ('turn-%03d-sink' % number), commands, env)
            if require_speech:
                result = validate_turn_decode(reference_stats, observed_stats, number)
                result['speech_status']='DECODED_REFERENCE_MATCH'
            else:
                require(reference_stats['failures']==observed_stats['failures']==0,'Short-turn decoder failed')
                result={'status':'PASS','turn':number,'reference':reference_stats,'observed':observed_stats,
                        'speech_status':'UNASSESSED_INSUFFICIENT_DURATION','intelligibility':'UNASSESSED'}
                if reference_stats['synthesis_blocks'] and observed_stats['synthesis_blocks']:
                    validate_turn_decode(reference_stats,observed_stats,number)
                    result['speech_status']='DECODED_REFERENCE_MATCH'
            result['content_match'] = modem_waveform_match(reference_turn,48000,observed,44100)
            result['microphone_frame_budget'] = source_budget
            result.update(trace_file=trace_path.name, start_ms=turn['start_ms'], end_ms=turn['end_ms'])
            result['reference_boundary_basis'] = 'exact_modem_tap_offsets' if exact else 'transport_ratio_with_resampler_context'
            results.append(result)
    require(len(results) == expected_turns and results, 'Sink evidence did not cover every completed turn')
    return results


def headset_oracle(root, trace_path, recording_started):
    rows=[json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    samples=[row for row in rows if row.get('kind')=='sample' and row.get('state')==1]
    rendered=next((row for row in samples if row.get('rendered_rx',0)>0),None)
    require(rendered is not None,'RX did not render decoded speech')
    reference=trace_path.with_name(trace_path.stem+'-decoded.raw').read_bytes()
    require(len(reference)//2==samples[-1]['rendered_rx'],'Decoded RX tap is incomplete')
    pcm=(root/'headset-output.raw').read_bytes()
    # The complete reference remains intact; only leading monitor silence is
    # cropped. Two hundred milliseconds precede the first positive counter.
    begin=max(0,math.floor((rendered['ms']/1000-recording_started-.2)*48000))
    matched=modem_waveform_match(reference,48000,pcm[begin*2:],48000,expected_gain=.125)
    descriptor=next(row for row in rows if row.get('kind')=='stream' and row.get('stream')=='radio-rx')
    progression=microphone_frame_budget(samples[-1]['ms']-samples[0]['ms'],
        samples[-1]['rx_captured']-samples[0]['rx_captured'],descriptor['fragsize']//2,rate=44100,stream='radio-rx')
    require(samples[-1]['rx_holes']==0,'RX capture contained a hole')
    return {'status':'PASS','scope':'Actual private-Pulse headphone monitor vs complete decoded tap; no RF',
            'waveform':matched,'capture_frame_budget':progression,'rendered_frames':len(reference)//2,
            'leading_monitor_context_ms':200,'intelligibility':'UNASSESSED'}


def retain_failed_controller(root, args, record):
    """Grade available failure receipts without replacing the primary failure."""
    runtime=root/'result'
    failure=runtime/'harness-failure.json'
    if failure.is_file():
        data=failure.read_bytes()
        require(len(data)<=16384,'Harness failure descriptor exceeds its bound')
        value=json.loads(data)
        require(isinstance(value,dict) and value.get('status')=='FAIL','Invalid harness failure descriptor')
        record['harness_failure']=value
    pointer=runtime/'freedv-usb-current.json'
    if not pointer.is_file():
        return
    current=json.loads(pointer.read_text())
    completion=runtime/current['completion_basename']
    if not completion.is_file():
        record['failure_collection_error']='The failed controller did not finish its terminal receipt'
        return
    terminal=json.loads(completion.read_text())
    source=os.environ.get('USB_TEST_EXPECTED_SOURCE_COMMIT') or terminal['source_commit']
    build=os.environ.get('USB_TEST_EXPECTED_BUILD_ID') or terminal['build_id']
    record['failure_collector_contract']=validate_collector_contract(
        runtime,root/'failure-collector-contract',args.binary,source,build,record['test_binary_sha256'],
        storage_failure_expected=args.case in {'storage-start-fails','storage-completion-fails'})


def run_case(args):
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='freedv-usb-test-'))
    root.mkdir(parents=True, exist_ok=True)
    require(not (root / 'result').exists(), 'Case output must be fresh: ' + str(root))
    (root / 'result').mkdir()
    print('Evidence:', root, flush=True)
    commands = []
    record = {'schema': 'x6200.usb.integration.case.v3', 'status': 'FAIL', 'case': args.case,
              'scope': 'Real codec and Pulse streams at representative rates; fake radio control, no hardware RF',
              'radio_accessed': False, 'test_binary_sha256': sha(args.binary),
              'fixture_sha256': {'speech': sha(args.speech), 'modem': sha(args.modem)},
              'runner_sha256': sha(args.runner) if args.runner else None,
              'fault_injector_sha256': sha(args.fault_inject_so) if args.fault_inject_so else None,
              'commands': commands}
    env = os.environ.copy()
    supervisor = ProcessSupervisor(root, env)
    recording = headset_recording = cleanup = pulse_runtime = None
    previous_term = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        del signum, frame
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise RuntimeError('Case interrupted by supervisor; preserving partial evidence')

    signal.signal(signal.SIGTERM, interrupted)
    try:
        pulse_runtime = PulseTestRuntime(root)
        env.update(pulse_runtime.environment())
        record['pulse_runtime'] = pulse_runtime.identity()
        save_json(root/'pulse-runtime-identity.json',record['pulse_runtime'])
        expected = {name:os.environ.get(variable) for name,variable in
                    [('source_commit','USB_TEST_EXPECTED_SOURCE_COMMIT'),('build_id','USB_TEST_EXPECTED_BUILD_ID'),
                     ('binary_sha256','USB_TEST_EXPECTED_BINARY_SHA256')]}
        if any(expected.values()):
            require(all(expected.values()), 'Campaign supplied incomplete controller identity')
            require(record['test_binary_sha256'] == expected['binary_sha256'], 'Invoked controller binary differs from the campaign')
        conf = root / 'default.pa'
        conf.write_text('.fail\n' + f'load-module module-native-protocol-unix socket={pulse_runtime.socket} auth-anonymous=1\n' +
            'load-module module-null-sink sink_name=mic_fixture rate=16000 channels=1 format=s16le norewinds=1\n' +
            'load-module module-remap-source source_name=alsa_input.usb-fixture master=mic_fixture.monitor rate=16000 channels=1\n' +
            'load-module module-null-sink sink_name=rf_fixture rate=44100 channels=2 format=s16le norewinds=1\n' +
            'load-module module-remap-source source_name=radio_source_fixture master=rf_fixture.monitor rate=44100 channels=2\n' +
            'load-module module-null-sink sink_name=alsa_output.usb-fixture rate=48000 channels=2 format=s24le norewinds=1\n' +
            'load-module module-null-sink sink_name=radio_sink_fixture rate=44100 channels=2 format=s16le norewinds=1\n' +
            'load-module module-null-sink sink_name=unrelated_sink norewinds=1\nload-module module-rescue-streams\n' +
            ('load-module module-suspend-on-idle timeout=1\n' if args.case == 'cold-idle' else ''))
        pulse_args = ['pulseaudio', '-nF', str(conf), '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1']
        modules = os.environ.get('TEST_PULSE_MODULES')
        if not modules:
            binary = shutil.which('pulseaudio')
            require(binary is not None, 'pulseaudio is unavailable')
            choices = list(Path(binary).resolve().parents[1].glob('lib/pulse-*/modules'))
            if choices:
                modules = str(choices[0])
        if modules:
            pulse_args += ['--dl-search-path', modules]
            env['LD_LIBRARY_PATH'] = modules + os.pathsep + env.get('LD_LIBRARY_PATH', '')
        supervisor.start('pulse', pulse_args)
        ready_until = time.monotonic() + 7.5
        ready = False
        attempt = 0
        while time.monotonic() < ready_until:
            supervisor.require_running()
            result, _ = check_command(['pactl', 'info'], root, 'pulse-ready-%03d' % attempt, commands, env, 1, False)
            if result['exit_code'] == 0:
                ready = True
                break
            fatal = pulse_startup_failure((root/'pulse.log').read_text(errors='replace'))
            require(fatal is None, 'Private Pulse startup failed: '+str(fatal))
            attempt += 1
            time.sleep(.05)
        require(ready, 'Private Pulse did not become ready')
        supervisor.start('legacy-capture', ['parec', '--device=radio_source_fixture', '--rate=44100', '--channels=2',
                                           '--format=s16le', '--latency-msec=10'], stdout=subprocess.DEVNULL)
        horizon = CASE_TIMEOUTS.get(args.case, 75) + 15
        with wave.open(str(args.speech), 'rb') as speech:
            require(speech.getcomptype() == 'NONE', 'Speech fixture must be PCM WAV')
            speech_args = continuous_fixture(root/'continuous-speech.raw', speech.readframes(speech.getnframes()),
                                             speech.getframerate(), speech.getnchannels(), speech.getsampwidth(), horizon)
        radio_pcm = args.modem.read_bytes() if args.case == 'rx-signal' else b'\0' * 88200
        radio_args = continuous_fixture(root/'continuous-radio.raw', radio_pcm,
                                        48000 if args.case == 'rx-signal' else 44100, 1, 2, horizon)
        supervisor.start('speech-source', ['paplay', '--device=mic_fixture', *speech_args])
        radio_source=supervisor.start('radio-source', ['paplay', '--device=rf_fixture', *radio_args])
        rx_modem_args=(['--raw','--rate=48000','--format=s16le','--channels=1',str(args.modem)]
                       if args.case=='rx-pause150' else None)
        rx_fixture_seconds=args.modem.stat().st_size/96000 if args.case=='rx-pause150' else None
        time.sleep(.15)
        supervisor.require_running()

        def endpoints(name):
            content = ''
            for kind in ('sources', 'sinks'):
                _, text = check_command(['pactl', 'list', 'short', kind], root, name+'-'+kind, commands, env)
                content += kind + '\n' + text
            (root/name).write_text(content)
            return content

        before = endpoints('endpoints-before.txt')
        require(re.search(r'alsa_input\.usb-fixture\s+[^\n]*1ch 16000Hz', before), before)
        require(re.search(r'alsa_output\.usb-fixture\s+[^\n]*2ch 48000Hz', before), before)
        require(re.search(r'radio_sink_fixture\s+[^\n]*2ch 44100Hz', before), before)
        recorder = None
        recording_started = None
        headset_recorder = None
        headset_recording_started = None
        # An open monitor can prevent the idle sink from suspending. This case
        # proves private-Pulse suspension through trace events, not sink decode.
        if args.case not in {'cold-idle','rx-pause150'}:
            recording = (root/'radio-output.raw').open('wb')
            recording_started = time.monotonic()
            recorder = supervisor.start('modem-monitor', ['parec', '--device=radio_sink_fixture.monitor', '--rate=44100',
                                                         '--format=s16le', '--channels=1', '--latency-msec=20'], stdout=recording)
        if args.case=='rx-pause150':
            headset_recording=(root/'headset-output.raw').open('wb')
            headset_recording_started=time.monotonic()
            headset_recorder=supervisor.start('headset-monitor',['parec','--device=alsa_output.usb-fixture.monitor',
                '--rate=48000','--format=s16le','--channels=1','--latency-msec=20'],stdout=headset_recording)
        child_env = dict(env, **injection_environment(args.case, root, args.fault_inject_so))
        if args.case=='rx-pause150':
            require(0<rx_fixture_seconds<=30,'RX fixture exceeds its bounded orchestration horizon')
            child_env['USB_TEST_RX_FIXTURE_MS']=str(math.ceil(rx_fixture_seconds*1000))
        # Only the controller process receives the interposer, never Pulse/fixtures.
        # Keep the controller's bounded trace-prefix buffers independent of
        # immutable attempt path depth, just as the private Pulse socket is.
        record['controller_working_directory'] = str(root/'result')
        record['controller_output_argument'] = '.'
        test = supervisor.start('test', [args.binary, '.', '--'+args.case], env=child_env, cwd=root/'result')
        until = time.monotonic() + CASE_TIMEOUTS.get(args.case, 75)
        changed = False
        rx_source_started=rx_source_silenced=False
        while test.poll() is None:
            supervisor.require_running(except_process=test)
            require(time.monotonic() <= until, 'Controller scenario timed out')
            text = (root/'test.log').read_text()
            if args.case=='rx-pause150' and not rx_source_started and 'RX_FIXTURE_READY' in text:
                supervisor.stop(radio_source)
                radio_source=supervisor.start('radio-modem-source',['paplay','--device=rf_fixture',*rx_modem_args])
                supervisor.expect_exit(radio_source,exit_code=0)
                rx_source_started=True
                rx_fixture_started=time.monotonic()
                record['rx_fixture_started_ms']=round(time.monotonic()*1000)
            if (args.case=='rx-pause150' and rx_source_started and not rx_source_silenced and
                    radio_source.poll() is not None and 'RX_SOURCE_TO_SILENCE' in text):
                require(rx_source_started,'RX requested source stop before its modem fixture began')
                elapsed=time.monotonic()-rx_fixture_started
                record['rx_fixture_eof']=planned_fixture_eof(radio_source.returncode,rx_fixture_seconds,elapsed)
                rx_source_silenced=True
                stopped_at=round(time.monotonic()*1000)
                (root/'result/rx-source-silenced').write_text(str(stopped_at)+'\n')
                record['rx_fixture_silenced_ms']=stopped_at
                record['rx_settle_contract']={'clocked_zero_settle_ms':2000,
                    'reason':'Retain complete decoded tail beyond <=1s sink capacity, codec and pending queues before Stop'}
            if not changed and 'TX_ACTIVE' in text:
                endpoints('endpoints-active.txt')
                changed = True
                if args.case == 'disconnect':
                    check_command(['pactl', 'unload-module', 'module-remap-source'], root, 'disconnect', commands, env)
            time.sleep(.02)
        supervisor.require_running(except_process=test)
        text = (root/'test.log').read_text()
        print(text, flush=True)
        record['controller_exit_code']=test.returncode
        require(test.returncode == 0, 'Controller scenario failed with exit '+str(test.returncode))
        harness = json.loads((root/'result/harness-result.json').read_text())
        require(harness['status'] == 'PASS' and not harness['physical_tx'], str(harness))
        trace_paths = sorted((root/'result').glob('freedv-usb-session*.jsonl'))
        require(trace_paths, 'No controller trace was produced')
        rows, identities = [], []
        for path in trace_paths:
            trace_rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            status_rows = [row for row in trace_rows if row.get('kind') == 'sample']
            require(status_rows and status_rows[-1]['state'] in (5, 6), 'Controller trace is not closed: '+path.name)
            if status_rows[-1].get('rf_valid'):
                require(not status_rows[-1]['rf_tx'], 'Final fresh status still reports TX')
            completion_path = path.with_name(path.stem+'-completion.json')
            require(completion_path.is_file(), 'Closed controller completion record is missing')
            identities.append(validate_session_identity(trace_rows, json.loads(completion_path.read_text()),
                                                        expected['source_commit'], expected['build_id'],
                                                        args.case in {'storage-start-fails','storage-completion-fails'}))
            rows.extend(trace_rows)
        record['identity'] = {'status':'PASS' if all(expected.values()) else 'NOT_REQUESTED',
                              'source_commit':identities[0]['source_commit'], 'build_id':identities[0]['build_id'],
                              'binary_sha256':record['test_binary_sha256'], 'session_ids':[identity['session_id'] for identity in identities]}
        if any(row.get('kind')=='stream' for row in rows):
            reserve=audio_buffer_contract(rows)
            record['audio_buffer_contract']=reserve
            if args.case=='buffer-contract-reject':
                require(reserve['status']=='FAIL' and harness['fault']==13 and harness['keys']==0,
                        'Insufficient negotiated audio reserve was admitted')
                require(harness['first_fault_origin']=='modem-buffer-budget','Unexpected buffer-admission blocker')
            else:
                require(reserve['status']=='PASS','Negotiated audio buffers do not cover the declared service pause')
        if args.case in INJECT_CASES:
            require('INJECT ' in text, 'Fault injector did not exercise requested path')
        if args.case in PA_CASES:
            phase = PA_CASES[args.case]['phase']
            require('phase='+phase in text, 'The PA operation fault did not target the requested preparation phase')
            record['injected_operation'] = validate_operation_phase(rows,phase,PA_CASES[args.case]['mode'],text)
        if args.case=='drain-tx-loss':
            require('INJECT PA trigger' in text and 'phase=tx' in text,
                    'The injected wait did not target the TX trigger operation')
            record['drain_loss_wait']=validate_drain_loss_wait(rows,harness,text)
        if args.case=='eoo-backpressure':
            record['tail_backpressure']=validate_tail_backpressure(rows,harness,text)
        if args.case == 'mic-valid-drop300':
            require('no-hole=1' in text and 'valid capture resumed' in text,
                    'The finite microphone-only valid-data deletion was not exercised')
            require(harness['fault'] == 23 and harness['turns'] == 0 and harness['eoo'] == 0 and harness['mic_holes'] == 0,
                    'Deleted microphone data was accepted as a complete turn')
            accounting = [row for row in rows if row.get('kind') == 'source-accounting']
            require(accounting and accounting[-1]['evaluated'] and accounting[-1]['passed'] is False and
                    accounting[-1]['accepted_mic_frames'] < accounting[-1]['required_mic_frames'],
                    'The source frame deficit was not reported independently of holes')
            record['injected_source_loss'] = accounting[-1]
        if args.case in COLD_CASES:
            require('INJECT cold ACK success=1' in text, text)
            require('callback starvation device=alsa_input.usb-fixture' in text, text)
            require('callback starvation device=radio_source_fixture' in text, text)
            if args.case == 'cold-qualification':
                qualified = [row for row in rows if row.get('kind') == 'sample' and row.get('qualification_complete')]
                require(qualified, 'Qualification did not publish rate-accounting evidence')
                last = qualified[-1]
                require(all(value >= 760 for value in last['preparation_excluded_ms']), str(last))
                require(all(value > 0 for value in last['preparation_excluded_frames']), str(last))
                require(harness['mic_max_gap_ms'] >= harness['rx_max_gap_ms']+150, str(harness))
                require(abs(last['preparation_excluded_ms'][0]-last['preparation_excluded_ms'][1]) < 100, str(last))
                require(last['mic_rate_window_ms'] >= 3000 and last['rx_rate_window_ms'] >= 3000, str(last))
                require(45600 <= last['mic_rate_hz'] <= 50400 and 41895 <= last['rx_rate_hz'] <= 46305, str(last))
                record['preparation_accounting'] = {key:last[key] for key in
                    ('preparation_excluded_ms','preparation_excluded_frames','mic_rate_hz','rx_rate_hz','mic_rate_window_ms','rx_rate_window_ms')}
        if args.case == 'cold-idle':
            suspend = [row for row in rows if row.get('event') == 'stream-suspended' and row.get('stream') == 'modem']
            require(any(row.get('status') == 1 for row in suspend), 'Idle case never suspended the real private-Pulse modem stream')
            require(any(row.get('status') == 0 for row in suspend), 'Idle case never resumed the real private-Pulse modem stream')
        if args.case in {'io-pause150','rx-pause150'}:
            require('INJECT IO service pause' in text and 'no-discard=1' in text,
                    'Controlled client I/O pause was not exercised without discarding data')
            pause=harness['io_service_pause']
            require(pause['count']==1 and 150<=pause['duration_ms']<500,
                    'The controlled service pause duration was not retained')
            onset=re.search(r'after_payload_ms=(\d+)',text)
            require(onset and int(onset[1])>=600,'The pause occurred before steady payload delivery')
            require(harness['mic_max_gap_ms']>=140 and harness['rx_max_gap_ms']>=140,
                    'The controlled pause did not hold both client capture services')
            record['io_service_pause']=pause
        record['harness'] = harness
        record['event_count'] = len(rows)
        record['status_row_count'] = sum(row.get('kind') == 'sample' for row in rows)
        if recorder:
            time.sleep(.2)
            supervisor.require_running(except_process=test)
            supervisor.stop(recorder)
            recording.close()
            recording = None
            monitor_seconds = (root/'radio-output.raw').stat().st_size / 88200
            wall_seconds = time.monotonic() - recording_started
            record['monitor_clock'] = {'basis':'CLOCK_MONOTONIC process launch to reaped monitor',
                                       'started_seconds':recording_started, 'wall_seconds':wall_seconds,
                                       'pcm_seconds':monitor_seconds, 'maximum_alignment_error_seconds':.2}
            require(abs(wall_seconds-monitor_seconds) <= .2,
                    'Monitor sample clock does not match its monotonic recording interval')
        if headset_recorder:
            require(rx_source_started and rx_source_silenced,'RX fixture transitions were incomplete')
            time.sleep(.2)
            supervisor.require_running(except_process=test)
            supervisor.stop(headset_recorder)
            headset_recording.close();headset_recording=None
            seconds=(root/'headset-output.raw').stat().st_size/96000
            wall=time.monotonic()-headset_recording_started
            require(abs(wall-seconds)<=.2,'Headset monitor clock differs from its recording interval')
            record['headset_monitor_clock']={'basis':'CLOCK_MONOTONIC launch to reaped monitor',
                'started_seconds':headset_recording_started,'wall_seconds':wall,'pcm_seconds':seconds,
                'maximum_alignment_error_seconds':.2}
        cleanup = supervisor.close()
        require(not cleanup['errors'], 'Fixture cleanup failed: '+str(cleanup['errors']))
        record['collector_contract'] = validate_collector_contract(
            root/'result', root/'collector-contract', args.binary,
            expected['source_commit'] or identities[0]['source_commit'],
            expected['build_id'] or identities[0]['build_id'], record['test_binary_sha256'],
            storage_failure_expected=args.case in {'storage-start-fails','storage-completion-fails'})
        runner = args.runner or (Path(os.environ['USB_RF_REFERENCE_RUNNER']) if os.environ.get('USB_RF_REFERENCE_RUNNER') else None)
        if args.case in WAVEFORM_CASES:
            require(runner is not None, 'Speech cases require the actual codec runner')
            record['actual_sink_turns'] = sink_turn_oracle(root, trace_paths, recording_started, runner, commands, env,
                harness.get('total_turns', harness['turns']), harness.get('turn_records',()),require_speech=args.case in SPEECH_CASES)
            record['actual_sink_decode'] = {'failures':0,
                'synthesis_blocks':sum(turn['observed']['synthesis_blocks'] for turn in record['actual_sink_turns']),
                'synchronized_blocks':sum(turn['observed']['synchronized_blocks'] for turn in record['actual_sink_turns'])}
        if args.case=='rx-pause150':
            require(harness['keys']==0 and harness['turns']==0,'RX resilience test attempted RF admission')
            record['actual_headset_output']=headset_oracle(root,trace_paths[0],headset_recording_started)
        record['status'] = 'PASS'
        print('PASS scenario:', args.case, flush=True)
    except BaseException as error:
        record['error'] = str(error)
        raise
    finally:
        if cleanup is None:
            cleanup = supervisor.close()
        record['process_cleanup'] = cleanup
        if recording:
            recording.close()
        if headset_recording:
            headset_recording.close()
        if pulse_runtime is not None:
            try:
                pulse_runtime.close()
            except Exception as error:
                cleanup['errors'].append('Private Pulse runtime cleanup: '+str(error))
            record['pulse_runtime'] = pulse_runtime.identity()
            save_json(root/'pulse-runtime-identity.json',record['pulse_runtime'])
        if cleanup['errors']:
            record['status'] = 'FAIL'
            record.setdefault('error','Fixture cleanup failed: '+str(cleanup['errors']))
        if record['status']=='FAIL':
            try:
                retain_failed_controller(root,args,record)
            except Exception as error:
                record['failure_collection_error']=str(error)
        signal.signal(signal.SIGTERM, previous_term)
        save_json(root/'case-result.json', record)
        if cleanup['errors'] and sys.exc_info()[0] is None:
            raise RuntimeError(record['error'])
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
