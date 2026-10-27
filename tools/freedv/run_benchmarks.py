#!/usr/bin/env python3
"""Run local benchmark cases without audio-device or radio-control access."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def assess_timing(result):
    """Screen measured timing independently of workload execution or qualification."""
    p99 = result.get('p99_frame_ratio')
    misses = result.get('deadline_misses')
    valid_p99 = type(p99) in (int, float) and math.isfinite(p99) and p99 >= 0
    valid_misses = type(misses) is int and misses >= 0
    if not valid_p99 or not valid_misses:
        return {'status': 'INCONCLUSIVE', 'p99_target_ratio': 0.70,
                'reason': 'Missing or invalid p99 ratio or paced deadline count.'}
    # Do not round before comparison: the first radio TX result was 0.700613.
    status = 'FAIL' if p99 > 0.70 else ('REVIEW' if misses else 'WITHIN_LIMITS')
    return {'status': status, 'p99_target_ratio': 0.70, 'p99_frame_ratio': p99,
            'p99_within_target': p99 <= 0.70, 'deadline_misses': misses,
            'reason': ('p99 exceeds the proposed 70% target.' if status == 'FAIL' else
                       'Deadline misses need review for sustained failure.' if status == 'REVIEW' else
                       'Measured p99 is within target and no deadline misses were reported.'),
            'full_qualification': False}


def timing_summary(cases, execution_complete):
    statuses = [case.get('timing', {}).get('status', 'INCONCLUSIVE') for case in cases]
    if 'FAIL' in statuses:
        return 'FAIL'
    if 'REVIEW' in statuses:
        return 'REVIEW'
    if execution_complete and statuses and all(status == 'WITHIN_LIMITS' for status in statuses):
        return 'WITHIN_LIMITS'
    return 'INCONCLUSIVE'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--output', type=Path, required=True, help='new directory on a writable data partition')
    p.add_argument('--suite', choices=('smoke', 'qualification'), default='smoke')
    p.add_argument('--radio', action='store_true', help='require the SDK package, ARM host and running GUI')
    a = p.parse_args()
    root = a.package.resolve()
    manifest = json.loads((root / 'package-manifest.json').read_text())
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        expected, filename = line.split('  ', 1)
        path = (root / filename).resolve()
        if root not in path.parents or not path.is_file() or digest(path) != expected:
            raise ValueError('package verification failed: ' + filename)
    if a.radio:
        if manifest['target'] != 'x6200-buildroot' or not platform.machine().startswith('arm'):
            p.error('--radio requires a matching SDK package running on the radio')
        subprocess.run(['pidof', 'x6100_gui'], check=True, stdout=subprocess.DEVNULL)
    elif a.suite == 'qualification':
        p.error('qualification requires --radio and the normal GUI/waterfall active')
    output = a.output.resolve()
    if output == root or root in output.parents:
        p.error('store results outside the package')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'package-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    subprocess.run([sys.executable, str(root / 'tools/freedv/inventory.py'), '--output', str(output / 'inventory.json')], check=True)
    cases = [('700E', 'tx', '700e-speech', 'continuous'), ('700E', 'rx', '700e-modem', 'continuous'),
             ('700E', 'rx', 'silence', 'continuous'), ('RADE-V1', 'tx', 'rade-speech', 'continuous'),
             ('RADE-V1', 'rx', 'rade-modem', 'continuous'), ('RADE-V1', 'rx', 'silence', 'continuous'),
             ('RADE-V1', 'rx', 'rade-modem', 'interrupt')]
    results = []
    try:
        for i, (mode, direction, fixture, workload) in enumerate(cases):
            name = '%02d-%s-%s-%s-%s' % (i + 1, mode, direction, fixture, workload)
            print('Running', name, flush=True)
            seconds = 1800 if a.suite == 'qualification' else (14 if workload == 'interrupt' else 3)
            command = [sys.executable, str(root / 'tools/freedv/collect_benchmark.py'), '--mode', mode,
                       '--benchmark', str(root / 'bin' / ('codec2-frame-bench' if mode == '700E' else 'rade-frame-bench')),
                       '--fixture', str(root / 'fixtures' / (fixture + '.raw')), '--direction', direction,
                       '--seconds', str(seconds), '--pacing', 'paced', '--warmup', '5' if a.suite == 'qualification' else '1',
                       '--workload', workload, '--sample-interval', '10' if a.suite == 'qualification' else '1',
                       '--output', str(output / name)]
            done = subprocess.run(command)
            entry = {'case': name, 'return_code': done.returncode, 'validated': False}
            results.append(entry)
            if done.returncode:
                raise RuntimeError('benchmark failed: ' + name)
            data = json.loads((output / name / 'result.json').read_text())
            if data['mode'] != mode or data['direction'] != direction or data['frames'] <= 0 or data['radio_io']:
                raise ValueError('invalid benchmark identity or empty workload: ' + name)
            if direction == 'rx':
                sync = data['sync_frames' if mode == '700E' else 'sync_blocks']
                if (fixture == 'silence' and sync != 0) or (fixture != 'silence' and sync <= 0):
                    raise ValueError('unexpected receive synchronization: ' + name)
                if workload == 'interrupt' and data['sync_blocks_after_first_interrupt'] <= 0:
                    raise ValueError('receiver did not reacquire after interruption')
            entry['validated'] = True
            entry['timing'] = assess_timing(data)
            print('Execution validated; timing:', entry['timing']['status'], '-',
                  entry['timing']['reason'], flush=True)
    finally:
        complete = len(results) == len(cases) and all(r['return_code'] == 0 and r['validated'] for r in results)
        summary = {'schema_version': 1, 'suite': a.suite, 'cases': results,
                   'complete': complete, 'execution_complete': complete,
                   'codec_timing_check': timing_summary(results, complete),
                   'timing_check_is_full_qualification': False,
                   'hardware_qualification': 'INCONCLUSIVE',
                   'reason': 'Routing and PTT unmeasured; queue/underrun instrumentation remains incomplete.',
                   'automatic_upload': False}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Results saved locally to', output)
    print('Execution complete:', complete, '; codec timing check:', summary['codec_timing_check'],
          '; full hardware qualification: INCONCLUSIVE')


if __name__ == '__main__':
    main()
