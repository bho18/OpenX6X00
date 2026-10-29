#!/usr/bin/env python3
"""Prepare/analyze local native-audio evidence. No network, radio or audio-device access.

Uses Python's standard library, including on Windows. A decoding process is only
started by the explicit decode subcommand; paths are never executed from metadata.
"""
import argparse
import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import wave
import zipfile

IMAGE_COMMIT = '2083e3e7f819e2b3fc4feaf27e5c9a007c996308'
RADE_COMMIT = 'c8a3dc156045cae2cd251e1a4be0c304c9ddf2f9'
MODEL_SHA = '4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def inside(root, name):
    """Only regular evidence files under the supplied root; no metadata escapes."""
    relative = PurePosixPath(str(name).replace('\\', '/'))
    if not relative.parts or relative.is_absolute() or '..' in relative.parts or ':' in str(relative):
        raise ValueError('evidence path must be relative and confined: ' + str(name))
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents):
        raise ValueError('symlink evidence is not supported: ' + str(name))
    if not path.is_file() or root not in path.resolve().parents:
        raise ValueError('missing/confined evidence file: ' + str(name))
    return path


def extract(archive, output):
    """Normalize Windows ZIP paths, validating everything before extraction."""
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 4000 or sum(m.file_size for m in members) > 256 * 1024 * 1024:
            raise ValueError('radio ZIP exceeds 4000 entries or 256 MiB; keep large IQ on PC')
        seen = set()
        for m in members:
            p = PurePosixPath(m.filename.replace('\\', '/'))
            key = str(p).casefold()
            if (not p.parts or p.is_absolute() or '..' in p.parts or ':' in str(p)
                    or stat.S_ISLNK(m.external_attr >> 16) or key in seen):
                raise ValueError('unsafe/duplicate ZIP entry: ' + m.filename)
            seen.add(key)
        for m in members:
            path = output.joinpath(*PurePosixPath(m.filename.replace('\\', '/')).parts)
            if m.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(m) as incoming, path.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)


def pcm_summary(path):
    with wave.open(str(path), 'rb') as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if width != 2 or channels not in (1, 2) or frames * channels > 12_000_000:
            raise ValueError('expected bounded 16-bit mono/stereo PCM WAV: ' + str(path))
        raw = array.array('h', wav.readframes(frames))
    if sys.byteorder != 'little':
        raw.byteswap()
    if len(raw) != frames * channels or not rate:
        raise ValueError('truncated/invalid WAV: ' + str(path))
    stats = []
    for ch in range(channels):
        data = raw[ch::channels]
        after = data[min(int(rate * .5), len(data)):]
        if not after:
            after = data
        mean = sum(after) / len(after) if after else 0
        ac = math.sqrt(sum((x - mean) ** 2 for x in after) / len(after)) if after else 0
        stats.append({'channel': ch, 'dc_mean_after_0_5s': mean, 'ac_rms_after_0_5s': ac,
                      'peak': max(map(abs, data), default=0),
                      'clipped_samples': sum(x <= -32768 or x >= 32767 for x in data),
                      'nonzero_samples': sum(x != 0 for x in data)})
    return {'sample_rate': rate, 'channels': channels, 'frames': frames,
            'seconds': frames / rate, 'sha256': sha(path), 'channel_statistics': stats}


def recovery(case):
    a = case.get('audio', {})
    packet = case.get('radio_at_result_export', {}).get('physical_packet', {})
    required = ('restoration_complete', 'readback_match', 'radio_before', 'radio_after',
                'alsa_before', 'alsa_after', 'command_clear_packet_sequence')
    if not all(k in case for k in required) or not a or not packet:
        return 'INCONCLUSIVE'
    fields = ('capture_dropped_samples', 'capture_hole_samples', 'capture_errors', 'playback_write_errors')
    if not all(k in a for k in fields) or 'playback_flush_acknowledged' not in a:
        return 'INCONCLUSIVE'
    cause = {'Manual Stop test': (1, 0), 'Watchdog timeout test': (3, 0), 'Audio failure test': (7, 1)}
    expected = cause.get(case.get('name'))
    okay = (case['restoration_complete'] and case['readback_match'] == 1
            and case.get('run_started') is True and case.get('preparation_failed') is False
            and case['radio_before'] is not None and case['radio_before'] == case['radio_after']
            and case['alsa_before'] is not None and case['alsa_before'] == case['alsa_after']
            and case.get('transport_failures') == 0 and case.get('restoration_failures') == 0
            and all(a[k] == 0 for k in fields) and a['playback_flush_acknowledged'] == 1
            and packet.get('known') and packet.get('fresh') and packet.get('tx') is False
            and packet.get('sequence', -1) > case['command_clear_packet_sequence'])
    if expected:
        okay = okay and case.get('seen_packet_tx') is True and case.get('first_stop_reason') == expected[0]
        okay = okay and (case.get('audio_failures', -1) == 0 if not expected[1]
                         else case.get('audio_failures', 0) > 0)
    else:
        okay = okay and case.get('audio_failures') == 0
    return 'PASS' if okay else 'FAIL'


def analyze(root):
    root = Path(root).resolve()
    cases = []
    for candidate in sorted(root.rglob('coordinator.json')):
        path = inside(root, candidate.relative_to(root))
        case = load(path)
        a = case.get('audio', {})
        entry = {k: case.get(k) for k in ('case_number', 'name', 'matrix_case', 'microphone',
                 'capture_channel', 'mixer_choice', 'seen_packet_tx', 'first_stop_reason', 'command_clear_elapsed_ms')}
        entry['path'] = path.relative_to(root).as_posix()
        entry['selected_state_and_recovery'] = recovery(case)
        entry['underflow_count_including_startup'] = a.get('playback_underflows_including_startup')
        entry['underflows_after_first_write'] = a.get('playback_underflows_after_first_write')
        entry['underflow_note'] = 'An event after first write is not automatically a steady-state failure; inspect event times and buffering.'
        entry['waves'] = {p.name: pcm_summary(inside(root, p.relative_to(root)))
                          for p in sorted(path.parent.glob('*.wav'))}
        cases.append(entry)
    if not cases:
        raise ValueError('no coordinator.json cases found')
    manifests = []
    for candidate in sorted(root.rglob('build-manifest.json')):
        path = inside(root, candidate.relative_to(root))
        manifest = load(path)
        manifests.append({'path': path.relative_to(root).as_posix(), 'sha256': sha(path),
                          'firmware_commit': manifest.get('firmware_commit'),
                          'source_manifest_sha256': manifest.get('source_manifest_sha256'),
                          'model_sha256': manifest.get('model_sha256')})
    return {'schema_version': 1, 'kind': 'native-session-assessment', 'cases': cases,
            'build_manifests': manifests, 'radio_control': False, 'automatic_upload': False,
            'rf_cutoff_500ms': 'INCONCLUSIVE: requires separately correlated external evidence',
            'live_transmitted_speech': 'INCONCLUSIVE: requires reference decode and listening assessment',
            'native_receive_independence': 'INCONCLUSIVE: requires identifiable RF input during playback',
            'original_70_percent_margin': 'FAIL: retained separately from sustained throughput'}


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'session.json', {
        'schema_version': 1, 'image_commit': IMAGE_COMMIT,
        'release': 'freedv-native-diagnostics-20261028.1',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'sequence': 'proof_of_concept_first', 'formal_rf_timing_is_separate': True,
        'sdr_device': None, 'sdr_application': None, 'gui_waterfall_responsive': None,
        'notes': 'Record displayed case numbers. Do not put complete configuration databases here.'})
    write_json(output / 'recordings.json', {'schema_version': 1, 'recordings': [
        {'label': f'Turn {i}', 'case_path': None, 'wav': None,
         'expected_phrase': f'Turn {i}, X6200 hand microphone test',
         'iq_file_on_pc': None, 'iq_sha256': None, 'frequency_hz': None,
         'iq_sample_rate': None, 'iq_format': None,
         'normal_completion': i != 3, 'decoded_phrase_intelligible': None}
        for i in range(1, 11)]})
    (output / 'notes.txt').write_text('Case number | checklist step | speaker | SDR | restoration | observations\n', encoding='utf-8')


def validate_reference(source, build, sources, fixture, output):
    source, build = source.resolve(strict=True), build.resolve(strict=True)
    pins = load(sources)
    if pins['rade_c']['commit'] != RADE_COMMIT:
        raise ValueError('source manifest does not describe the tested RADE V1 pin')
    revision = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=True).stdout.strip()
    if revision != RADE_COMMIT:
        raise ValueError('RADE reference checkout differs from the required pin')
    cache = (build / 'CMakeCache.txt').read_text()
    if 'CMAKE_HOME_DIRECTORY:INTERNAL=' + str(source) + '\n' not in cache:
        raise ValueError('CMake build does not use the pinned source checkout')
    models = list(build.glob('build_opus-prefix/src/build_opus/opus_data-*.tar.gz'))
    if not any(sha(path) == MODEL_SHA for path in models):
        raise ValueError('matching Opus model archive missing or checksum invalid')
    if sha(fixture) != pins['rade_c']['modem_fixture']['sha256']:
        raise ValueError('reference modem fixture hash differs from sources.json')
    output.mkdir(parents=True, exist_ok=False)
    built = subprocess.run(['cmake', '--build', str(build), '--target', 'rade_rx_wav', '-j2'],
                           capture_output=True, timeout=600, check=False)
    (output / 'build.log').write_bytes(built.stdout + built.stderr)
    if built.returncode:
        raise ValueError('reference build failed; see build.log')
    decoder = build / 'src/rade_rx_wav'
    library = build / 'src/librade.so.0.1'
    wav = output / 'known-modem.wav'
    with wave.open(str(wav), 'wb') as stream:
        stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        stream.writeframes(fixture.read_bytes())
    speech = output / 'known-decoded.wav'
    checked = subprocess.run([str(decoder), str(wav), str(speech)], capture_output=True, timeout=120, check=False)
    (output / 'decode.log').write_bytes(checked.stdout + checked.stderr)
    if checked.returncode:
        raise ValueError('reference fixture decode failed')
    decoded = pcm_summary(speech)
    if decoded['seconds'] < 1 or not any(c['nonzero_samples'] for c in decoded['channel_statistics']):
        raise ValueError('reference fixture did not produce usable non-silent speech')
    patch = subprocess.run(['git', '-C', str(source), 'diff', '--binary', 'HEAD'],
                           capture_output=True, check=True).stdout
    (output / 'source.patch').write_bytes(patch)
    write_json(output / 'reference.json', {'schema_version': 1, 'rade_source_commit': revision,
        'source_patch_sha256': hashlib.sha256(patch).hexdigest(), 'model_sha256': MODEL_SHA,
        'fixture_sha256': sha(fixture), 'decoder_sha256': sha(decoder),
        'runtime_library': str(library.resolve()), 'runtime_library_sha256': sha(library),
        'fixture_check_passed': True, 'decoded': decoded})


def decode(recordings, decoder, converter, output, reference):
    metadata = load(recordings)
    root = recordings.parent.resolve()
    selected = [r for r in metadata['recordings'] if r.get('wav')]
    if not selected:
        raise ValueError('recordings.json has no WAV associations')
    # Check every input before starting any external program or writing results.
    inputs = [(r, inside(root, r['wav'])) for r in selected]
    decoder, converter = decoder.resolve(strict=True), converter.resolve(strict=True)
    identity = load(reference)
    if (identity.get('rade_source_commit') != RADE_COMMIT or identity.get('model_sha256') != MODEL_SHA
            or identity.get('decoder_sha256') != sha(decoder) or identity.get('fixture_check_passed') is not True
            or sha(Path(identity['runtime_library'])) != identity.get('runtime_library_sha256')):
        raise ValueError('decoder/reference provenance changed or fixture check did not pass')
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for index, (entry, source) in enumerate(inputs, 1):
        folder = output / f'tx-{index:02d}'
        folder.mkdir()
        modem, speech = folder / 'modem-8000.wav', folder / 'decoded-speech.wav'
        commands = [[str(converter), '-nostdin', '-v', 'error', '-i', str(source),
                     '-ac', '1', '-ar', '8000', '-c:a', 'pcm_s16le', str(modem)],
                    [str(decoder), str(modem), str(speech)]]
        row = {'label': entry.get('label'), 'case_path': entry.get('case_path'),
               'input': str(source.relative_to(root)), 'input_sha256': sha(source),
               'expected_phrase': entry.get('expected_phrase'), 'commands': commands,
               'transmitted_speech_gate': 'INCONCLUSIVE: listen to decoded speech and identify the phrase'}
        for number, cmd in enumerate(commands, 1):
            result = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
            (folder / f'step-{number}.log').write_bytes(result.stdout + result.stderr)
            if result.returncode:
                row['error'] = f'step {number} exited {result.returncode}'
                break
        else:
            row['modem'] = pcm_summary(modem)
            row['decoded'] = pcm_summary(speech)
            if not row['decoded']['frames'] or not any(c['nonzero_samples'] for c in row['decoded']['channel_statistics']):
                row['error'] = 'decoder produced empty/silent speech'
        results.append(row)
    write_json(output / 'decode-results.json', {
        'schema_version': 1, 'decoder_sha256': sha(decoder), 'converter_sha256': sha(converter),
        'required_rade_source_commit': RADE_COMMIT, 'required_model_sha256': MODEL_SHA,
        'reference_manifest_sha256': sha(reference),
        'recordings': results})
    return not any('error' in r for r in results)


def timing(interval, uncertainty, evidence, output):
    if not all(math.isfinite(x) and x >= 0 for x in (interval, uncertainty)):
        raise ValueError('timing values must be finite and nonnegative')
    evidence = evidence.resolve(strict=True)
    if not evidence.is_file():
        raise ValueError('timing evidence must be a regular recording file')
    upper = interval + uncertainty
    write_json(output, {'schema_version': 1, 'method': 'operator_measured_conservative_video_upper_bound',
                       'observed_interval_ms': interval, 'uncertainty_ms': uncertainty,
                       'conservative_upper_bound_ms': upper, 'evidence_sha256': sha(evidence),
                       'evidence_file': evidence.name,
                       'result': 'PASS' if upper <= 500 else 'INCONCLUSIVE',
                       'conditions': 'Valid only if the video correlates local Stop with live RF disappearance, continuous IQ confirms RF-off, and display delay is nonnegative. Values are operator measurements, not extracted automatically.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    p = subs.add_parser('prepare'); p.add_argument('--output', type=Path, required=True)
    p = subs.add_parser('analyze'); p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p = subs.add_parser('decode'); p.add_argument('--recordings', type=Path, required=True)
    p.add_argument('--decoder', type=Path, required=True); p.add_argument('--ffmpeg', type=Path, required=True)
    p.add_argument('--reference', type=Path, required=True, help='validated reference.json from validate-reference')
    p.add_argument('--output', type=Path, required=True)
    p = subs.add_parser('validate-reference'); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--build', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--sources', type=Path, default=Path(__file__).with_name('sources.json'))
    p.add_argument('--fixture', type=Path, default=Path(__file__).with_name('fixtures') / 'rade-v1-reference-s16le.raw')
    p = subs.add_parser('timing'); p.add_argument('--interval-ms', type=float, required=True)
    p.add_argument('--uncertainty-ms', type=float, required=True); p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args.output)
        elif args.action == 'analyze':
            with tempfile.TemporaryDirectory(prefix='freedv-evidence-') as name:
                root = args.input
                if root.is_file():
                    root = Path(name); extract(args.input, root)
                write_json(args.output, analyze(root))
        elif args.action == 'decode':
            if not decode(args.recordings, args.decoder, args.ffmpeg, args.output, args.reference):
                raise ValueError('one or more reference decodes failed; see decode-results.json')
        elif args.action == 'validate-reference':
            validate_reference(args.source, args.build, args.sources, args.fixture, args.output)
        else:
            timing(args.interval_ms, args.uncertainty_ms, args.evidence, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, wave.Error, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
