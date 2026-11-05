#!/usr/bin/env python3
"""Collect one sustained synchronized RX workload with an external continuous fixture."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

PACKAGE_COMMIT = 'b9fcea82e0858d9d34694dc0ae8d46057a398104'
PACKAGE_CHECKSUMS_SHA256 = '60e19c4ebe5843d4961b32fa4ef8839025242a91baced823101ad19f34410610'
PRODUCER_SOURCE_SHA256 = '90e78f7161329821edbc808338bc652b6ddbd350e970ac52bc1ef381ca0e2f96'
GENERATOR_SOURCE_SHA256 = '6312e3daa82729d8bd74e64759d3a3c8f2038b6b28e4aba562d4262be0a76cac'
SOURCE_PINS = {
    'rade_c_commit': 'c8a3dc156045cae2cd251e1a4be0c304c9ddf2f9',
    'opus_commit': '940d4e5af64351ca8ba8390df3f555484c567fbb',
    'opus_model_sha256': '4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453',
    'speech_fixture_sha256': 'a53d3cc835a5668c4687dba80314b82d8a96d9e0431e5de5072fa397fc8c4a79',
    'libsamplerate_source_sha256': '3258da280511d24b49d6b08615bbe824d0cacc9842b0e4caf11c52cf2b043893',
}
SECONDS, WARMUP, WORKERS = 1800, 30, 3
CASES = {'smoke': (5, 3), 'endurance': (SECONDS, WARMUP)}
MIN_AVAILABLE_KIB = 600 * 1024
MAX_FIXTURE_BYTES = 32 * 1024 * 1024
MIN_FIXTURE_SAMPLES = 1840 * 8000
# Each duration is rounded up by at most one 140 ms input block.
MAX_EXPECTED_INPUT_SAMPLES = (SECONDS + WARMUP) * 8000 + 2 * 1120


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def load_package(root):
    """Check the exact previously tested checksum list before importing its helpers."""
    checksum_path = root / 'SHA256SUMS'
    if digest(checksum_path) != PACKAGE_CHECKSUMS_SHA256:
        raise ValueError('use the unchanged acquisition package from the successful b9fcea82 SDK build')
    checksums = dict((name, value) for value, name in
                     (line.split('  ', 1) for line in checksum_path.read_text().splitlines()))
    for name in ('run_pipeline_benchmarks.py', 'pipeline_evidence.py', 'acquisition_evidence.py'):
        relative = 'tools/freedv/' + name
        if digest(root / relative) != checksums.get(relative):
            raise ValueError('package helper checksum differs: ' + relative)
    sys.path.insert(0, str(root / 'tools/freedv'))
    package_tools = importlib.import_module('run_pipeline_benchmarks')
    evidence = importlib.import_module('acquisition_evidence')
    manifest, manifest_text, checksum_text = package_tools.verify_package(root)
    experiment = manifest.get('rade_acquisition_parallel_experiment', {})
    if (manifest.get('target') != 'x6200-buildroot' or
            manifest.get('firmware_commit') != PACKAGE_COMMIT or
            manifest.get('firmware_worktree_dirty') is not False or
            experiment.get('enabled') is not True or
            experiment.get('worker_counts') != [1, 2, 3, 4] or
            experiment.get('workers_include_calling_thread') is not True or
            experiment.get('acquisition_reuse_enabled') is not False):
        raise ValueError('package identity or acquisition experiment differs')
    return (manifest_text, checksum_text, evidence.validate_acquisition_result,
            importlib.import_module('pipeline_evidence').validate_pipeline_result)


def strict_json(text):
    def reject(value):
        raise ValueError('nonfinite JSON value: ' + value)
    def unique(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError('duplicate JSON key: ' + key)
            values[key] = value
        return values
    return json.loads(text, parse_constant=reject, object_pairs_hook=unique)


def integer(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(field + ': nonnegative integer required')
    return value


def sha_field(value, field):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError(field + ': SHA256 required')
    return value


def object_field(value, field):
    if type(value) is not dict:
        raise ValueError(field + ': JSON object required')
    return value


def require_steady_result(result, first_eoo_sample):
    """Require an uninterrupted measured population, without calling it a speed pass."""
    if not result.get('execution_complete'):
        return
    full, measured = result['full'], result['measured']
    frames = integer(measured['frames'], 'measured.frames')
    if (not frames or measured['sync_blocks'] != frames or measured['feature_blocks'] != frames or
            full['eoo_blocks'] != 0 or measured['eoo_blocks'] != 0):
        raise ValueError('completed long-sync run did not remain synchronized with features on every measured block and zero EOO')
    if full['audio_ns'] * 8000 >= first_eoo_sample * 1_000_000_000:
        raise ValueError('long-sync run reached the fixture ending')


def verify_fixture(raw, manifest_path, validate_native):
    """Read data and JSON only; no code from the external fixture is imported."""
    size = raw.stat().st_size
    if size % 2 or not MIN_FIXTURE_SAMPLES * 2 <= size < MAX_FIXTURE_BYTES:
        raise ValueError('fixture must be at least 1840 seconds of PCM16 and smaller than 32 MiB')
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError('fixture manifest is unexpectedly large')
    manifest_text = manifest_path.read_text()
    manifest = object_field(strict_json(manifest_text), 'manifest')
    if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1 or
            manifest.get('kind') != 'rade-v1-continuous-fixture' or
            manifest.get('revision') != 'rade-v1-long-sync-1' or
            manifest.get('radio_io') is not False or manifest.get('radio_qualified') is not False or
            manifest.get('cross_host_bit_reproducible') is not False):
        raise ValueError('unsupported external fixture manifest')
    fixture = object_field(manifest['fixture'], 'fixture')
    if (fixture.get('path') != 'fixture.raw' or raw.name != 'fixture.raw' or
            fixture.get('sample_format') != 'signed-16-bit-little-endian' or
            type(fixture.get('sample_rate_hz')) is not int or fixture['sample_rate_hz'] != 8000 or
            type(fixture.get('channels')) is not int or fixture['channels'] != 1):
        raise ValueError('fixture PCM format differs')
    for field in ('bytes', 'samples', 'first_eoo_sample', 'normal_modem_frames',
                  'modem_samples_per_frame', 'terminal_eoo_samples'):
        integer(fixture.get(field), 'fixture.' + field)
    if (fixture['bytes'] != size or fixture['samples'] * 2 != size or
            fixture['modem_samples_per_frame'] != 960 or fixture['terminal_eoo_samples'] != 1152 or
            fixture['normal_modem_frames'] != 15500 or
            fixture['first_eoo_sample'] != fixture['normal_modem_frames'] * 960 or
            fixture['samples'] != fixture['first_eoo_sample'] + fixture['terminal_eoo_samples'] or
            fixture['first_eoo_sample'] <= MAX_EXPECTED_INPUT_SAMPLES):
        raise ValueError('fixture frame counts or end-of-over boundary differ')
    raw_sha = digest(raw)
    if raw_sha != sha_field(fixture.get('sha256'), 'fixture.sha256'):
        raise ValueError('external fixture checksum mismatch')
    sources = object_field(manifest['sources'], 'sources')
    if any(sources.get(name) != value for name, value in SOURCE_PINS.items()):
        raise ValueError('external fixture source/model pins differ')
    generation = object_field(manifest['generation'], 'generation')
    sha_field(generation.get('producer_source_sha256'), 'generation.producer_source_sha256')
    if (generation.get('producer_source_sha256') != PRODUCER_SOURCE_SHA256 or
            generation.get('generator_source_sha256') != GENERATOR_SOURCE_SHA256 or
            generation.get('native_host_only') is not True or generation.get('one_transmitter_session') is not True or
            type(generation.get('intermediate_eoo_frames')) is not int or generation['intermediate_eoo_frames'] != 0 or
            type(generation.get('speech_seconds')) is not int or generation['speech_seconds'] != 1860 or
            type(generation.get('speech_rate_hz')) is not int or generation['speech_rate_hz'] != 16000 or
            type(generation.get('speech_samples')) is not int or generation['speech_samples'] != 29_760_000 or
            type(generation.get('code_commit')) is not str or
            not re.fullmatch('[0-9a-f]{40}', generation['code_commit']) or
            type(generation.get('compiler_version')) is not str or not generation['compiler_version']):
        raise ValueError('fixture producer identity or continuous generation contract differs')
    for field in ('repeated_speech_wav_sha256', 'generator_binary_sha256', 'generator_library_sha256',
                  'opus_config_sha256', 'host_package_manifest_sha256'):
        sha_field(generation.get(field), 'generation.' + field)
    native = object_field(manifest['native_validation'], 'native_validation')
    if (native.get('passed') is not True or native.get('radio_timing_qualified') is not False or
            type(native.get('seconds')) is not int or native['seconds'] != SECONDS or
            type(native.get('warmup_seconds')) is not int or native['warmup_seconds'] != WARMUP or
            native.get('execution') != 'serial' or native.get('pacing') != 'unpaced' or
            native.get('result_file') != 'native-validation.json'):
        raise ValueError('fixture native validation contract differs')
    native_path = manifest_path.parent / 'native-validation.json'
    if native_path.stat().st_size > 1024 * 1024:
        raise ValueError('native validation result is unexpectedly large')
    native_text = native_path.read_text()
    if digest(native_path) != sha_field(native.get('result_sha256'), 'native_validation.result_sha256'):
        raise ValueError('native validation result checksum differs')
    native_result = strict_json(native_text)
    validate_native(native_result, execution='serial', pacing='unpaced', workload='continuous',
                    seconds=SECONDS, warmup=WARMUP, fixture_name='rade-modem', require_complete=True)
    require_steady_result(native_result, fixture['first_eoo_sample'])
    for field, actual in [('measured_frames', native_result['measured']['frames']),
                          ('measured_sync_blocks', native_result['measured']['sync_blocks']),
                          ('measured_feature_blocks', native_result['measured']['feature_blocks']),
                          ('eoo_blocks', native_result['full']['eoo_blocks'])]:
        if integer(native.get(field), 'native_validation.' + field) != actual:
            raise ValueError('native validation summary differs from its result')
    return {'manifest': manifest, 'manifest_text': manifest_text, 'native_result_text': native_text,
            'fixture_sha256': raw_sha, 'manifest_sha256': digest(manifest_path),
            'native_result_sha256': digest(native_path), 'raw_bytes': size}


def memory_snapshot(text=None):
    if text is None:
        text = Path('/proc/meminfo').read_text()
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r'(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+) kB', line.strip())
        if match:
            values[match[1]] = int(match[2])
    if 'MemAvailable' not in values:
        raise ValueError('MemAvailable is unavailable; cannot check fixture preparation headroom')
    return {'utc': datetime.now(timezone.utc).isoformat(), 'memory_kib': values,
            'minimum_available_kib': MIN_AVAILABLE_KIB,
            'sufficient': values['MemAvailable'] >= MIN_AVAILABLE_KIB}


def timing_observation(result, validated, case_name='endurance'):
    observed = {'status': 'INCONCLUSIVE', 'scope': 'three-worker synchronized RX ' + case_name,
                'original_serial_70_percent_gate': 'unchanged',
                'note': 'Pipeline throughput, whole-path latency and resource stability require separate review.'}
    if not validated or not result.get('execution_complete'):
        return observed
    timing, queue = result['timing'], result['queue']
    observed.update(status='RECORDED',
                    p99_full_path_input_ratio=timing['full_path_input_ratio']['p99'],
                    full_path_ms=timing['full_path_ms'],
                    nominal_release_to_completion_ms=timing['nominal_release_to_completion_ms'],
                    output_completion_period_ms=timing['output_completion_period_ms'],
                    ingress_late_more_than_one_interval=timing['ingress_late_more_than_one_interval'],
                    maximum_virtual_input_backlog_ns_including_warmup=timing['maximum_virtual_input_backlog_ns_including_warmup'],
                    queue_max_depth=queue['max_depth'], queue_full_events=queue['full_events'],
                    dropped_blocks=queue['dropped_blocks'], abandoned_blocks=queue['abandoned_blocks'])
    return observed


def collect(root, raw, output, manifest_text, checksum_text, fixture, validate, case_name='endurance'):
    seconds, warmup = CASES[case_name]
    output.mkdir(parents=True, exist_ok=False)
    (output / 'package-manifest.json').write_text(manifest_text)
    (output / 'package-SHA256SUMS').write_text(checksum_text)
    (output / 'fixture-manifest.json').write_text(fixture['manifest_text'])
    (output / 'fixture-native-validation.json').write_text(fixture['native_result_text'])
    summary = {'schema_version': 1, 'experiment': 'rade-long-sync', 'case': case_name,
               'seconds_requested': seconds, 'warmup_seconds_requested': warmup, 'acquisition_workers': WORKERS,
               'runner_sha256': digest(Path(__file__)), 'package_checksums_verified_before_run': True,
               'fixture_sha256': fixture['fixture_sha256'], 'fixture_manifest_sha256': fixture['manifest_sha256'],
               'fixture_native_result_sha256': fixture['native_result_sha256'],
               'external_fixture_bytes': fixture['raw_bytes'], 'fixture_copied_to_results': False,
               'radio_gui_process_checked_before_run': True, 'automatic_upload': False, 'radio_io': False,
               'execution_validated': False, 'execution_complete': False,
               'capacity_qualification': 'INCONCLUSIVE', 'hardware_qualification': 'INCONCLUSIVE',
               'original_70_percent_assessment': 'unchanged_serial_reference_required',
               'memory_interpretation': 'The unchanged benchmark converts the entire fixture to retained 44.1 kHz float samples (about 313 MiB); preparation uses additional copies, and /tmp raw PCM consumes RAM separately. Timing records also grow resident as they are touched. Peak RSS or growth is not isolated codec memory use.',
               'limitation': 'Synchronized offline RX only; no interrupted/off-air signal, microphone/speaker, PTT or RF qualification. Frequency/cooling observations do not replace missing temperature measurements.'}
    result = {}
    case = output / ('sync-acq-3-%ds' % seconds)
    try:
        summary['initial_memory_preflight'] = memory_snapshot()
        if not summary['initial_memory_preflight']['sufficient']:
            raise ValueError('need at least 600 MiB MemAvailable after fixture transfer; benchmark was not started')
        subprocess.run([sys.executable, str(root / 'tools/freedv/inventory.py'),
                        '--output', str(output / 'inventory.json')], check=True)
        summary['launch_memory_preflight'] = memory_snapshot()
        if not summary['launch_memory_preflight']['sufficient']:
            raise ValueError('MemAvailable fell below 600 MiB before launch; benchmark was not started')
        command = [sys.executable, str(root / 'tools/freedv/collect_benchmark.py'),
                   '--mode', 'RADE-V1', '--direction', 'rx', '--execution', 'parallel',
                   '--acquisition-workers', str(WORKERS), '--benchmark', str(root / 'bin/rade-pipeline-bench'),
                   '--fixture', str(raw), '--seconds', str(seconds), '--pacing', 'paced',
                   '--warmup', str(warmup), '--workload', 'continuous', '--sample-interval', '1',
                   '--output', str(case)]
        print('Preparing the long waveform, then running', warmup, 'seconds of warm-up and',
              seconds, 'seconds of synchronized RX (' + case_name + ').', flush=True)
        print('Waveform preparation can take a few minutes even for the short smoke case.', flush=True)
        done = subprocess.run(command)
        summary['collector_return_code'] = done.returncode
        result = strict_json((case / 'result.json').read_text())
        assessment = validate(result, workers=WORKERS, execution='parallel', pacing='paced',
                              workload='continuous', seconds=seconds, warmup=warmup,
                              fixture_name='rade-modem', require_complete=False)
        if (done.returncode == 0) != result['execution_complete']:
            raise ValueError('collector exit status disagrees with benchmark completeness')
        require_steady_result(result, fixture['manifest']['fixture']['first_eoo_sample'])
        summary['execution_validated'] = assessment['execution_validated']
        summary['execution_complete'] = result['execution_complete']
        summary['failure_reason'] = result['failure_reason']
        summary['fixture_preparation_ms'] = result['fixture_preparation_ms']
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        summary['error'] = str(exc)
    finally:
        summary['timing_observation'] = timing_observation(result, summary['execution_validated'], case_name)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Results retained in', output, '; overall capacity and hardware qualification: INCONCLUSIVE', flush=True)
    return 0 if summary['execution_validated'] and summary['execution_complete'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--fixture-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=CASES, default='endurance')
    args = parser.parse_args()
    root, output = args.package.resolve(), args.output.resolve()
    if output == root or root in output.parents:
        parser.error('store results outside the package')
    if not platform.machine().startswith('arm'):
        parser.error('run this collector on the ARM radio')
    try:
        manifest, checksums, validate, validate_native = load_package(root)
        fixture = verify_fixture(args.fixture.resolve(), args.fixture_manifest.resolve(), validate_native)
        subprocess.run(['pidof', 'x6100_gui'], check=True, stdout=subprocess.DEVNULL)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    raise SystemExit(collect(root, args.fixture.resolve(), output, manifest, checksums, fixture, validate, args.case))


if __name__ == '__main__':
    main()

