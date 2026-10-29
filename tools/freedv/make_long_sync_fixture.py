#!/usr/bin/env python3
"""Generate and validate one long RADE V1 transmission using pinned native tools.

Only PCM, provenance and licenses are published. Native binaries never become a
radio package, and native timing never qualifies X6200 performance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import wave

SPEECH_SECONDS = 1860
SPEECH_RATE = 16000
MODEM_RATE = 8000
MODEM_FRAME = 960
EOO_SAMPLES = 1152
MEASURED_SECONDS = 1800
WARMUP_SECONDS = 30


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(command, **kwargs):
    command = list(map(str, command))
    print('+', ' '.join(command), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def repeat_speech(source, output, expected_sha, seconds=SPEECH_SECONDS):
    """Repeat speech samples, never encoded modem frames or transmitter state."""
    if sha(source) != expected_sha:
        raise ValueError('pinned speech input hash differs')
    with wave.open(str(source), 'rb') as src:
        if (src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getcomptype()) != (1, 2, SPEECH_RATE, 'NONE'):
            raise ValueError('expected mono 16 kHz PCM16 speech')
        payload = src.readframes(src.getnframes())
    if not payload or len(payload) % 2 or type(seconds) is not int or seconds <= 0:
        raise ValueError('invalid speech payload or duration')
    remaining = seconds * SPEECH_RATE * 2
    with wave.open(str(output), 'wb') as dst:
        dst.setnchannels(1); dst.setsampwidth(2); dst.setframerate(SPEECH_RATE)
        while remaining:
            part = payload[:remaining]
            dst.writeframesraw(part)
            remaining -= len(part)
    return seconds * SPEECH_RATE


def extract_modem(wav_path, output, generator_stderr):
    """Cross-check actual generator frame count and V1 dimensions with WAV data."""
    frames = re.findall(r'^Modem frames: ([0-9]+) \+ EOO$', generator_stderr, re.M)
    dimensions = re.findall(r'^rade_open: V1 n_features_in=432 Nmf=960 Neoo=1152 n_eoo_bits=180$', generator_stderr, re.M)
    if len(frames) != 1 or len(dimensions) != 1:
        raise ValueError('generator did not report one V1 transmission with terminal EOO')
    normal_frames = int(frames[0])
    if normal_frames != SPEECH_SECONDS * MODEM_RATE // MODEM_FRAME:
        raise ValueError('unexpected complete modem frame count')
    first_eoo = normal_frames * MODEM_FRAME
    with wave.open(str(wav_path), 'rb') as src:
        if (src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getcomptype()) != (1, 2, MODEM_RATE, 'NONE'):
            raise ValueError('expected mono 8 kHz PCM16 modem output')
        samples = src.getnframes()
        if samples != first_eoo + EOO_SAMPLES or not 1840 * MODEM_RATE <= first_eoo or samples * 2 >= 32 * 1024 * 1024:
            raise ValueError('modem length or EOO placement differs')
        with output.open('wb') as dst:
            while True:
                block = src.readframes(65536)
                if not block:
                    break
                dst.write(block)
    if output.stat().st_size != samples * 2:
        raise ValueError('modem payload extraction was incomplete')
    return {'path': 'fixture.raw', 'sha256': sha(output), 'bytes': samples * 2,
            'samples': samples, 'sample_rate_hz': MODEM_RATE, 'channels': 1,
            'sample_format': 'signed-16-bit-little-endian', 'first_eoo_sample': first_eoo,
            'normal_modem_frames': normal_frames, 'modem_samples_per_frame': MODEM_FRAME,
            'terminal_eoo_samples': EOO_SAMPLES}


def steady_summary(result):
    """Additional continuous-transmission checks after ordinary pipeline validation."""
    measured, full = result['measured'], result['full']
    if (result['execution_complete'] is not True or full['eoo_blocks'] != 0 or
            measured['frames'] <= 0 or
            not MEASURED_SECONDS * 1_000_000_000 <= measured['audio_ns'] <= MEASURED_SECONDS * 1_000_000_000 + 140_000_000 or
            measured['sync_blocks'] != measured['frames'] or
            measured['feature_blocks'] != measured['frames'] or
            not measured['nonzero_output_samples']):
        raise ValueError('native long fixture did not retain synchronized speech throughout measurement')
    if not (MEASURED_SECONDS + WARMUP_SECONDS) * 1_000_000_000 <= full['audio_ns'] <= (MEASURED_SECONDS + WARMUP_SECONDS) * 1_000_000_000 + 280_000_000:
        raise ValueError('native validation warm-up/measurement duration differs')
    return {'passed': True, 'seconds': MEASURED_SECONDS, 'warmup_seconds': WARMUP_SECONDS,
            'execution': 'serial', 'pacing': 'unpaced', 'result_file': 'native-validation.json',
            'measured_frames': measured['frames'], 'measured_sync_blocks': measured['sync_blocks'],
            'measured_feature_blocks': measured['feature_blocks'], 'eoo_blocks': full['eoo_blocks'],
            'radio_timing_qualified': False}


def main():
    producer_source_sha = sha(Path(__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-work', type=Path, required=True,
                        help='completed build_benchmark_package.py --host-validation work directory')
    parser.add_argument('--output', type=Path, required=True, help='new directory outside source and host build')
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    if sys.byteorder != 'little':
        parser.error('this native generator requires a little-endian host for PCM16 payloads')
    code, work, output = args.code_root.resolve(), args.host_work.resolve(), args.output.resolve()
    if any(output == root or root in output.parents for root in (code, work)):
        parser.error('output must be outside source and native build directories')
    pins = json.loads((code / 'tools/freedv/sources.json').read_text())
    host_manifest_path = work / 'package/package-manifest.json'
    host_manifest = json.loads(host_manifest_path.read_text())
    if host_manifest.get('target') != 'host-validation' or host_manifest.get('radio_control') is not False:
        parser.error('native validation reference build required')
    for name in ('rade_c', 'opus', 'x6200_libsamplerate'):
        for field in ('commit', 'model_sha256', 'speech_fixture_sha256', 'source_sha256'):
            if field in pins[name] and host_manifest['sources'][name].get(field) != pins[name][field]:
                raise ValueError('native source pin differs: ' + name + '.' + field)
    for name in ('rade_c', 'opus'):
        head = subprocess.check_output(['git', '-C', str(work / 'sources' / name), 'rev-parse', 'HEAD'], text=True).strip()
        if head != pins[name]['commit']:
            raise ValueError('native source checkout differs: ' + name)
    rade = work / 'sources/rade_c'
    if subprocess.check_output(['git', '-C', str(rade), 'diff', '--', 'src'], text=True):
        raise ValueError('native RADE implementation must retain pinned source')
    opus = work / 'rade_c/build_opus-prefix/src/build_opus'
    model = opus / ('opus_data-' + pins['opus']['model_sha256'] + '.tar.gz')
    if sha(model) != pins['opus']['model_sha256'] or host_manifest['opus_model_sha256'] != sha(model):
        raise ValueError('native Opus model hash differs')
    library = work / 'rade_c/src/librade.so.0.1'
    if sha(library) != sha(work / 'package/lib/librade.so.0.1'):
        raise ValueError('native library differs from recorded host package')
    if sha(opus / 'config.h') != host_manifest['opus_config_sha256']:
        raise ValueError('native Opus configuration differs')
    if sha(work / 'samplerate/src/libsamplerate.a') != host_manifest['libsamplerate_static_sha256']:
        raise ValueError('native resampler differs')
    revision = subprocess.check_output(['git', '-C', str(code), 'rev-parse', 'HEAD'], text=True).strip()
    benchmark_source = code / 'tools/freedv/rade_pipeline_bench.c'
    benchmark_source_sha = sha(benchmark_source)
    committed = subprocess.check_output(['git', '-C', str(code), 'show', revision + ':tools/freedv/rade_pipeline_bench.c'])
    if hashlib.sha256(committed).hexdigest() != sha(benchmark_source):
        raise ValueError('commit native validation benchmark changes first')
    flags = {}
    for target in ('rade_tx_wav', 'rade'):
        path = work / 'rade_c/src/CMakeFiles' / (target + '.dir/flags.make')
        text = path.read_text()
        entries = {}
        for field in ('C_FLAGS', 'C_DEFINES'):
            match = re.search(r'^' + field + r' = (.*)$', text, re.M)
            if not match:
                raise ValueError('native compiler flags record missing: ' + target + '.' + field)
            entries[field] = match[1]
        flags[target] = {'flags_make_sha256': sha(path), **entries}
    cache = (work / 'rade_c/CMakeCache.txt').read_text()
    compiler_match = re.search(r'^CMAKE_C_COMPILER:FILEPATH=(.+)$', cache, re.M)
    if not compiler_match:
        raise ValueError('native compiler record missing')
    compiler = compiler_match[1]
    compiler_version = subprocess.check_output([compiler, '--version'], text=True).splitlines()[0]
    triplet = subprocess.check_output([compiler, '-dumpmachine'], text=True).strip()
    if 'arm' in triplet or compiler_version != host_manifest['compiler_version']:
        raise ValueError('expected the recorded native compiler')
    output.mkdir(parents=True, exist_ok=False)
    pending = output / 'pending'; pending.mkdir()
    speech = output / 'speech.wav'
    speech_samples = repeat_speech(rade / 'input_sample.wav', speech, pins['rade_c']['speech_fixture_sha256'])
    generator = work / 'rade_c/src/rade_tx_wav'
    environment = dict(os.environ, LD_LIBRARY_PATH=str(work / 'rade_c/src'))
    modem = output / 'modem.wav'
    with (output / 'generator.stdout').open('w') as stdout, (output / 'generator.stderr').open('w') as stderr:
        run([generator, '-v', '1', speech, modem], env=environment, stdout=stdout, stderr=stderr, timeout=3600)
    fixture = extract_modem(modem, pending / 'fixture.raw', (output / 'generator.stderr').read_text())
    benchmark = output / 'native-pipeline-bench'
    includes = [rade / 'src', opus / 'dnn', opus / 'celt', opus / 'include', opus,
                work / 'sources/libsamplerate-0.2.2/include']
    command = [compiler, '-O2', '-g', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-error=cpp',
               '-pthread', '-include', opus / 'config.h', *['-I' + str(p) for p in includes],
               benchmark_source, '-L' + str(work / 'rade_c/src'), '-lrade', opus / '.libs/libopus.a',
               work / 'samplerate/src/libsamplerate.a', '-lm', '-Wl,-rpath,' + str(work / 'rade_c/src'), '-o', benchmark]
    run(command)
    validation_path = pending / 'native-validation.json'
    with validation_path.open('w') as stdout, (output / 'native-validation.stderr').open('w') as stderr:
        run([benchmark, 'serial', pending / 'fixture.raw', MEASURED_SECONDS, 'unpaced', WARMUP_SECONDS,
             'continuous'], env=environment, stdout=stdout, stderr=stderr, timeout=3600)
    sys.path.insert(0, str(code / 'tools/freedv'))
    from pipeline_evidence import strict_json, validate_pipeline_result
    result = strict_json(validation_path.read_text())
    validate_pipeline_result(result, execution='serial', pacing='unpaced', workload='continuous',
                             seconds=MEASURED_SECONDS, warmup=WARMUP_SECONDS, fixture_name='rade-modem')
    validation = steady_summary(result)
    validation['result_sha256'] = sha(validation_path)
    if sha(Path(__file__)) != producer_source_sha or sha(benchmark_source) != benchmark_source_sha:
        raise ValueError('producer or validation source changed during generation')
    manifest = {
        'schema_version': 1, 'kind': 'rade-v1-continuous-fixture', 'revision': 'rade-v1-long-sync-1',
        'radio_io': False, 'radio_qualified': False, 'cross_host_bit_reproducible': False,
        'fixture': fixture,
        'sources': {'rade_c_commit': pins['rade_c']['commit'], 'opus_commit': pins['opus']['commit'],
                    'opus_model_sha256': pins['opus']['model_sha256'],
                    'speech_fixture_sha256': pins['rade_c']['speech_fixture_sha256'],
                    'libsamplerate_source_sha256': pins['x6200_libsamplerate']['source_sha256']},
        'generation': {'producer_source_sha256': producer_source_sha, 'code_commit': revision,
                       'native_host_only': True, 'speech_seconds': SPEECH_SECONDS,
                       'speech_samples': speech_samples, 'speech_rate_hz': SPEECH_RATE,
                       'repeated_speech_wav_sha256': sha(speech),
                       'generator_source_sha256': sha(rade / 'src/rade_tx_wav.c'),
                       'generator_binary_sha256': sha(generator), 'generator_library_sha256': sha(library),
                       'opus_config_sha256': sha(opus / 'config.h'), 'compiler_version': compiler_version,
                       'compiler_triplet': triplet, 'host_package_manifest_sha256': sha(host_manifest_path),
                       'native_target_compile_flags': flags, 'runtime_cpu_dispatch_recorded': False,
                       'host_source_archive_sha256': host_manifest['corresponding_source_archive_sha256'],
                       'validation_benchmark_source_sha256': benchmark_source_sha,
                       'validation_benchmark_binary_sha256': sha(benchmark),
                       'validation_compile_command': list(map(str, command)),
                       'one_transmitter_session': True, 'intermediate_eoo_frames': 0,
                       'speech_repetition': 'repeat original PCM samples to fixed duration; one persistent transmitter and feature extractor'},
        'native_validation': validation,
        'qualification_limit': 'Native execution validates this finite fixture; it does not prove matching-SDK decoding, radio timing or physical audio/RF behavior.',
        'attribution': {'speech_source': pins['rade_c']['repository'] + '/blob/' + pins['rade_c']['commit'] + '/input_sample.wav',
                        'generator_source': pins['rade_c']['repository'] + '/blob/' + pins['rade_c']['commit'] + '/src/rade_tx_wav.c',
                        'licenses_directory': 'licenses'}}
    (pending / 'fixture-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    licenses = pending / 'licenses'; licenses.mkdir()
    for license_file in (work / 'package/licenses').iterdir():
        if license_file.name.startswith(('rade_c-', 'opus-', 'libsamplerate-')):
            shutil.copy2(license_file, licenses / license_file.name)
    shutil.copy2(code / 'tools/freedv/THIRD-PARTY-NOTICES.md', licenses / 'THIRD-PARTY-NOTICES.md')
    hashes = [sha(p) + '  ' + str(p.relative_to(pending)) for p in sorted(pending.rglob('*')) if p.is_file()]
    (pending / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n')
    pending.rename(output / 'artifact')
    print('PASS: full native continuous fixture validation; publish data only:', output / 'artifact')


if __name__ == '__main__':
    main()
