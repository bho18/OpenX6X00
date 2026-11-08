#!/usr/bin/env python3
"""Independent scheduling observations; grading never changes a DUT result.

The probe may span compilation. Only intervals intersecting the recorded
qualification resource window are graded, and both ends of that window must
be covered. Samples stay in memory until the parent closes the build.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

PERIOD_NS = 20_000_000
MAX_GAP_NS = 170_000_000


def grade_window(record, start, end):
    samples = record.get('samples', [])
    if (record.get('schema') != 'x6200.operational-clock.v1' or
            record.get('period_ns') != PERIOD_NS or record.get('max_allowed_gap_ns') != MAX_GAP_NS):
        raise ValueError('Independent clock schema or fixed budgets differ')
    if record.get('errors'):
        raise ValueError('Independent clock observations contain errors')
    if (type(start) is not int or type(end) is not int or not 0 < start < end or
            len(samples) < 2 or samples[0] > start or samples[-1] < end or
            any(type(x) is not int for x in samples) or
            any(b <= a for a, b in zip(samples, samples[1:]))):
        raise ValueError('Independent clock observations do not cover qualification')
    gaps = [(a, b, b-a) for a, b in zip(samples, samples[1:]) if a < end and b > start]
    maximum = max(gap for _, _, gap in gaps)
    if maximum > MAX_GAP_NS:
        raise ValueError('Independent qualification clock exceeded 170 ms: %.3f ms' % (maximum/1e6))
    return {'status': 'PASS', 'start_monotonic_ns': start, 'end_monotonic_ns': end,
            'period_ns': PERIOD_NS, 'max_allowed_gap_ns': MAX_GAP_NS,
            'max_gap_ns': maximum, 'interval_count': len(gaps),
            'max_clock_delta_difference_ns': record.get('max_clock_delta_difference_ns', 0)}


def cpu_stat(root):
    fields = {}
    for line in (root/'cpu.stat').read_text().splitlines():
        name, value = line.split()
        if name in fields or not value.isdecimal():
            raise ValueError('Independent cgroup CPU counter is invalid')
        fields[name] = int(value)
    required = ('usage_usec', 'user_usec', 'system_usec', 'nr_periods', 'nr_throttled', 'throttled_usec')
    if any(name not in fields for name in required):
        raise ValueError('Independent cgroup CPU counters are incomplete')
    return {name: fields[name] for name in required}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ready', 'stop', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--cpu', required=True, type=int)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--source-tree-sha256', required=True)
    parser.add_argument('--build-id', required=True)
    parser.add_argument('--cgroup-root', type=Path, default=Path('/sys/fs/cgroup'))
    args = parser.parse_args()
    for path in (args.ready, args.stop, args.output):
        if path.exists():
            raise ValueError('Refusing to reuse probe evidence: '+str(path))
    if args.cpu not in os.sched_getaffinity(0):
        raise ValueError('Independent probe CPU is unavailable')
    os.sched_setaffinity(0, {args.cpu})
    record = {'schema': 'x6200.operational-clock.v1', 'source_commit': args.source_commit,
              'source_tree_sha256': args.source_tree_sha256, 'build_id': args.build_id,
              'cpu_affinity': sorted(os.sched_getaffinity(0)), 'period_ns': PERIOD_NS,
              'max_allowed_gap_ns': MAX_GAP_NS, 'samples': [], 'errors': [],
              'probe_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'max_clock_delta_difference_ns': 0, 'max_sampling_work_ns': 0,
              'cpu_max': (args.cgroup_root/'cpu.max').read_text().strip(),
              'cgroup_cpu_before': cpu_stat(args.cgroup_root)}
    with args.ready.open('x') as stream:
        json.dump({key: value for key, value in record.items() if key != 'samples'}, stream)
    samples = record['samples']
    previous = None
    def sample():
        nonlocal previous
        at = time.monotonic_ns()
        current = (at, time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW),
                   time.clock_gettime_ns(time.CLOCK_BOOTTIME), time.process_time_ns())
        if previous is not None:
            deltas = [now-before for now, before in zip(current, previous)]
            if any(delta < 0 for delta in deltas):
                record['errors'].append('Independent clock moved backwards')
            record['max_clock_delta_difference_ns'] = max(record['max_clock_delta_difference_ns'],
                abs(deltas[0]-deltas[1]), abs(deltas[0]-deltas[2]))
        previous = current
        samples.append(at)
        record['max_sampling_work_ns'] = max(record['max_sampling_work_ns'], time.monotonic_ns()-at)
    sample()
    while not args.stop.exists():
        time.sleep(PERIOD_NS/1e9)
        sample()
    sample()
    record['cgroup_cpu_after'] = cpu_stat(args.cgroup_root)
    if (args.cgroup_root/'cpu.max').read_text().strip() != record['cpu_max']:
        record['errors'].append('Independent CPU quota changed')
    if any(record['cgroup_cpu_after'][name] < value for name, value in record['cgroup_cpu_before'].items()):
        record['errors'].append('Independent CPU counter moved backwards')
    record['stop'] = json.loads(args.stop.read_text())
    with args.output.open('x') as stream:
        json.dump(record, stream, separators=(',', ':'))
        stream.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
