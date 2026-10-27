#!/usr/bin/env python3
"""Run an in-memory benchmark and collect local resource evidence; never controls radio hardware."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'tools/freedv/sources.json'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_value(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def sample_resources(pid, elapsed):
    proc = Path('/proc') / str(pid)
    status = read_value(proc / 'status')
    values = {}
    if status:
        keys = {'VmRSS', 'VmHWM', 'VmSize', 'Threads', 'voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches'}
        for line in status.splitlines():
            key, separator, value = line.partition(':')
            if separator and key in keys:
                values[key] = value.strip()
    policies = sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy[0-9]*'))
    thermals = sorted(Path('/sys/class/thermal').glob('thermal_zone[0-9]*'))
    cooling = sorted(Path('/sys/class/thermal').glob('cooling_device[0-9]*'))
    return {
        'utc': utc_now(), 'elapsed_seconds': round(elapsed, 6), 'process_status': values,
        'cpu_policies': [{'name': p.name, 'current_khz': read_value(p / 'scaling_cur_freq'),
                          'governor': read_value(p / 'scaling_governor')} for p in policies],
        'thermal_zones': [{'name': p.name, 'type': read_value(p / 'type'), 'temperature_millidegrees_c': read_value(p / 'temp')} for p in thermals],
        'cooling_devices': [{'name': p.name, 'type': read_value(p / 'type'), 'state': read_value(p / 'cur_state')} for p in cooling],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('700E', 'RADE-V1'))
    parser.add_argument('--benchmark', required=True, type=Path, help='prebuilt matching in-memory benchmark')
    parser.add_argument('--fixture', required=True, type=Path, help='raw PCM fixture; not copied into the result')
    parser.add_argument('--direction', required=True, choices=('tx', 'rx'))
    parser.add_argument('--execution', choices=('serial', 'parallel'), help='optional experimental RADE RX pipeline execution')
    parser.add_argument('--seconds', required=True, type=int, choices=range(1, 1801), metavar='1..1800')
    parser.add_argument('--pacing', required=True, choices=('paced', 'unpaced'))
    parser.add_argument('--warmup', type=int, choices=range(61), default=5, metavar='0..60')
    parser.add_argument('--workload', choices=('continuous', 'interrupt'), default='continuous')
    parser.add_argument('--sample-interval', type=float, default=10.0, help='resource sample interval in seconds (1..60)')
    parser.add_argument('--model', type=Path, action='append', default=[], help='optional model artifact to hash; repeatable')
    parser.add_argument('--output', required=True, type=Path, help='new local result directory')
    args = parser.parse_args()
    if not 1 <= args.sample_interval <= 60:
        parser.error('sample interval must be 1..60 seconds')
    if args.workload == 'interrupt' and (args.mode != 'RADE-V1' or args.direction != 'rx'):
        parser.error('interrupt is only available for the RADE V1 RX prototype')
    if args.execution and (args.mode != 'RADE-V1' or args.direction != 'rx'):
        parser.error('--execution is only available for the experimental RADE V1 RX pipeline')
    exe, fixture = (p.resolve(strict=True) for p in (args.benchmark, args.fixture))
    models = [p.resolve(strict=True) for p in args.model]
    for path in (exe, fixture, *models, SOURCES):
        if not path.is_file():
            parser.error(f'not a regular file: {path}')
    if not os.access(exe, os.X_OK):
        parser.error('benchmark is not executable')
    output = args.output.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error('output directory already exists; choose a new one')
    common = [str(exe), args.execution or args.direction, str(fixture), str(args.seconds), args.pacing]
    command = common + (['resample', str(args.warmup)] if args.mode == '700E'
                        else [str(args.warmup), args.workload])
    metadata = {
        'schema_version': 1, 'kind': 'codec-benchmark-collection', 'created_utc': utc_now(),
        'mode': args.mode, 'direction': args.direction, 'seconds_requested': args.seconds,
        'warmup_seconds_requested': args.warmup, 'pacing': args.pacing, 'workload': args.workload,
        'benchmark_sha256': digest(exe), 'fixture_sha256': digest(fixture),
        'sources_manifest_sha256': digest(SOURCES), 'pinned_sources': json.loads(SOURCES.read_text()),
        'model_artifacts': [{'name': p.name, 'sha256': digest(p)} for p in models],
        'sample_interval_seconds': args.sample_interval, 'kernel': platform.release(), 'machine': platform.machine(),
        'resource_units': {'rss': 'Linux /proc status values in KiB', 'cpu_frequency': 'kHz', 'temperature': 'millidegrees C'},
        'resource_samples_are_observations_not_thermal_proof': True,
        'queue_and_underrun_counters_available': False, 'radio_control': False,
        'hardware_routing_tested': False, 'controlled_rf_interoperability_tested': False,
        'recordings_included': False, 'automatic_upload': False,
    }
    if args.execution:
        metadata.update(experiment='rade-rx-two-worker', execution=args.execution,
                        experimental_queue_counters_available=True,
                        hardware_audio_underrun_counters_available=False,
                        original_70_percent_assessment='unchanged_serial_reference_required')
    (output / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    started_utc = utc_now()
    start = time.monotonic()
    interrupted = None
    with (output / 'benchmark.stdout').open('w') as stdout, (output / 'benchmark.stderr').open('w') as stderr, \
         (output / 'resources.jsonl').open('w') as samples:
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        try:
            while proc.poll() is None:
                samples.write(json.dumps(sample_resources(proc.pid, time.monotonic() - start)) + '\n')
                samples.flush()
                try:
                    proc.wait(timeout=args.sample_interval)
                except subprocess.TimeoutExpired:
                    pass
        except BaseException as exc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            interrupted = exc
    ended_utc = utc_now()
    events = [{'event': 'benchmark_started', 'utc': started_utc},
              {'event': 'benchmark_exited', 'utc': ended_utc, 'return_code': proc.returncode,
               'elapsed_seconds': round(time.monotonic() - start, 6)}]
    (output / 'events.json').write_text(json.dumps(events, indent=2) + '\n')
    if interrupted is not None:
        raise interrupted
    if proc.returncode and not args.execution:
        raise SystemExit(f'benchmark failed with exit code {proc.returncode}; see {output}/benchmark.stderr')
    try:
        result = json.loads((output / 'benchmark.stdout').read_text())
    except (ValueError, OSError) as exc:
        raise SystemExit(f'invalid benchmark output in {output}: {exc}') from exc
    if args.execution:
        identity = (result.get('experiment'), result.get('execution'), result.get('pacing'), result.get('workload'))
        if identity != ('rade-rx-two-worker', args.execution, args.pacing, args.workload):
            raise SystemExit(f'pipeline output does not match requested execution/pacing/workload; see {output}')
    elif (result.get('mode'), result.get('direction'), result.get('pacing')) != (args.mode, args.direction, args.pacing):
        raise SystemExit(f'benchmark output does not match requested mode/direction/pacing; see {output}')
    if result.get('radio_io') is not False or result.get('full_pipeline_qualified') is not False:
        raise SystemExit(f'benchmark did not identify itself as a non-qualified, no-radio prototype; see {output}')
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    if proc.returncode:
        raise SystemExit(f'pipeline benchmark failed with exit code {proc.returncode}; structured failure retained in {output}/result.json')
    print(f'Collected local benchmark evidence in {output}; this does not qualify radio performance.')


if __name__ == '__main__':
    main()
