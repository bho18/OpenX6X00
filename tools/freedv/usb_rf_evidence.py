#!/usr/bin/env python3
"""Collect stopped USB GUI evidence and optional off-air audio; never key RF."""
import argparse
from array import array
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave
from usb_headset_inventory import collect, command, sha256


ERRORS = (OSError, ValueError, KeyError, EOFError, wave.Error, subprocess.SubprocessError)


class ActiveUsbWorkerError(ValueError):
    pass


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def read_sdr(path):
    with wave.open(str(path), 'rb') as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if channels not in (1, 2) or width != 2 or rate not in (8000, 44100, 48000) or not 0 < frames <= rate * 30:
            raise ValueError(f'SDR WAV has {channels} channels, {width * 8}-bit samples, {rate} Hz and {frames / rate:.3f} seconds. '
                             'Use up to 30 seconds of PCM16 mono/stereo at 8, 44.1 or 48 kHz; record demodulated USB audio, not IQ.')
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
    return dict(last=states[-1], first_fault=first_fault, metadata=metadata, streams=streams[-32:],
                rf_disabled=rf_disabled, qualification=qualification,
                unexpected_qualification_tx=any(row.get('rf_disabled') is True and row.get('rf_tx') is True for row in states))


def preserve_immutable_diagnostics(prefix, output):
    """Read atomically published JSON sidecars even if shutdown is blocked.

    A mutable event trace or PCM tap is never a substitute for these records.
    Validate the bounded bytes once before saving, so a partial JSON write is
    not presented as an immutable first-fault snapshot.
    """
    for suffix in ('fault.json', 'metadata.json', 'config.json', 'configuration.json'):
        path = Path(str(prefix) + '-' + suffix)
        if not path.exists():
            continue
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Immutable runtime diagnostics exceed collection bound: ' + suffix)
        data = path.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise ValueError('Immutable runtime diagnostics exceed collection bound: ' + suffix)
        record = json.loads(data)
        if not isinstance(record, dict):
            raise ValueError('Immutable runtime diagnostics must be a JSON object: ' + suffix)
        (output / ('runtime-' + suffix)).write_bytes(data)


def collect_trace(output):
    sessions = sorted(Path('/tmp').glob('freedv-usb-*.jsonl'), key=lambda p: p.stat().st_mtime)
    if not sessions:
        raise ValueError('No GUI USB-mode trace found. Complete a USB-mode test first.')
    trace = sessions[-1]
    prefix = trace.with_suffix('')
    preserve_immutable_diagnostics(prefix, output)
    for process in Path('/proc').glob('[0-9]*/fd'):
        for fd in process.glob('*'):
            try:
                if str(fd.resolve()) == str(trace):
                    raise ActiveUsbWorkerError('USB audio worker is still running. Immutable diagnostics retained; Stop/Exit before collecting its mutable trace and audio.')
            except OSError:
                pass
    if trace.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Trace exceeds collection bound.')
    shutil.copy2(trace, output / 'usb-session.jsonl')
    rows = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    summary = summarize_trace(rows)
    summary['audio_taps'] = {}
    for name in ('mic', 'radio-rx', 'modem', 'decoded'):
        path = Path(str(prefix) + '-' + name + '.raw')
        if path.exists():
            size = path.stat().st_size
            if size > 48000 * 2 * 30 or size % 2:
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
    return summary


def preserve_sdr(output, sdr):
    size = sdr.stat().st_size
    info = dict(bytes=size, sha256=sha256(sdr))
    if size <= 16 * 1024 * 1024:
        shutil.copy2(sdr, output / 'sdr-usb-audio.wav')
    else:
        with sdr.open('rb') as source:
            (output / 'sdr-header.bin').write_bytes(source.read(4096))
        raise ValueError('SDR file exceeds the 16 MiB evidence bound; header retained. Export a short demodulated-audio WAV.')
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
            if manifest.get('_load_error'):
                raise ValueError('Kit manifest unavailable: ' + manifest['_load_error'])
            inventory = collect(expected_gui=manifest['gui_sha256'])
            save(output / 'inventory.json', inventory)
            result['inventory_status'] = inventory['status']
            identity_ok = True
        except ERRORS as error:
            blocker('collection', error)
        try:
            save(output / 'post-stop-diagnostics.json', post_stop_diagnostics())
        except ERRORS as error:
            # Supplementary diagnostic commands may be unavailable after a fault.
            save(output / 'post-stop-diagnostics.json', dict(scope='COLLECTION_TIME_CURRENT_STATE', error=str(error)))
        try:
            summary = collect_trace(output)
            result['last_gui_state'] = summary['last']
            result['gui_session_fault'] = fault_code(summary['first_fault'] or summary['last'])
            result['rf_disabled'] = summary['rf_disabled']
            result['audio_taps'] = summary['audio_taps']
            if summary['metadata']:
                result['runtime_metadata'] = summary['metadata']
            if summary['streams']:
                result['runtime_stream_configuration'] = summary['streams']
            if summary['first_fault']:
                record_gui_fault(summary['first_fault'])
            else:
                result['stages']['gui'] = dict(status='STOPPED', intentional_stop=result['gui_session_fault'] == 1)
                # A fault-free stop alone is not an audio continuity qualification.
                result['stages']['audio'] = dict(status='UNASSESSED', reason='Review per-stream runtime continuity counters and phase events.')
            last = summary['last']
            contract_v2 = manifest.get('usb_startup_contract_version') == 2 or any(
                row.get('schema') == 'x6200.usb-audio.v2' for row in summary['metadata'])
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
            # The coordinator may be blocked after the independent watchdog has
            # already published its first-fault snapshot. Keep its origin as the
            # primary GUI failure without reading a changing event/PCM file.
            fault_path = output / 'runtime-fault.json'
            if fault_path.exists():
                try:
                    record = json.loads(fault_path.read_text())
                    result['immutable_first_fault_snapshot'] = record
                    fault = record.get('first_fault', record.get('fault', record))
                    if isinstance(fault, dict) and fault_code(fault) > 1:
                        record_gui_fault(fault)
                except ERRORS as sidecar_error:
                    result['immutable_fault_error'] = str(sidecar_error)
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
        elif summary and summary['rf_disabled']:
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
