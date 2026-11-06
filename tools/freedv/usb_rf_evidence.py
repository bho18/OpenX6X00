#!/usr/bin/env python3
"""Collect stopped USB GUI evidence and optional off-air audio; never key RF."""
import argparse
from array import array
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from usb_headset_inventory import collect, command, sha256
from usb_output_contract import validate_output_contract


ERRORS = (OSError, ValueError, KeyError, EOFError, wave.Error, subprocess.SubprocessError)
DIAGNOSTIC_LIMIT = 4 * 1024 * 1024
MAX_SDR_SECONDS = 120
MAX_SDR_BYTES = 32 * 1024 * 1024
IDENTITY_FIELDS = ('session_id', 'build_id', 'source_commit')


class ActiveUsbWorkerError(ValueError):
    pass


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def read_sdr(path):
    with wave.open(str(path), 'rb') as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if channels not in (1, 2) or width != 2 or rate not in (8000, 44100, 48000):
            raise ValueError(f'Unsupported SDR WAV format: {channels} channels, {width * 8}-bit samples, {rate} Hz. '
                             'Use PCM16 mono/stereo audio at 8, 44.1 or 48 kHz.')
        if not frames:
            raise ValueError('SDR audio recording contains no samples.')
        if frames > rate * MAX_SDR_SECONDS:
            raise ValueError(f'SDR audio duration is {frames / rate:.3f} seconds; '
                             f'the collector supports up to {MAX_SDR_SECONDS} seconds.')
        raw = wav.readframes(frames)
        if len(raw) != frames * channels * 2:
            raise ValueError('Truncated SDR WAV')
    samples = array('h', raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    if channels == 2:
        samples = array('h', (int((samples[i] + samples[i + 1]) / 2) for i in range(0, len(samples), 2)))
    metadata = dict(rate=rate, channels=channels, frames=frames, seconds=frames / rate,
                    rms=math.sqrt(sum(x * x for x in samples) / len(samples)),
                    clipped_samples=sum(abs(x) >= 32760 for x in samples))
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes(), metadata


def wrap(raw, destination, rate=48000):
    with wave.open(str(destination), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(raw)


def post_stop_diagnostics():
    """These are current observations after teardown, not the first-fault state."""
    queries = {
        'sink_inputs': ['pactl', 'list', 'sink-inputs'],
        'source_outputs': ['pactl', 'list', 'source-outputs'],
        'kernel': ['dmesg', '-s', '131072'],
        'system_log': ['logread', '-l', '200'],
    }
    return dict(scope='COLLECTION_TIME_CURRENT_STATE; worker may still be active; first-fault details belong to immutable runtime diagnostics',
                commands={name: command(args, timeout=3, limit=128 * 1024) for name, args in queries.items()})


def fault_code(row):
    value = row.get('fault', row.get('code', 0))
    if isinstance(value, dict):
        value = value.get('code', value.get('fault', 0))
    return value if isinstance(value, int) else 0


def summarize_trace(rows):
    """Read historical sampled traces and newer metadata/event/snapshot records."""
    states = [row for row in rows if isinstance(row, dict) and 'state' in row and
              row.get('kind', 'sample') in ('sample', 'snapshot', 'state') and ('rf_tx' in row or 'generation' in row)]
    if not states:
        raise ValueError('GUI trace contains no state snapshots.')
    metadata = [row for row in rows if isinstance(row, dict) and
                row.get('kind', row.get('type', row.get('event'))) in ('metadata', 'meta', 'session', 'configuration')]
    streams = [row for row in rows if isinstance(row, dict) and row.get('kind') == 'stream']
    faults = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        first = row.get('first_fault')
        if isinstance(first, dict) and fault_code(first) > 1:
            faults.append(first)
        elif fault_code(row) > 1:
            faults.append(row)
    first_fault = faults[0] if faults else None
    # RF-disabled sessions must never be promoted by an unrelated SDR file.
    rf_disabled = states[-1].get('rf_disabled', any(row.get('rf_disabled') is True or row.get('qualification') is True or
                      row.get('qualification_mode') is True for row in metadata))
    qualification = next((row for row in reversed(rows) if isinstance(row, dict) and
                          (row.get('event') in ('qualification_complete', 'qualification_result') or
                           row.get('type') == 'qualification_result')), None)
    completed = next((row for row in reversed(states) if row.get('qualification_complete') is True), None)
    if qualification is None and completed:
        target = next((row['qualification_target'] for row in reversed(metadata) if 'qualification_target' in row), 3)
        turns = completed.get('qualification_turns', 0)
        qualification = dict(status='PASS' if turns >= target else 'INCOMPLETE',
                             turns=turns, target=target, rf_tested=False,
                             loopback=completed.get('qualification_loopback', False),
                             listening='UNASSESSED')
    return dict(last=states[-1], sample_count=len(states),
                trace_rows=sum(isinstance(row, dict) and row.get('kind') in ('event', 'sample') for row in rows),
                first_fault=first_fault, metadata=metadata, streams=streams[-32:],
                rf_disabled=rf_disabled, qualification=qualification,
                unexpected_qualification_tx=any(row.get('rf_disabled') is True and row.get('rf_tx') is True for row in states))


def strict_contract(manifest):
    version = manifest.get('usb_startup_contract_version', 1)
    return type(version) is int and version >= 3


def output_contract_version(manifest):
    if 'usb_output_contract_version' not in manifest:
        return 0
    version = manifest['usb_output_contract_version']
    if type(version) is not int or version != 1:
        raise ValueError('Unsupported USB output contract version; new images require integer version 1.')
    if manifest.get('usb_startup_contract_version') != 3:
        raise ValueError('USB output contract version 1 requires the startup v3 session contract.')
    return version


def collect_persistent_journal(output):
    """Keep bounded incident metadata across reboot, never qualify from it."""
    directory = Path('/mnt/freedv-usb-incidents')
    report = dict(scope='PERSISTENT_DIAGNOSTICS_ONLY; missing original completed trace cannot be promoted',
                  status='UNAVAILABLE', entries=[], errors=[])
    if not directory.is_dir() or directory.is_symlink():
        return report
    destination = output / 'persistent-incidents'
    destination.mkdir()
    records = {}
    names = ['current-session.json'] + [f'slot-{slot:02d}-{kind}.json'
             for slot in range(16) for kind in ('metadata', 'fault', 'completion', 'aborted')]
    for name in names:
        path = directory / name
        if not path.exists() and not path.is_symlink():
            continue
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError('Nonregular persistent incident artifact: ' + name)
            with path.open('rb') as source:
                data = source.read(16385)
            if len(data) > 16384:
                raise ValueError('Persistent incident exceeds 16 KiB bound: ' + name)
            (destination / name).write_bytes(data)
            record = json.loads(data)
            if not isinstance(record, dict):
                raise ValueError('Persistent incident must be an object: ' + name)
            session_id = record.get('session_id')
            if not isinstance(session_id, str) or re.fullmatch(r'freedv-usb-[A-Za-z0-9_-]{1,160}', session_id) is None:
                raise ValueError('Persistent incident has invalid session identity: ' + name)
            if name == 'current-session.json':
                if (record.get('schema') != 'x6200.usb.incident-pointer.v1' or
                        type(record.get('slot')) is not int or not 0 <= record['slot'] < 16):
                    raise ValueError('Persistent current-session pointer is invalid.')
            else:
                match = re.fullmatch(r'slot-(\d{2})-(metadata|fault|completion|aborted)\.json', name)
                nested = record.get('record')
                if (record.get('schema') != 'x6200.usb.incident.v1' or not 0 <= int(match[1]) < 16 or
                        record.get('kind') != match[2] or not isinstance(nested, dict) or nested.get('session_id') != session_id or
                        (match[2] != 'aborted' and not all(isinstance(nested.get(field), str) and
                            0 < len(nested[field]) <= 200 for field in IDENTITY_FIELDS))):
                    raise ValueError('Persistent incident slot/kind/session mismatch: ' + name)
            records[name] = record
            report['entries'].append(name)
        except ERRORS as error:
            report['errors'].append(str(error))
    for name,record in list(records.items()):
        match=re.fullmatch(r'slot-(\d{2})-(fault|completion|aborted)\.json',name)
        if match:
            metadata=records.get(f'slot-{int(match[1]):02d}-metadata.json')
            if (not metadata or metadata['session_id'] != record['session_id'] or any(
                    field in record['record'] and record['record'][field] != metadata['record'].get(field)
                    for field in ('build_id', 'source_commit'))):
                report['errors'].append('Persistent incident has no matching retained metadata: ' + name)
                del records[name]
    pointer = records.get('current-session.json')
    if pointer:
        selected = {}
        for kind in ('metadata', 'fault', 'completion', 'aborted'):
            record = records.get(f"slot-{pointer['slot']:02d}-{kind}.json")
            if record and record['session_id'] == pointer['session_id']:
                selected[kind] = record['record']
        if 'metadata' in selected:
            report['current_session'] = dict(session_id=pointer['session_id'], records=selected)
        else:
            report['errors'].append('Persistent pointer has no matching metadata record.')
    report['status'] = 'PARTIAL' if report['errors'] else 'COLLECTED'
    return report


def validate_identity(record, manifest, session_id=None):
    if not isinstance(record, dict):
        raise ValueError('Runtime identity record must be a JSON object.')
    for name in IDENTITY_FIELDS:
        value = record.get(name)
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError('Runtime identity is missing or invalid: ' + name)
    if session_id is not None and record['session_id'] != session_id:
        raise ValueError('Runtime records refer to different sessions.')
    if record['source_commit'] != manifest.get('firmware_commit'):
        raise ValueError('Runtime session source does not match the image/kit.')
    if record['build_id'] != manifest.get('build_id'):
        raise ValueError('Runtime session build does not match the image/kit.')


def validate_completion(summary, manifest, completion):
    """A parseable last sample is not proof that a worker completed teardown."""
    sessions = [row for row in summary['metadata'] if row.get('kind') == 'session']
    if len(sessions) != 1 or sessions[0].get('schema') != 'x6200.usb-audio.v3':
        raise ValueError('Current image requires exactly one v3 runtime session header.')
    session = sessions[0]
    validate_identity(session, manifest)
    validate_identity(completion, manifest, session['session_id'])
    if (completion.get('kind') != 'completion' or
            completion.get('schema') != 'x6200.usb-completion.v1' or
            completion.get('terminal') is not True or completion.get('trace_complete') is not True):
        raise ValueError('Runtime session has no complete immutable terminal record.')
    last = summary['last']
    for field in ('state', 'fault', 'qualification_turns', 'qualification_target', 'ms'):
        if type(last.get(field)) is not int or last[field] < 0:
            raise ValueError('Runtime final snapshot has an invalid integer field: ' + field)
        if field != 'ms' and (type(completion.get(field)) is not int or completion[field] < 0):
            raise ValueError('Runtime completion has an invalid integer field: ' + field)
    for field in ('rf_disabled', 'rf_valid', 'rf_tx', 'rf_clear_confirmed', 'qualification_complete'):
        if type(last.get(field)) is not bool or type(completion.get(field)) is not bool:
            raise ValueError('Runtime terminal records have an invalid boolean field: ' + field)
    if (type(session.get('qualification_target')) is not int or session['qualification_target'] < 1 or
            last['qualification_target'] != session['qualification_target']):
        raise ValueError('Runtime qualification target conflicts with its session header.')
    if last.get('state') not in (5, 6) or completion.get('state') != last.get('state'):
        raise ValueError('Runtime trace does not end in the recorded terminal state.')
    fields = ('fault', 'rf_disabled', 'rf_valid', 'rf_tx', 'rf_clear_confirmed',
              'qualification_complete', 'qualification_turns', 'qualification_target')
    for field in fields:
        if field not in last or field not in completion or last[field] != completion[field]:
            raise ValueError('Runtime completion conflicts with final snapshot: ' + field)
    if type(completion.get('last_sample_ms')) is not int or completion['last_sample_ms'] != last['ms']:
        raise ValueError('Runtime completion does not identify the final snapshot.')
    if type(completion.get('finalized_ms')) is not int or completion['finalized_ms'] < last['ms']:
        raise ValueError('Runtime completion has invalid finalization timing.')
    if type(completion.get('trace_rows')) is not int or completion['trace_rows'] != summary['trace_rows']:
        raise ValueError('Runtime completion row count does not match the collected trace.')
    if type(completion.get('events_dropped')) is not int or completion['events_dropped'] != 0:
        raise ValueError('Runtime event history is incomplete.')
    if completion.get('trace_history_truncated') is not False:
        raise ValueError('Runtime trace history was truncated or its completeness is unknown.')
    if type(completion.get('storage_errno')) is not int or completion['storage_errno'] != 0:
        raise ValueError('Runtime incident persistence did not complete successfully.')
    if last['state'] == 5 and fault_code(last) <= 1:
        raise ValueError('Runtime terminal fault state has no matching fault.')
    if last['state'] == 6 and fault_code(last) > 1:
        raise ValueError('Runtime terminal done state conflicts with its fault.')
    summary['session_identity'] = {name: session[name] for name in IDENTITY_FIELDS}
    summary['completion'] = completion


def preserve_immutable_diagnostics(prefix, output):
    """Read atomically published JSON sidecars even if shutdown is blocked.

    A mutable event trace or PCM tap is never a substitute for these records.
    Validate the bounded bytes once before saving, so a partial JSON write is
    not presented as an immutable first-fault snapshot.
    """
    errors = []
    for suffix in ('fault.json', 'completion.json', 'metadata.json', 'config.json', 'configuration.json'):
        path = Path(str(prefix) + '-' + suffix)
        if not path.exists():
            continue
        try:
            with path.open('rb') as source:
                data = source.read(DIAGNOSTIC_LIMIT + 1)
            if len(data) > DIAGNOSTIC_LIMIT:
                (output / ('runtime-' + suffix + '.header')).write_bytes(data[:4096])
                raise ValueError('Immutable runtime diagnostics exceed collection bound: ' + suffix)
            # Preserve bounded corrupt/partial records as evidence too.
            (output / ('runtime-' + suffix)).write_bytes(data)
            record = json.loads(data)
            if not isinstance(record, dict):
                raise ValueError('Immutable runtime diagnostics must be a JSON object: ' + suffix)
        except ERRORS as error:
            errors.append(str(error))
    return errors


def collect_trace(output, manifest=None):
    manifest = manifest or {}
    runtime = Path('/tmp')
    pointer = None
    if strict_contract(manifest):
        path = runtime / 'freedv-usb-current.json'
        with path.open('rb') as source:
            data = source.read(DIAGNOSTIC_LIMIT + 1)
        if len(data) > DIAGNOSTIC_LIMIT:
            raise ValueError('Current-session pointer exceeds collection bound.')
        (output / 'runtime-current.json').write_bytes(data)
        pointer = json.loads(data)
        if not isinstance(pointer, dict) or pointer.get('kind') != 'session-pointer' or pointer.get('schema') != 'x6200.usb-current.v1':
            raise ValueError('Current image requires an explicit runtime session pointer.')
        session_id = pointer.get('session_id')
        if not isinstance(session_id, str) or re.fullmatch(r'freedv-usb-[A-Za-z0-9_-]{1,160}', session_id) is None:
            raise ValueError('Current-session pointer has an invalid session identifier.')
        if (pointer.get('trace_basename') != session_id + '.jsonl' or
                pointer.get('completion_basename') != session_id + '-completion.json'):
            raise ValueError('Current-session pointer has inconsistent artifact names.')
        trace = runtime / pointer['trace_basename']
    else:
        sessions = sorted(runtime.glob('freedv-usb-*.jsonl'), key=lambda p: p.stat().st_mtime)
        if not sessions:
            raise ValueError('No GUI USB-mode trace found. Complete a USB-mode test first.')
        trace = sessions[-1]
    prefix = trace.with_suffix('')
    diagnostic_errors = preserve_immutable_diagnostics(prefix, output)
    for process in Path('/proc').glob('[0-9]*/fd'):
        for fd in process.glob('*'):
            try:
                if str(fd.resolve()) == str(trace):
                    raise ActiveUsbWorkerError('USB audio worker is still running. Immutable diagnostics retained; Stop/Exit before collecting its mutable trace and audio.')
            except OSError:
                pass
    if trace.stat().st_size > DIAGNOSTIC_LIMIT:
        raise ValueError('Trace exceeds collection bound.')
    data = trace.read_bytes()
    if len(data) > DIAGNOSTIC_LIMIT:
        raise ValueError('Trace exceeds collection bound.')
    (output / 'usb-session.jsonl').write_bytes(data)
    rows = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    summary = summarize_trace(rows)
    if strict_contract(manifest):
        try:
            validate_identity(pointer, manifest)
            completion = output / 'runtime-completion.json'
            if not completion.exists():
                raise ValueError('Current image requires an immutable runtime completion record.')
            validate_completion(summary, manifest, json.loads(completion.read_text()))
            if summary['session_identity']['session_id'] != pointer['session_id']:
                raise ValueError('Runtime trace conflicts with the explicit current-session pointer.')
            summary['current_session'] = pointer
            summary['output_contract'] = validate_output_contract(
                rows, summary['completion'], output_contract_version(manifest), summary['session_identity'])
        except ERRORS as error:
            diagnostic_errors.append(str(error))
    else:
        summary['output_contract'] = validate_output_contract(rows, None, output_contract_version(manifest))
    summary['audio_taps'] = {}
    pcm_seconds = 30
    if strict_contract(manifest):
        sessions = [row for row in summary['metadata'] if row.get('kind') == 'session']
        if len(sessions) == 1:
            limit = sessions[0].get('trace_pcm_seconds', 30)
            if type(limit) is not int or not 1 <= limit <= 120:
                diagnostic_errors.append('Runtime PCM evidence budget must be an integer from 1 to 120 seconds.')
            else:
                pcm_seconds = limit
    for name in ('mic', 'radio-rx', 'modem', 'decoded'):
        path = Path(str(prefix) + '-' + name + '.raw')
        if path.exists():
            size = path.stat().st_size
            if size > 48000 * 2 * pcm_seconds or size % 2:
                raise ValueError('Unexpected trace PCM length: ' + name)
            wrap(path.read_bytes(), output / (name + '.wav'))
            summary['audio_taps'][name] = dict(frames=size // 2, rate=48000, seconds=size / 96000)
    # Runtime diagnostics are optional on older images. Keep each bounded and
    # preserve its schema rather than confusing an event with the final state.
    for suffix in ('qualification.json', 'diagnostics.json', 'events.jsonl'):
        path = Path(str(prefix) + '-' + suffix)
        if path.exists():
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('Runtime diagnostics exceed collection bound: ' + suffix)
            shutil.copy2(path, output / ('runtime-' + suffix))
    if diagnostic_errors:
        raise ValueError('; '.join(diagnostic_errors))
    return summary


def preserve_sdr(output, sdr):
    size = sdr.stat().st_size
    info = dict(bytes=size, sha256=sha256(sdr))
    if size <= MAX_SDR_BYTES:
        shutil.copy2(sdr, output / 'sdr-usb-audio.wav')
    else:
        with sdr.open('rb') as source:
            (output / 'sdr-header.bin').write_bytes(source.read(4096))
        raise ValueError(f'SDR file exceeds the {MAX_SDR_BYTES // (1024 * 1024)} MiB evidence bound; header retained.')
    return info


def decode_sdr(output, manifest, sdr, result):
    raw, metadata = read_sdr(sdr)
    result['sdr_input'] = metadata
    from usb_half_duplex import stream_process
    runner = Path('/opt/freedv-benchmark/bin/freedv_voice_runner')
    if sha256(runner) != manifest['runner_sha256']:
        raise ValueError('Installed decoder runner mismatch.')
    if sha256(Path('/opt/freedv-benchmark/lib/librade.so.0.1')) != manifest['rade_sha256']:
        raise ValueError('Installed RADE library mismatch.')
    seconds = math.ceil(metadata['seconds']) + 1
    raw += b'\0' * (seconds * metadata['rate'] * 2 - len(raw))
    decoded = output / 'offair-decode'
    decoded.mkdir()
    with tempfile.TemporaryDirectory(prefix='usb-sdr-decode-') as temp:
        pcm = Path(temp) / 'sdr.raw'
        pcm.write_bytes(raw)
        stream_process([runner, '--rx', pcm, '--output', decoded, '--rate', metadata['rate'],
                        '--seconds', seconds], output, timeout=180)
    stats = json.loads((decoded / 'engine-stats.json').read_text())
    result['offair_decoder_stats'] = stats
    if stats.get('failures', 1) or stats.get('synchronized_blocks', 0) < 1 or stats.get('synthesis_blocks', 0) < 1:
        raise ValueError('Off-air recording did not produce synchronized decoded speech.')
    raw_decoded = decoded / 'decoded-s16le.raw'
    wrap(raw_decoded.read_bytes(), decoded / 'offair-decoded.wav', metadata['rate'])
    result['decoded_pcm_sha256'] = sha256(raw_decoded)
    raw_decoded.unlink()
    result['rf_recording_decoded'] = True


def collect_evidence(output, manifest, sdr=None):
    result = dict(schema='x6200.usb-rf-evidence.v1', schema_version=2, status='BLOCKED',
                  rf_key_commands_sent=False, rf_recording_decoded=False, rf_tested=False,
                  ready_for_rf_test=False,
                  usb_startup_contract_version=manifest.get('usb_startup_contract_version', 1),
                  usb_output_contract_version=manifest.get('usb_output_contract_version', 0),
                  listening='UNASSESSED', physical_rf_off_timing='UNMEASURED', blockers=[],
                  stages=dict(collection={'status': 'PENDING'}, gui={'status': 'UNASSESSED'},
                              audio={'status': 'UNASSESSED'}, control_completion={'status': 'UNASSESSED'}, qualification={'status': 'NOT_REQUESTED'},
                              rf_decode={'status': 'NOT_REQUESTED'}))
    def blocker(stage, error):
        result['blockers'].append(dict(stage=stage, reason=str(error)))
        result['stages'][stage] = dict(status='BLOCKED', reason=str(error))
        print(stage.upper() + ': ' + str(error), flush=True)
    def record_gui_fault(fault):
        result['first_gui_fault'] = fault
        result['gui_session_fault'] = fault_code(fault)
        reason = fault.get('reason', fault.get('fault_name', fault.get('detail',
                 fault.get('origin', 'GUI session stopped with fault ' + str(fault_code(fault))))))
        blocker('gui', reason)
        result['stages']['audio'] = dict(status='UNASSESSED',
            reason='GUI fault prevented completion; first-fault metadata identifies the failing subsystem.',
            first_fault=fault)
        if fault_code(fault) in (2, 6, 7, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 23):
            result['stages']['audio']['status'] = 'FAULT'
    identity_ok = False
    summary = None
    try:
        try:
            result['persistent_journal'] = collect_persistent_journal(output)
        except ERRORS as error:
            result['persistent_journal'] = dict(status='PARTIAL', scope='PERSISTENT_DIAGNOSTICS_ONLY', error=str(error))
        try:
            if manifest.get('_load_error'):
                raise ValueError('Kit manifest unavailable: ' + manifest['_load_error'])
            if type(manifest.get('usb_startup_contract_version', 1)) is not int or manifest.get('usb_startup_contract_version', 1) not in (1, 2, 3):
                raise ValueError('Unsupported USB startup contract version.')
            output_contract_version(manifest)
            inventory = collect(expected_gui=manifest['gui_sha256'])
            save(output / 'inventory.json', inventory)
            result['inventory_status'] = inventory['status']
            identity_ok = True
            if inventory['status'] != 'USB_DUPLEX_CANDIDATE':
                raise ValueError('Current USB endpoint inventory is incomplete: ' + str(inventory['status']))
        except ERRORS as error:
            blocker('collection', error)
        try:
            save(output / 'post-stop-diagnostics.json', post_stop_diagnostics())
        except ERRORS as error:
            # Supplementary diagnostic commands may be unavailable after a fault.
            save(output / 'post-stop-diagnostics.json', dict(scope='COLLECTION_TIME_CURRENT_STATE', error=str(error)))
        try:
            summary = collect_trace(output, manifest)
            result['last_gui_state'] = summary['last']
            result['gui_session_fault'] = fault_code(summary['first_fault'] or summary['last'])
            result['rf_disabled'] = summary['rf_disabled']
            result['audio_taps'] = summary['audio_taps']
            result['output_contract'] = summary['output_contract']
            result['stages']['output_completion'] = summary['output_contract']
            if summary['output_contract']['status'] != 'PASS':
                result['historical_output_evidence'] = True
            if summary['metadata']:
                result['runtime_metadata'] = summary['metadata']
            if summary['streams']:
                result['runtime_stream_configuration'] = summary['streams']
            if summary.get('session_identity'):
                result['session_identity'] = summary['session_identity']
                result['runtime_completion'] = summary['completion']
                result['stages']['session'] = dict(status='COMPLETE', identity=summary['session_identity'])
            else:
                result['historical_runtime_evidence'] = True
                result['stages']['session'] = dict(status='HISTORICAL_ONLY',
                    reason='Legacy trace has no current session/build identity and immutable completion contract.')
            if summary['first_fault']:
                record_gui_fault(summary['first_fault'])
            else:
                result['stages']['gui'] = dict(status='STOPPED', intentional_stop=result['gui_session_fault'] == 1)
                # A fault-free stop alone is not an audio continuity qualification.
                result['stages']['audio'] = dict(status='UNASSESSED', reason='Review per-stream runtime continuity counters and phase events.')
            last = summary['last']
            version = manifest.get('usb_startup_contract_version', 1)
            contract_v2 = (type(version) is int and version >= 2) or any(
                row.get('schema') in ('x6200.usb-audio.v2', 'x6200.usb-audio.v3') for row in summary['metadata'])
            confirmed_rx = last.get('rf_clear_confirmed') is True and last.get('rf_valid') is True and last.get('rf_tx') is False
            if confirmed_rx:
                result['stages']['control_completion'] = dict(status='CONFIRMED_RX',
                    scope='Fresh post-clear packet telemetry; physical RF-off timing remains unmeasured.')
            elif contract_v2 or last.get('rf_tx') is True or last.get('rf_valid') is False:
                reason = 'Final RX confirmation was not received; software Stop alone does not establish that RF returned to RX.'
                result['stages']['control_completion'] = dict(status='UNCONFIRMED',
                    rf_clear_confirmed=last.get('rf_clear_confirmed'),rf_valid=last.get('rf_valid'),rf_tx=last.get('rf_tx'))
                blocker('gui', reason)
            else:
                result['stages']['control_completion'] = dict(status='UNASSESSED',
                    reason='Legacy trace has no explicit post-clear confirmation; no confirmed RF-clear state is inferred.')
            if summary['qualification']:
                result['runtime_qualification'] = summary['qualification']
                result['stages']['qualification'] = summary['qualification']
                if summary['qualification'].get('status') == 'PASS' and not summary['first_fault']:
                    result['stages']['audio'] = dict(status='QUALIFIED_RF_DISABLED',
                        reason='The runtime completed its bounded RF-disabled integrated qualification; RF performance is unassessed.')
            if summary['rf_disabled']:
                qualification = summary['qualification']
                result['stages']['qualification'] = qualification or dict(status='INCOMPLETE')
                if not qualification or qualification.get('status') != 'PASS':
                    blocker('qualification', 'RF-disabled qualification did not report PASS.')
                if summary['unexpected_qualification_tx'] or summary['last'].get('rf_tx') is not False or summary['last'].get('rf_valid') is not True:
                    blocker('qualification', 'RF-disabled qualification requires fresh RX telemetry and no observed TX during that phase.')
                result['rf_tested'] = False
            elif manifest.get('on_device_readiness_required') is True and (
                    not summary['qualification'] or summary['qualification'].get('status') != 'PASS'):
                blocker('qualification', 'This image requires a completed on-device RF-disabled qualification before RF use.')
        except ERRORS as error:
            blocker('collection', error)
            result['worker_active'] = isinstance(error, ActiveUsbWorkerError)
        # Reconcile the atomic first-fault record on every path. A clean-looking
        # or truncated trace must never hide an independently published failure.
        fault_path = output / 'runtime-fault.json'
        if fault_path.exists():
            try:
                record = json.loads(fault_path.read_text())
                result['immutable_first_fault_snapshot'] = record
                if strict_contract(manifest):
                    pointer_path = output / 'runtime-current.json'
                    pointer = json.loads(pointer_path.read_text())
                    validate_identity(pointer, manifest)
                    validate_identity(record, manifest, pointer['session_id'])
                fault = record.get('first_fault', record.get('fault', record))
                if isinstance(fault, dict) and fault_code(fault) > 1:
                    previous = result.get('first_gui_fault')
                    if previous and (fault_code(previous) != fault_code(fault) or any(
                            previous.get(key) and fault.get(key) and previous[key] != fault[key]
                            for key in ('origin', 'stream'))):
                        result['conflicting_faults'] = dict(trace=previous, immutable=fault)
                        blocker('collection', 'Runtime trace conflicts with immutable first-fault evidence.')
                    elif not previous:
                        record_gui_fault(fault)
            except ERRORS as sidecar_error:
                result['immutable_fault_error'] = str(sidecar_error)
                blocker('collection', 'Immutable first-fault evidence could not be reconciled: ' + str(sidecar_error))
        if not summary and not result.get('first_gui_fault'):
            # A reboot can remove /tmp. Preserve the previous incident's reason
            # without treating persistent metadata as a completed live test.
            stored = result.get('persistent_journal', {}).get('current_session', {})
            record = stored.get('records', {}).get('fault')
            if isinstance(record, dict):
                try:
                    validate_identity(record, manifest, stored.get('session_id'))
                    fault = record.get('first_fault')
                    if isinstance(fault, dict) and fault_code(fault) > 1:
                        result['persistent_first_fault_snapshot'] = record
                        record_gui_fault(fault)
                except ERRORS as error:
                    result['persistent_fault_identity_error'] = str(error)
        if sdr:
            try:
                result['sdr_file'] = preserve_sdr(output, sdr)
                if result.get('worker_active'):
                    blocker('rf_decode', 'USB worker is still active; SDR input preserved but decoder was not run.')
                elif any(item['stage'] == 'collection' for item in result['blockers']):
                    blocker('rf_decode', 'Runtime collection is incomplete; SDR input preserved but decoder was not run.')
                elif summary and summary['rf_disabled']:
                    result['stages']['rf_decode'] = dict(status='NOT_APPLICABLE', reason='RF disabled during this session; external recording is preserved only.')
                elif not identity_ok:
                    blocker('rf_decode', 'Installed GUI identity was not verified; decoder was not run.')
                else:
                    decode_sdr(output, manifest, sdr, result)
                    result['stages']['rf_decode'] = dict(status='DECODED_LISTENING_REQUIRED')
            except ERRORS as error:
                blocker('rf_decode', error)
        if not any(item['stage'] == 'collection' for item in result['blockers']):
            result['stages']['collection'] = dict(status='PASS')
        if result['blockers']:
            priority = {'gui': 0, 'audio': 1, 'qualification': 2, 'collection': 3, 'rf_decode': 4}
            primary = min(result['blockers'], key=lambda item: priority[item['stage']])
            result['reason'] = primary['reason']
            result['primary_failure_stage'] = primary['stage']
        elif (summary and summary['rf_disabled'] and strict_contract(manifest) and
              summary['output_contract']['status'] == 'PASS'):
            result['status'] = 'READY_FOR_RF_TEST'
            result['ready_for_rf_test'] = True
        elif result['rf_recording_decoded']:
            result['status'] = 'RF_RECORDING_DECODED_LISTENING_REQUIRED'
        else:
            result['status'] = 'COLLECTED_NO_SDR_RECORDING'
    finally:
        save(output / 'rf-evidence.json', result)
        files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(sha256(p) + '  ' + p.relative_to(output).as_posix() + '\n' for p in files))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sdr-wav', type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, exist_ok=False)
    try:
        manifest = json.loads(Path(__file__).with_name('rf-test-manifest.json').read_text())
        if not isinstance(manifest, dict):
            raise ValueError('Kit manifest must be a JSON object.')
    except (OSError, ValueError) as error:
        # Retain stopped runtime diagnostics even when the kit transfer or its
        # manifest is damaged. Identity remains unverified and decoding blocked.
        manifest = {'_load_error': str(error)}
    result = collect_evidence(args.output, manifest, args.sdr_wav)
    print('Evidence status: ' + result['status'], flush=True)
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
