#!/usr/bin/env python3
"""Run fixed three-worker follow-up cases using the already tested SDK package."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import platform
import subprocess
import sys

PACKAGE_COMMIT = 'b9fcea82e0858d9d34694dc0ae8d46057a398104'
PACKAGE_CHECKSUMS_SHA256 = '60e19c4ebe5843d4961b32fa4ef8839025242a91baced823101ad19f34410610'
WORKERS = 3
CASES = {'no-sync-endurance': ('silence', 1800, 30),
         'steady-sync': ('rade-modem', 5, 3)}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    return manifest_text, checksum_text, evidence.validate_acquisition_result


def timing_observations(result, validated, case_name):
    """A single workload screen, never hardware or whole-decoder qualification."""
    observation = {'scope': case_name, 'status': 'INCONCLUSIVE'}
    if not validated or not result.get('execution_complete'):
        return observation
    timing, queue = result['timing'], result['queue']
    observation.update(p99_full_path_input_ratio=timing['full_path_input_ratio']['p99'],
                       ingress_late_more_than_one_interval=timing['ingress_late_more_than_one_interval'],
                       fullpath_one_input_interval_misses=timing['fullpath_one_input_interval_misses'],
                       nominal_release_one_interval_misses=timing['nominal_release_one_interval_misses'],
                       dropped_blocks=queue['dropped_blocks'], abandoned_blocks=queue['abandoned_blocks'],
                       queue_full_events=queue['full_events'])
    if case_name == 'steady-sync':
        observation.update(status='RECORDED',
                           note='Short steady-input control only; full-path latency is not pipeline throughput.')
        return observation
    observation['p99_full_path_input_ratio_limit'] = 0.70
    met = observation['p99_full_path_input_ratio'] <= 0.70 and all(
        observation[name] == 0 for name in ('ingress_late_more_than_one_interval',
        'fullpath_one_input_interval_misses', 'nominal_release_one_interval_misses',
        'dropped_blocks', 'abandoned_blocks', 'queue_full_events'))
    observation['status'] = 'MET' if met else 'NOT_MET'
    return observation


def collect(root, output, manifest_text, checksum_text, validate, case_name):
    fixture, seconds, warmup = CASES[case_name]
    output.mkdir(parents=True, exist_ok=False)
    (output / 'package-manifest.json').write_text(manifest_text)
    (output / 'package-SHA256SUMS').write_text(checksum_text)
    summary = {'schema_version': 1, 'experiment': 'rade-acquisition-endurance',
               'case': case_name, 'fixture': fixture, 'seconds_requested': seconds,
               'warmup_seconds_requested': warmup, 'acquisition_workers': WORKERS,
               'runner_sha256': digest(Path(__file__)), 'package_checksums_verified_before_run': True,
               'radio_gui_process_checked_before_run': True, 'automatic_upload': False,
               'radio_io': False, 'execution_validated': False, 'execution_complete': False,
               'capacity_qualification': 'INCONCLUSIVE', 'hardware_qualification': 'INCONCLUSIVE',
               'original_70_percent_assessment': 'unchanged_serial_reference_required',
               'memory_interpretation': 'Benchmark timing records are preallocated and touched throughout the run; RSS growth alone is not proof of a codec leak. Review resource trends separately.',
               'limitation': 'One fixed workload only; resource trends and operator observations require review. No complete sustained decoder, off-air noise, audio-device underrun or RF qualification.'}
    result = {}
    case = output / ('%s-acq-3-%ds' % (fixture, seconds))
    try:
        subprocess.run([sys.executable, str(root / 'tools/freedv/inventory.py'),
                        '--output', str(output / 'inventory.json')], check=True)
        command = [sys.executable, str(root / 'tools/freedv/collect_benchmark.py'),
                   '--mode', 'RADE-V1', '--direction', 'rx', '--execution', 'parallel',
                   '--acquisition-workers', str(WORKERS), '--benchmark', str(root / 'bin/rade-pipeline-bench'),
                   '--fixture', str(root / 'fixtures' / (fixture + '.raw')), '--seconds', str(seconds),
                   '--pacing', 'paced', '--warmup', str(warmup), '--workload', 'continuous',
                   '--sample-interval', '1', '--output', str(case)]
        print('Running', case_name, 'with 3 workers:', seconds, 'seconds after', warmup,
              'seconds of warm-up.', flush=True)
        done = subprocess.run(command)
        summary['collector_return_code'] = done.returncode
        def reject_nonfinite(value):
            raise ValueError('nonfinite JSON token: ' + value)
        result = json.loads((case / 'result.json').read_text(), parse_constant=reject_nonfinite)
        assessment = validate(result, workers=WORKERS, execution='parallel', pacing='paced',
                              workload='continuous', seconds=seconds, warmup=warmup,
                              fixture_name=fixture, require_complete=False)
        if (done.returncode == 0) != result['execution_complete']:
            raise ValueError('collector exit status disagrees with benchmark completeness')
        if case_name == 'steady-sync' and (result['full']['eoo_blocks'] or
                                          result['full']['audio_ns'] >= 9_984_000_000):
            raise ValueError('steady-input control unexpectedly reached the fixture ending')
        summary['execution_validated'] = assessment['execution_validated']
        summary['execution_complete'] = result['execution_complete']
        summary['failure_reason'] = result['failure_reason']
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        summary['error'] = str(exc)
    finally:
        summary['timing_observation'] = timing_observations(result, summary['execution_validated'], case_name)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Results retained in', output, flush=True)
    print('Timing observation:', summary['timing_observation']['status'],
          '; overall capacity and hardware qualification: INCONCLUSIVE', flush=True)
    return 0 if summary['execution_validated'] and summary['execution_complete'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=CASES, default='no-sync-endurance')
    args = parser.parse_args()
    root, output = args.package.resolve(), args.output.resolve()
    if output == root or root in output.parents:
        parser.error('store results outside the package')
    if not platform.machine().startswith('arm'):
        parser.error('run this test on the ARM radio')
    try:
        manifest, checksums, validate = load_package(root)
        subprocess.run(['pidof', 'x6100_gui'], check=True, stdout=subprocess.DEVNULL)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    raise SystemExit(collect(root, output, manifest, checksums, validate, args.case))


if __name__ == '__main__':
    main()
