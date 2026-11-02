#!/usr/bin/env python3
"""Exercise offline RX pipeline equivalence, bounded queues and failure shutdown.

Tests assert functional/accounting invariants, never host performance targets.
Every child has a timeout solely to detect a hang. Failure injection is driven
by queue state or consumed-record count, not by hoping a sleep causes overload.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

DISTRIBUTIONS = {
    'a_service_ms', 'b_service_ms', 'full_path_ms', 'queue_residence_ms',
    'a_service_input_ratio', 'b_service_feature_ratio', 'full_path_input_ratio',
    'nominal_release_to_completion_ms', 'input_resampling_ms', 'rade_rx_ms',
    'fargan_ms', 'output_resampling_ms', 'output_completion_period_ms',
}
COUNTERS = {
    'frames', 'audio_ns', 'output_samples', 'resampled_output_samples',
    'sync_blocks', 'feature_blocks', 'nonzero_output_samples',
    'interrupted_input_samples', 'sync_blocks_after_first_interrupt', 'eoo_blocks',
}
HASHES = {'feature_hash', 'native_float_hash', 'pcm_s16_hash', 'metadata_hash'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def integer(value, label):
    require(type(value) is int and value >= 0, f'{label}: expected nonnegative integer')


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            f'{label}: expected finite nonnegative number')


def strict_json(text):
    def invalid(value):
        raise AssertionError(f'nonstandard JSON number: {value}')
    return json.loads(text, parse_constant=invalid)


def check_common(result, execution, pacing, workload):
    require(result['schema_version'] == 1, 'unexpected schema')
    require(result['experiment'] == 'rade-rx-two-worker', 'unexpected experiment')
    require((result['execution'], result['pacing'], result['workload']) ==
            (execution, pacing, workload), 'command/result mismatch')
    require(result['workers_joined'] is True, 'workers did not join')
    require(result['radio_io'] is False and result['full_pipeline_qualified'] is False,
            'offline experiment claimed radio or full pipeline qualification')
    require(result['original_70_percent_assessment'] == 'unchanged_serial_reference_required',
            'original performance gate was changed')
    require(result['checksum_algorithm'] == 'fnv1a64-noncryptographic', 'unexpected checksum semantics')
    require(result['checksum_cost_in_service_timing'] is True, 'checksum cost was excluded')
    require(result['release_semantics'] == 'last_input_sample_available_fixed_cumulative_nin',
            'virtual input clock definition changed')
    for key in ('attempted_blocks', 'processed_input_blocks', 'submitted_blocks', 'completed_blocks', 'warmup_audio_ns',
                'peak_rss_kib_linux'):
        integer(result[key], key)
    require(result['attempted_blocks'] >= result['processed_input_blocks'] >= result['submitted_blocks'] >= result['completed_blocks'],
            'block accounting is out of order')
    for key in ('fixture_preparation_ms', 'initialization_ms'):
        finite(result[key], key)
    queue = result['queue']
    require(queue['capacity'] == 2 and queue['capacity_excludes_b_private_record'] is True,
            'queue capacity/ownership definition changed')
    for key in ('max_depth', 'full_events', 'producer_wait_ns', 'consumer_wait_ns',
                'dropped_blocks', 'abandoned_blocks'):
        integer(queue[key], 'queue.' + key)
    require(queue['max_depth'] <= queue['capacity'], 'bounded queue overflowed')
    require(result['processed_input_blocks'] == result['submitted_blocks'] + queue['dropped_blocks'],
            'processed input was neither submitted nor reported dropped')
    require(result['submitted_blocks'] == result['completed_blocks'] + queue['abandoned_blocks'],
            'accepted input was neither completed nor reported abandoned')
    if execution == 'serial':
        require(queue['max_depth'] == queue['full_events'] == queue['producer_wait_ns'] ==
                queue['consumer_wait_ns'] == queue['dropped_blocks'] == queue['abandoned_blocks'] == 0,
                'serial reference used the queue')
    for group in ('full', 'measured'):
        counters = result[group]
        require(set(counters) == COUNTERS | HASHES, f'{group}: unexpected functional fields')
        for key in COUNTERS:
            integer(counters[key], group + '.' + key)
        for key in HASHES:
            require(isinstance(counters[key], str) and re.fullmatch('[0-9a-f]{16}', counters[key]),
                    group + '.' + key + ': invalid checksum')
        require(counters['feature_blocks'] <= counters['frames'] and
                counters['sync_blocks'] <= counters['frames'] and
                counters['sync_blocks_after_first_interrupt'] <= counters['sync_blocks'],
                group + ': invalid frame counters')
        require(counters['nonzero_output_samples'] <= counters['resampled_output_samples'],
                group + ': invalid output counters')
    full, measured = result['full'], result['measured']
    require(full['frames'] == result['completed_blocks'], 'completed record count mismatch')
    for key in COUNTERS:
        require(measured[key] <= full[key], f'measured {key} includes extra records')
    timing = result['timing']
    frames = measured['frames']
    for key in ('wall_ns_including_drain', 'summed_service_ns', 'a_service_ns', 'b_service_ns',
                'a_cpu_ns', 'b_cpu_ns', 'fullpath_one_input_interval_misses',
                'ingress_late_more_than_one_interval', 'ingress_late_max_ns',
                'maximum_virtual_input_backlog_ns_including_warmup'):
        integer(timing[key], 'timing.' + key)
    require(timing['summed_service_ns'] == timing['a_service_ns'] + timing['b_service_ns'],
            'worker service totals do not add up')
    require(timing['fullpath_one_input_interval_misses'] <= frames, 'too many full path misses')
    require(timing['maximum_virtual_input_backlog_ns_including_warmup'] >= timing['ingress_late_max_ns'],
            'input backlog omitted measured lateness')
    if pacing == 'unpaced':
        require(timing['nominal_release_one_interval_misses'] is None,
                'unpaced run presented nominal real-time deadlines')
    else:
        integer(timing['nominal_release_one_interval_misses'], 'nominal misses')
        require(timing['nominal_release_one_interval_misses'] <= frames, 'too many nominal misses')
    rate = timing['offline_output_samples_per_wall_second']
    if timing['wall_ns_including_drain']:
        finite(rate, 'offline output rate')
        expected = measured['resampled_output_samples'] * 1e9 / timing['wall_ns_including_drain']
        require(abs(rate - expected) <= 0.000001, 'offline output rate disagrees with completed output')
    else:
        require(rate is None and frames == 0, 'empty timing window had output rate')
    for name in DISTRIBUTIONS:
        distribution = timing[name]
        count = distribution['count']
        integer(count, name + '.count')
        require(count <= frames, name + ': too many observations')
        if not count:
            require(all(distribution[k] is None for k in ('mean', 'p99', 'max')),
                    name + ': empty distribution must be null')
        else:
            for key in ('mean', 'p99', 'max'):
                finite(distribution[key], name + '.' + key)
            require(distribution['mean'] <= distribution['max'] + 1e-9 and
                    distribution['p99'] <= distribution['max'] + 1e-9,
                    name + ': invalid distribution')
        if name == 'b_service_feature_ratio':
            require(count == measured['feature_blocks'], 'B ratio included inactive feature records')
        elif name == 'nominal_release_to_completion_ms':
            require(count == (frames if pacing == 'paced' else 0), 'nominal latency population mismatch')
        elif name == 'output_completion_period_ms':
            require(count <= max(0, measured['feature_blocks'] - 1), 'invalid output completion population')
        else:
            require(count == frames, name + ': warm-up entered or measured frames omitted')
    if frames:
        full_mean = timing['full_path_ms']['mean']
        service_mean = timing['a_service_ms']['mean'] + timing['b_service_ms']['mean']
        require(full_mean + 1e-7 >= service_mean, 'full path latency excluded worker service time')
        require(timing['full_path_input_ratio']['p99'] + 1e-9 >=
                timing['a_service_input_ratio']['p99'], 'full path percentile below component percentile')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--original-stage-binary', type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new result directory')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    fixtures = args.fixtures.resolve()
    records = []

    def launch(label, mode, fixture, seconds, pacing, warmup, workload, extra=(), original=False):
        directory = args.output / label
        directory.mkdir()
        if original:
            command = [str(args.original_stage_binary.resolve()), 'rx', str(fixtures / fixture),
                       str(seconds), pacing, str(warmup), workload]
        else:
            command = [str(binary), mode, str(fixtures / fixture), str(seconds), pacing,
                       str(warmup), workload, *extra]
        (directory / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as error:
            raise AssertionError(f'{label}: child did not exit/join within 120 seconds') from error
        (directory / 'stdout.json').write_text(process.stdout)
        (directory / 'stderr.txt').write_text(process.stderr)
        (directory / 'exit-code.txt').write_text(str(process.returncode) + '\n')
        result = strict_json(process.stdout)
        if not original:
            check_common(result, mode, pacing, workload)
            require(result['test_hooks_used'] is bool(extra), label + ': incorrect test-hook disclosure')
        records.append({'case': label, 'returncode': process.returncode,
                        'execution_complete': result.get('execution_complete'),
                        'failure_reason': result.get('failure_reason')})
        return process.returncode, result

    cases = [
        ('sync', 'rade-modem.raw', 3, 1, 'continuous'),
        ('silence', 'silence.raw', 3, 1, 'continuous'),
        ('interrupted', 'rade-modem.raw', 14, 1, 'interrupt'),
        ('cold-sync', 'rade-modem.raw', 1, 0, 'continuous'),
    ]
    for name, fixture, seconds, warmup, workload in cases:
        pair = []
        for mode in ('serial', 'parallel'):
            code, result = launch(name + '-' + mode, mode, fixture, seconds, 'unpaced', warmup, workload)
            require(code == 0 and result['execution_complete'] is True and result['failure_reason'] is None,
                    name + '-' + mode + ': normal execution did not complete')
            require(result['attempted_blocks'] == result['submitted_blocks'] == result['completed_blocks'],
                    name + ': completed run lost records')
            require(result['queue']['dropped_blocks'] == result['queue']['abandoned_blocks'] == 0,
                    name + ': completed run dropped/abandoned work')
            require(result['queue_policy'] == 'block-unpaced-only', 'unpaced correctness queue policy changed')
            require(result['full']['audio_ns'] == result['measured']['audio_ns'] + result['warmup_audio_ns'],
                    name + ': warm-up accounting mismatch')
            require(seconds * 1e9 <= result['measured']['audio_ns'] < seconds * 1e9 + 140_000_000,
                    name + ': requested input duration changed')
            if warmup:
                require(result['full']['frames'] > result['measured']['frames'], name + ': warm-up missing')
            else:
                require(result['full'] == result['measured'], name + ': zero warm-up changed populations')
            pair.append(result)
        for population in ('full', 'measured'):
            require(pair[0][population] == pair[1][population],
                    name + ': parallel processing changed ' + population + ' counters/checksums')
        if name == 'silence':
            require(pair[1]['measured']['feature_blocks'] == pair[1]['measured']['output_samples'] == 0,
                    'silence workload unexpectedly generated features/speech')
        if name == 'interrupted':
            require(pair[1]['measured']['interrupted_input_samples'] > 0 and
                    pair[1]['measured']['sync_blocks_after_first_interrupt'] > 0,
                    'interruption or reacquisition did not execute')
        if args.original_stage_binary:
            code, original = launch(name + '-original-stage', 'serial', fixture, seconds,
                                    'unpaced', warmup, workload, original=True)
            require(code == 0, name + ': original stage benchmark failed')
            for key in COUNTERS - {'audio_ns', 'eoo_blocks'}:
                require(pair[0]['measured'][key] == original[key], name + ': serial refactor changed ' + key)
            require(pair[0]['measured']['audio_ns'] == round(original['audio_seconds'] * 1e9),
                    name + ': serial refactor changed represented duration')
            require(pair[0]['warmup_audio_ns'] == round(original['warmup_audio_seconds'] * 1e9),
                    name + ': serial refactor changed warm-up duration')
        print(name + ': matching full/measured counters and regression hashes', flush=True)

    for warmup in (0, 1):
        label = f'held-consumer-warmup-{warmup}'
        code, result = launch(label, 'parallel', 'silence.raw', 1, 'paced', warmup, 'continuous',
                              ('--test-hold-b-until-full',))
        require(code != 0 and result['execution_complete'] is False and result['failure_reason'] == 'queue_full',
                label + ': deterministic saturation was hidden')
        require(result['queue_policy'] == 'fail-on-full', label + ': paced run used backpressure to hide overload')
        require(result['queue']['max_depth'] == 2 and result['queue']['full_events'] >= 1,
                label + ': full bounded queue did not execute')
        require(result['queue']['dropped_blocks'] == 1 and result['queue']['abandoned_blocks'] == 0,
                label + ': failure drain accounting changed')
        require(result['submitted_blocks'] == result['completed_blocks'] == 2,
                label + ': accepted records did not drain before joining')
        require(result['measured']['frames'] == (2 if warmup == 0 else 0),
                label + ': failure/warm-up population mismatch')
        print(label + ': saturation rejected and accepted records drained', flush=True)

    for count in (0, 1):
        label = f'consumer-failure-after-{count}'
        code, result = launch(label, 'parallel', 'rade-modem.raw', 1, 'unpaced', 0, 'continuous',
                              ('--test-fail-b-after', str(count)))
        require(code != 0 and result['execution_complete'] is False and
                result['failure_reason'] == 'injected_worker_b_failure',
                label + ': worker failure was hidden')
        require(result['completed_blocks'] == count and result['queue']['abandoned_blocks'] > 0,
                label + ': worker shutdown accounting changed')
        print(label + ': producer stopped and worker joined', flush=True)

    # Sleep injected inside A is longer than any valid V1 input interval.
    # It deterministically proves that the source clock is not reset when late;
    # this is a fault test, not a host throughput success/failure assertion.
    code, result = launch('delayed-producer-fixed-clock', 'serial', 'silence.raw',
                          1, 'paced', 0, 'continuous', ('--test-delay-a-ms', '250'))
    require(code == 0 and result['execution_complete'] is True,
            'delayed producer did not finish its offline fixture work')
    timing = result['timing']
    require(timing['ingress_late_more_than_one_interval'] > 0 and
            timing['maximum_virtual_input_backlog_ns_including_warmup'] > 120_000_000,
            'producer delay was hidden by resetting/slowing the input clock')
    require(timing['nominal_release_one_interval_misses'] > 0 and
            timing['fullpath_one_input_interval_misses'] > 0,
            'missed deadlines were hidden despite injected overload')
    require(timing['a_service_ms']['mean'] >= 250,
            'injected producer delay was excluded from service wall timing')
    print('delayed-producer-fixed-clock: deadline and virtual backlog failures exposed', flush=True)

    # The V1 initialization line is emitted after handler installation. Wait
    # for that observable readiness boundary instead of sleeping and hoping
    # SIGTERM arrives after the handler exists. This exercises orderly startup
    # cancellation; it does not claim a running DSP call can be interrupted.
    label = 'sigterm-after-handler-ready'
    directory = args.output / label
    directory.mkdir()
    command = [str(binary), 'parallel', str(fixtures / 'rade-modem.raw'),
               '60', 'paced', '1', 'continuous']
    (directory / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    prefix = bytearray()
    deadline = time.monotonic() + 120
    selector = selectors.DefaultSelector()
    selector.register(process.stderr, selectors.EVENT_READ)
    try:
        while b'rade_open: V1 ' not in prefix:
            remaining = deadline - time.monotonic()
            require(remaining > 0, label + ': no handler readiness marker within 120 seconds')
            events = selector.select(timeout=min(remaining, 1.0))
            for key, _ in events:
                data = os.read(key.fileobj.fileno(), 65536)
                require(bool(data), label + ': process ended before readiness marker')
                prefix.extend(data)
            require(process.poll() is None, label + ': process exited before cancellation')
        process.send_signal(signal.SIGTERM)
        stdout, suffix = process.communicate(timeout=120)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    finally:
        selector.close()
    stdout = stdout.decode('utf-8')
    stderr = (bytes(prefix) + suffix).decode('utf-8')
    (directory / 'stdout.json').write_text(stdout)
    (directory / 'stderr.txt').write_text(stderr)
    (directory / 'exit-code.txt').write_text(str(process.returncode) + '\n')
    result = strict_json(stdout)
    check_common(result, 'parallel', 'paced', 'continuous')
    require(result['test_hooks_used'] is False, label + ': unexpected fault hook')
    require(process.returncode != 0 and result['execution_complete'] is False and
            result['failure_reason'] == 'interrupted',
            label + ': SIGTERM did not produce structured incomplete shutdown')
    records.append({'case': label, 'returncode': process.returncode,
                    'execution_complete': result['execution_complete'],
                    'failure_reason': result['failure_reason']})
    print(label + ': structured interruption and joined worker', flush=True)

    summary = {'schema_version': 1, 'passed': True, 'runs': records,
               'performance_asserted': False, 'radio_io': False,
               'full_pipeline_qualified': False}
    (args.output / 'test-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'PASS ({len(records)} executions; functional and failure tests only)', flush=True)


if __name__ == '__main__':
    main()
