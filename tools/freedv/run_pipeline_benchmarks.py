#!/usr/bin/env python3
"""Collect six paired experimental RADE RX cases; never access radio control or audio devices."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

from pipeline_evidence import strict_json, validate_pipeline_result, compare_pipeline_pair


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_package(root):
    checksum_text = (root / 'SHA256SUMS').read_text()
    verified = set()
    for line in checksum_text.splitlines():
        expected, filename = line.split('  ', 1)
        path = (root / filename).resolve()
        if (not re.fullmatch('[0-9a-f]{64}', expected) or filename in verified or
                root not in path.parents or not path.is_file() or digest(path) != expected):
            raise ValueError('package verification failed: ' + filename)
        verified.add(filename)
    required = {'package-manifest.json', 'bin/rade-pipeline-bench', 'fixtures/rade-modem.raw',
                'fixtures/silence.raw', 'tools/freedv/collect_benchmark.py', 'tools/freedv/sources.json',
                'tools/freedv/inventory.py', 'tools/freedv/pipeline_evidence.py',
                'tools/freedv/run_pipeline_benchmarks.py'}
    required.update(str(p.relative_to(root)) for p in (root / 'lib').iterdir() if p.is_file())
    if not required.issubset(verified):
        raise ValueError('package checksum list omits required experiment files')
    manifest_text = (root / 'package-manifest.json').read_text()
    manifest = strict_json(manifest_text)
    if manifest.get('rade_pipeline_experiment', {}).get('enabled') is not True:
        raise ValueError('package does not identify the optional pipeline experiment')
    return manifest, manifest_text, checksum_text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--output', type=Path, required=True, help='new local directory outside the package')
    p.add_argument('--radio', action='store_true', help='require matching SDK package, ARM host and active GUI')
    a = p.parse_args()
    root = a.package.resolve()
    manifest, manifest_text, checksum_text = verify_package(root)
    if a.radio:
        if manifest.get('target') != 'x6200-buildroot' or not platform.machine().startswith('arm'):
            p.error('--radio requires a matching SDK package running on the radio')
        subprocess.run(['pidof', 'x6100_gui'], check=True, stdout=subprocess.DEVNULL)
    output = a.output.resolve()
    if output == root or root in output.parents:
        p.error('store results outside the package')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'package-manifest.json').write_text(manifest_text)
    (output / 'package-SHA256SUMS').write_text(checksum_text)
    subprocess.run([sys.executable, str(root / 'tools/freedv/inventory.py'),
                    '--output', str(output / 'inventory.json')], check=True)
    cases, pairs, data = [], [], {}
    try:
        for label, fixture, seconds, workload in [('sync', 'rade-modem', 3, 'continuous'),
                                                 ('silence', 'silence', 3, 'continuous'),
                                                 ('interrupted', 'rade-modem', 14, 'interrupt')]:
            data[label] = {}
            for execution in ('serial', 'parallel'):
                name = '%02d-%s-%s' % (len(cases) + 1, label, execution)
                print('Running', name, flush=True)
                entry = {'case': name, 'execution': execution, 'execution_validated': False}
                cases.append(entry)
                command = [sys.executable, str(root / 'tools/freedv/collect_benchmark.py'),
                           '--mode', 'RADE-V1', '--direction', 'rx', '--execution', execution,
                           '--benchmark', str(root / 'bin/rade-pipeline-bench'),
                           '--fixture', str(root / 'fixtures' / (fixture + '.raw')),
                           '--seconds', str(seconds), '--pacing', 'paced', '--warmup', '1',
                           '--workload', workload, '--sample-interval', '1', '--output', str(output / name)]
                done = subprocess.run(command)
                entry['return_code'] = done.returncode
                try:
                    result = strict_json((output / name / 'result.json').read_text())
                    assessment = validate_pipeline_result(result, execution=execution, pacing='paced',
                                                         workload=workload, seconds=seconds, warmup=1,
                                                         fixture_name=fixture, require_complete=False)
                    if (done.returncode == 0) != result['execution_complete']:
                        raise ValueError('exit status disagrees with execution completeness')
                    entry.update(assessment)
                    entry['failure_reason'] = result['failure_reason']
                    entry['capacity_observations'] = {'timing': result['timing'], 'queue': result['queue']}
                    data[label][execution] = result
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    entry['error'] = str(exc)
                # Continue after incomplete cases to retain the remaining paired evidence.
            if len(data[label]) == 2:
                pairs.append({'workload': label, **compare_pipeline_pair(data[label]['serial'], data[label]['parallel'])})
            else:
                pairs.append({'workload': label, 'status': 'INCONCLUSIVE', 'reason': 'Missing valid result.'})
    finally:
        complete = len(cases) == 6 and all(c.get('return_code') == 0 and c['execution_validated'] for c in cases)
        equivalent = len(pairs) == 3 and all(pair['status'] == 'MATCH' for pair in pairs)
        summary = {'schema_version': 1, 'experiment': 'rade-rx-two-worker', 'suite': 'paired-smoke',
                   'cases': cases, 'pairs': pairs, 'execution_complete': complete,
                   'functional_regression_evidence_matches': equivalent,
                   'capacity_qualification': 'INCONCLUSIVE', 'hardware_qualification': 'INCONCLUSIVE',
                   'original_70_percent_assessment': 'unchanged_serial_reference_required',
                   'package_checksums_verified_before_run': True,
                   'package_checksum_file': 'package-SHA256SUMS', 'radio_gui_process_checked': a.radio,
                   'automatic_upload': False,
                   'reason': 'Short isolated pipeline experiment; original serial gate and sustained hardware qualification remain separate.'}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Results saved locally to', output)
    print('Execution complete:', complete, '; regression evidence matches:', equivalent,
          '; capacity and hardware qualification: INCONCLUSIVE')
    if not complete or not equivalent:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
