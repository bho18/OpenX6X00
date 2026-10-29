#!/usr/bin/env python3
"""Compare 1..4 acquisition participants in the offline two-stage RX pipeline."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

from run_pipeline_benchmarks import verify_package
from pipeline_evidence import strict_json
from acquisition_evidence import validate_acquisition_result, compare_acquisition_results

# Fixed public cases provide multiple acquisition/recovery opportunities. They
# remain short characterization runs, never the 30-minute acceptance gate.
CASES = [('sync', 'rade-modem', 10, 'continuous'),
         ('silence', 'silence', 10, 'continuous'),
         ('interrupted', 'rade-modem', 26, 'interrupt')]
WARMUP = 3


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--radio', action='store_true', help='require SDK package, ARM host and running GUI')
    a = p.parse_args()
    root = a.package.resolve()
    manifest, manifest_text, checksum_text = verify_package(root)
    experiment = manifest.get('rade_acquisition_parallel_experiment', {})
    if (experiment.get('enabled') is not True or experiment.get('worker_counts') != [1, 2, 3, 4] or
            experiment.get('workers_include_calling_thread') is not True or
            experiment.get('acquisition_reuse_enabled') is not False):
        p.error('package does not identify the isolated parallel acquisition experiment')
    verified = {line.split('  ', 1)[1] for line in checksum_text.splitlines()}
    if not {'tools/freedv/run_acquisition_benchmarks.py', 'tools/freedv/acquisition_evidence.py'}.issubset(verified):
        p.error('package checksums omit acquisition collection helpers')
    if a.radio:
        if manifest.get('target') != 'x6200-buildroot' or not platform.machine().startswith('arm'):
            p.error('--radio requires matching SDK package on the ARM radio')
        subprocess.run(['pidof', 'x6100_gui'], check=True, stdout=subprocess.DEVNULL)
    output = a.output.resolve()
    if output == root or root in output.parents:
        p.error('store results outside the package')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'package-manifest.json').write_text(manifest_text)
    (output / 'package-SHA256SUMS').write_text(checksum_text)
    subprocess.run([sys.executable, str(root / 'tools/freedv/inventory.py'),
                    '--output', str(output / 'inventory.json')], check=True)
    cases, comparisons, values = [], [], {}
    try:
        for label, fixture, seconds, workload in CASES:
            values[label] = {}
            for workers in range(1, 5):
                name = '%02d-%s-acq-%d' % (len(cases) + 1, label, workers)
                print('Running', name, flush=True)
                entry = {'case': name, 'acquisition_workers': workers, 'execution_validated': False}
                cases.append(entry)
                command = [sys.executable, str(root / 'tools/freedv/collect_benchmark.py'),
                           '--mode', 'RADE-V1', '--direction', 'rx', '--execution', 'parallel',
                           '--acquisition-workers', str(workers),
                           '--benchmark', str(root / 'bin/rade-pipeline-bench'),
                           '--fixture', str(root / 'fixtures' / (fixture + '.raw')),
                           '--seconds', str(seconds), '--pacing', 'paced', '--warmup', str(WARMUP),
                           '--workload', workload, '--sample-interval', '1', '--output', str(output / name)]
                done = subprocess.run(command)
                entry['return_code'] = done.returncode
                try:
                    result = strict_json((output / name / 'result.json').read_text())
                    assessment = validate_acquisition_result(result, workers=workers, seconds=seconds,
                                                            warmup=WARMUP, fixture_name=fixture,
                                                            workload=workload, pacing='paced',
                                                            execution='parallel', require_complete=False)
                    if (done.returncode == 0) != result['execution_complete']:
                        raise ValueError('exit status disagrees with execution completeness')
                    entry.update(assessment)
                    entry['failure_reason'] = result['failure_reason']
                    entry['capacity_observations'] = {'timing': result['timing'], 'queue': result['queue'],
                                                      'acquisition_workers': result['acquisition_workers']}
                    values[label][workers] = result
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    entry['error'] = str(exc)
                # Keep failures and continue all counts; never slow the source
                # clock or silently accept a lower effective worker count.
            for workers in range(2, 5):
                if 1 in values[label] and workers in values[label]:
                    comparison = compare_acquisition_results(values[label][1], values[label][workers])
                else:
                    comparison = {'status': 'INCONCLUSIVE', 'reason': 'Missing valid result.'}
                comparisons.append({'workload': label, 'reference_workers': 1,
                                    'candidate_workers': workers, **comparison})
    finally:
        complete = len(cases) == 12 and all(c.get('return_code') == 0 and c['execution_validated'] for c in cases)
        equal = len(comparisons) == 9 and all(c['status'] == 'MATCH' for c in comparisons)
        summary = {'schema_version': 1, 'experiment': 'rade-acquisition-workers',
                   'suite': 'paced-worker-sweep', 'acquisition_worker_counts': [1, 2, 3, 4],
                   'worker_count_includes_caller': True, 'fargan_uses_separate_thread': True,
                   'cases': cases, 'comparisons': comparisons, 'execution_complete': complete,
                   'functional_regression_evidence_matches': equal,
                   'capacity_qualification': 'INCONCLUSIVE', 'hardware_qualification': 'INCONCLUSIVE',
                   'original_70_percent_assessment': 'unchanged_serial_reference_required',
                   'package_checksums_verified_before_run': True,
                   'radio_gui_process_checked': a.radio, 'automatic_upload': False,
                   'reason': 'Short offline characterization; throughput, latency and sustained hardware qualification remain separate.'}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Results saved locally to', output)
    print('Execution complete:', complete, '; regression evidence matches:', equal,
          '; capacity and hardware qualification: INCONCLUSIVE')
    if not complete or not equal:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
