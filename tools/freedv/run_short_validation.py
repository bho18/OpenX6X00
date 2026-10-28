#!/usr/bin/env python3
"""Five short file-only workloads on the previously tested X6200 SDK package."""
import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

PACKAGE_COMMIT = 'b9fcea82e0858d9d34694dc0ae8d46057a398104'
PACKAGE_CHECKSUMS = '60e19c4ebe5843d4961b32fa4ef8839025242a91baced823101ad19f34410610'
SECONDS, WARMUP = 120, 5
CASES = [('rade-tx', 'RADE-V1', 'tx', 'rade-speech'),
         ('700e-tx', '700E', 'tx', '700e-speech'),
         ('700e-rx', '700E', 'rx', '700e-modem'),
         ('700e-no-sync', '700E', 'rx', 'silence'),
         ('rade-noise', 'RADE-V1', 'rx', 'receiver-noise')]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json(text):
    def reject(value):
        raise ValueError('nonfinite JSON value: ' + value)
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate JSON key: ' + key)
            out[key] = value
        return out
    return json.loads(text, parse_constant=reject, object_pairs_hook=unique)


def noise_pcm():
    """Two seconds, 8kHz PCM16: fixed LCG, two low-pass stages and DC removal.

    This is a deterministic synthetic acquisition input, not calibrated RF noise.
    Integer arithmetic defines identical bytes on the host and ARM/Python 3.11.
    """
    seed, low1, low2, dc = 0x6200, 0, 0, 0
    out = bytearray()
    for _ in range(16000):
        seed = (1664525 * seed + 1013904223) & 0xffffffff
        sample = (seed >> 18) - 8192
        low1 += (sample - low1) // 2
        low2 += (low1 - low2) // 2
        dc += (low2 - dc) // 64
        out.extend(struct.pack('<h', low2 - dc))
    return bytes(out)


def load_package(root):
    # Authenticate the helper files before importing any package code. Then
    # validate the complete package, including libraries and executable hashes.
    if digest(root / 'SHA256SUMS') != PACKAGE_CHECKSUMS:
        raise ValueError('requires the unchanged b9fcea82 acquisition SDK package')
    sums = dict((name, value) for value, name in
                (line.split('  ', 1) for line in (root / 'SHA256SUMS').read_text().splitlines()))
    for name in ('run_pipeline_benchmarks.py', 'pipeline_evidence.py', 'acquisition_evidence.py'):
        relative = 'tools/freedv/' + name
        if digest(root / relative) != sums.get(relative):
            raise ValueError('package helper checksum mismatch: ' + relative)
    sys.path.insert(0, str(root / 'tools/freedv'))
    manifest, text, checksums = importlib.import_module('run_pipeline_benchmarks').verify_package(root)
    if (manifest.get('firmware_commit') != PACKAGE_COMMIT or
            manifest.get('target') != 'x6200-buildroot' or
            manifest.get('firmware_worktree_dirty') is not False or
            manifest.get('rade_acquisition_parallel_experiment', {}).get('enabled') is not True):
        raise ValueError('unexpected SDK package identity')
    return text, checksums, importlib.import_module('acquisition_evidence').validate_acquisition_result


def preflight(output):
    subprocess.run(['pidof', 'x6100_gui'], check=True, stdout=subprocess.DEVNULL)
    memory = dict((line.split(':')[0], line.split(':')[1].strip())
                  for line in Path('/proc/meminfo').read_text().splitlines())
    if int(memory['MemAvailable'].split()[0]) < 64 * 1024:
        raise ValueError('need at least 64 MiB MemAvailable for short fixtures')
    if shutil.disk_usage(output).free < 16 * 1024 * 1024:
        raise ValueError('need at least 16 MiB free for results')


def assess(result, mode, direction, fixture):
    if (result.get('mode'), result.get('direction'), result.get('pacing')) != (mode, direction, 'paced'):
        raise ValueError('result identity differs')
    if result.get('radio_io') is not False or result.get('full_pipeline_qualified') is not False:
        raise ValueError('unexpected hardware qualification claim')
    if type(result.get('frames')) is not int or result['frames'] <= 0:
        raise ValueError('no measured frames')
    if result.get('resampling_included') is not True:
        raise ValueError('resampling was not included')
    if direction == 'rx':
        sync = result.get('sync_frames')
        if type(sync) is not int or (fixture == 'silence' and sync != 0) or (fixture != 'silence' and sync <= 0):
            raise ValueError('unexpected synchronization')
    if fixture != 'silence' and result.get('resampled_output_samples', 0) <= 0:
        raise ValueError('expected encoded/decoded output is missing')
    ratio, misses = result.get('p99_frame_ratio'), result.get('deadline_misses')
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or ratio < 0 or type(misses) is not int or misses < 0:
        raise ValueError('missing or invalid timing')
    duration = result.get('audio_seconds')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not SECONDS <= duration <= SECONDS + 1:
        raise ValueError('measurement duration incomplete')
    warmup = result.get('warmup_audio_seconds')
    if type(warmup) not in (int, float) or not math.isfinite(warmup) or not WARMUP <= warmup <= WARMUP + 1:
        raise ValueError('warm-up incomplete')
    return {'p99_ratio': ratio, 'original_70_percent_margin': 'PASS' if ratio <= .70 else 'FAIL',
            'deadline_misses': misses, 'full_qualification': False}


def command(root, output, mode, direction, fixture):
    noise = fixture == 'receiver-noise'
    exe = 'rade-pipeline-bench' if noise else ('rade-frame-bench' if mode == 'RADE-V1' else 'codec2-frame-bench')
    path = output / 'receiver-noise.raw' if noise else root / 'fixtures' / (fixture + '.raw')
    cmd = [sys.executable, str(root / 'tools/freedv/collect_benchmark.py'), '--mode', mode,
           '--direction', direction, '--benchmark', str(root / 'bin' / exe), '--fixture', str(path),
           '--seconds', str(SECONDS), '--warmup', str(WARMUP), '--pacing', 'paced',
           '--workload', 'continuous', '--sample-interval', '1']
    if noise:
        cmd += ['--execution', 'parallel', '--acquisition-workers', '3']
    return cmd


def save(output, summary):
    temporary = output / 'summary.tmp'
    temporary.write_text(json.dumps(summary, indent=2) + '\n')
    temporary.replace(output / 'summary.json')


def run_suite(root, output, validate):
    summary = {'schema_version': 1, 'experiment': 'short-remaining-codec-validation',
               'seconds_per_case': SECONDS, 'warmup_seconds': WARMUP, 'cases': [],
               'runner_sha256': digest(Path(__file__)), 'radio_io': False,
               'hardware_qualification': 'INCONCLUSIVE', 'automatic_upload': False,
               'complete': False, 'coverage_complete': False,
               'operator_context': 'Confirm normal GUI/waterfall responsive and no other digital application.'}
    try:
        for name, mode, direction, fixture in CASES:
            preflight(output)
            entry = {'case': name, 'execution_validated': False, 'return_code': None}
            summary['cases'].append(entry)
            save(output, summary)
            dest = output / name
            cmd = command(root, output, mode, direction, fixture) + ['--output', str(dest)]
            entry['command'] = cmd
            print('Running', name, ':', SECONDS, 'measured seconds after', WARMUP, 'seconds warm-up', flush=True)
            try:
                entry['return_code'] = subprocess.run(cmd).returncode
                if entry['return_code']:
                    raise ValueError('collector failed; individual case evidence retained')
                result = strict_json((dest / 'result.json').read_text())
                if fixture == 'receiver-noise':
                    # The original validator's silence label means expected
                    # no sync/no speech; actual input identity remains noise in
                    # the case name, command, fixture hash and collection manifest.
                    validate(result, workers=3, execution='parallel', pacing='paced', workload='continuous',
                             seconds=SECONDS, warmup=WARMUP, fixture_name='silence', require_complete=True)
                    t = result['timing']
                    entry['timing'] = {'original_70_percent_margin': 'PASS' if t['full_path_input_ratio']['p99'] <= .70 else 'FAIL',
                                      'input_late_intervals': t['ingress_late_more_than_one_interval'],
                                      'p99_ratio': t['full_path_input_ratio']['p99'], 'queue': result['queue']}
                else:
                    entry['timing'] = assess(result, mode, direction, fixture)
                entry['execution_validated'] = True
            except (ValueError, KeyError, TypeError, OSError) as exc:
                entry['error'] = str(exc)
                print(name, 'FAILED:', exc, flush=True)
            save(output, summary)
        summary['coverage_complete'] = len(summary['cases']) == len(CASES)
        summary['complete'] = summary['coverage_complete'] and all(c['execution_validated'] for c in summary['cases'])
    except BaseException as exc:
        summary['aborted'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        save(output, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root, output = args.package.resolve(), args.output.resolve()
    if not platform.machine().startswith('arm'):
        parser.error('run on the X6200; host tests do not qualify radio timing')
    if output == root or root in output.parents:
        parser.error('results must be outside the verified package')
    text, checksums, validate = load_package(root)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'package-manifest.json').write_text(text)
    (output / 'package-SHA256SUMS').write_text(checksums)
    payload = noise_pcm()
    (output / 'receiver-noise.raw').write_bytes(payload)
    (output / 'noise-manifest.json').write_text(json.dumps({
        'schema_version': 1, 'revision': 'synthetic-filtered-noise-1', 'seed': 0x6200,
        'sample_rate_hz': 8000, 'channels': 1, 'format': 's16le', 'bytes': len(payload),
        'sha256': hashlib.sha256(payload).hexdigest(), 'rf_calibrated': False}, indent=2)+'\n')
    subprocess.run([sys.executable, str(root / 'tools/freedv/inventory.py'), '--output', str(output / 'inventory.json')], check=True)
    summary = run_suite(root, output, validate)
    print('Saved', output, '; execution complete:', summary['complete'], '; physical audio remains untested.')
    return 0 if summary['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
