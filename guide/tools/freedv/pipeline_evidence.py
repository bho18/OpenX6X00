#!/usr/bin/env python3
"""Validate experimental pipeline evidence without granting a timing pass."""
import math
import re


def strict_json(text):
    import json
    def reject(value):
        raise ValueError('nonfinite JSON token: ' + value)
    return json.loads(text, parse_constant=reject)


def integer(value, label, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(label + ': invalid integer')
    return value


def interrupted_samples(start_ns, duration_ns):
    """Native 8 kHz samples muted in [10,12) seconds of each 12-second cycle."""
    def before(ns):
        samples, residue = divmod(ns * 8000, 1_000_000_000)
        if residue:
            raise ValueError('input interval is not an integral modem sample count')
        cycles, offset = divmod(samples, 12 * 8000)
        return cycles * 2 * 8000 + max(0, offset - 10 * 8000)
    return before(start_ns + duration_ns) - before(start_ns)


def validate_pipeline_result(result, *, execution, pacing, workload, seconds,
                             warmup, fixture_name, require_complete=True,
                             allow_test_hooks=False):
    if not isinstance(result, dict):
        raise ValueError('result must be an object')
    if (type(result.get('schema_version')) is not int or result['schema_version'] != 1 or
            result.get('experiment') != 'rade-rx-two-worker' or
            (result.get('execution'), result.get('pacing'), result.get('workload')) !=
            (execution, pacing, workload)):
        raise ValueError('unexpected pipeline result identity')
    if (result.get('radio_io') is not False or result.get('full_pipeline_qualified') is not False or
            result.get('workers_joined') is not True or
            result.get('original_70_percent_assessment') != 'unchanged_serial_reference_required'):
        raise ValueError('pipeline result changed qualification or join semantics')
    if type(result.get('test_hooks_used')) is not bool or (result['test_hooks_used'] and not allow_test_hooks):
        raise ValueError('unexpected test hooks in measured pipeline run')
    if type(result.get('execution_complete')) is not bool:
        raise ValueError('missing execution completion flag')
    complete = result['execution_complete']
    if require_complete and not complete:
        raise ValueError('pipeline execution did not complete')
    expected_policy = 'fail-on-full' if pacing == 'paced' else 'block-unpaced-only'
    if allow_test_hooks and result.get('failure_reason') == 'queue_full':
        expected_policy = 'fail-on-full'
    if (result.get('queue_policy') != expected_policy or
            result.get('release_semantics') != 'last_input_sample_available_fixed_cumulative_nin' or
            result.get('checksum_algorithm') != 'fnv1a64-noncryptographic' or
            result.get('checksum_cost_in_service_timing') is not True):
        raise ValueError('unexpected queue/release/checksum semantics')
    q = result.get('queue', {})
    if q.get('capacity') != 2 or q.get('capacity_excludes_b_private_record') is not True:
        raise ValueError('unexpected queue capacity contract')
    for key in ('max_depth', 'full_events', 'producer_wait_ns', 'consumer_wait_ns', 'dropped_blocks', 'abandoned_blocks'):
        integer(q.get(key), 'queue.' + key)
    if q['max_depth'] > q['capacity']:
        raise ValueError('queue capacity exceeded')
    for key in ('attempted_blocks', 'processed_input_blocks', 'submitted_blocks', 'completed_blocks', 'warmup_audio_ns'):
        integer(result.get(key), key)
    if not result['attempted_blocks'] >= result['processed_input_blocks'] >= result['submitted_blocks'] >= result['completed_blocks']:
        raise ValueError('invalid pipeline block progression')
    if (result['processed_input_blocks'] != result['submitted_blocks'] + q['dropped_blocks'] or
            result['submitted_blocks'] != result['completed_blocks'] + q['abandoned_blocks']):
        raise ValueError('block conservation failed')
    count_keys = ('frames', 'audio_ns', 'output_samples', 'resampled_output_samples', 'sync_blocks',
                  'feature_blocks', 'nonzero_output_samples', 'interrupted_input_samples',
                  'sync_blocks_after_first_interrupt', 'eoo_blocks')
    for scope in ('full', 'measured'):
        counters = result.get(scope, {})
        for key in count_keys:
            integer(counters.get(key), scope + '.' + key)
        for key in ('feature_hash', 'native_float_hash', 'pcm_s16_hash', 'metadata_hash'):
            if not isinstance(counters.get(key), str) or not re.fullmatch('[0-9a-f]{16}', counters[key]):
                raise ValueError(scope + ': missing regression hash')
        if (counters['nonzero_output_samples'] > counters['resampled_output_samples'] or
                counters['sync_blocks'] > counters['frames'] or counters['feature_blocks'] > counters['frames'] or
                counters['sync_blocks_after_first_interrupt'] > counters['sync_blocks'] or
                counters['eoo_blocks'] > counters['frames'] or
                (counters['feature_blocks'] == 0) != (counters['output_samples'] == 0)):
            raise ValueError(scope + ': inconsistent functional counters')
    full, measured = result['full'], result['measured']
    if full['frames'] != result['completed_blocks'] or measured['frames'] > full['frames']:
        raise ValueError('completed-frame accounting differs')
    if any(measured[key] > full[key] for key in count_keys):
        raise ValueError('measured counter exceeds full stream')
    for scope, counts in [('full', full), ('measured', measured)]:
        if not counts['frames'] * 100_000_000 <= counts['audio_ns'] <= counts['frames'] * 140_000_000:
            raise ValueError(scope + ': audio interval/frame count differs')
    timing = result.get('timing', {})
    for key in ('wall_ns_including_drain', 'summed_service_ns', 'a_service_ns', 'b_service_ns',
                'a_cpu_ns', 'b_cpu_ns', 'fullpath_one_input_interval_misses',
                'ingress_late_more_than_one_interval', 'ingress_late_max_ns',
                'maximum_virtual_input_backlog_ns_including_warmup'):
        integer(timing.get(key), 'timing.' + key)
    if timing['summed_service_ns'] != timing['a_service_ns'] + timing['b_service_ns']:
        raise ValueError('worker service sum differs')
    nominal = timing.get('nominal_release_one_interval_misses')
    if pacing == 'paced':
        integer(nominal, 'nominal completion misses')
        if nominal > measured['frames']:
            raise ValueError('too many nominal completion misses')
    elif nominal is not None:
        raise ValueError('unpaced run claims nominal completion deadline')
    if timing['fullpath_one_input_interval_misses'] > measured['frames']:
        raise ValueError('too many full-path completion misses')
    for name in ('a_service_ms', 'b_service_ms', 'full_path_ms', 'queue_residence_ms',
                 'a_service_input_ratio', 'b_service_feature_ratio', 'full_path_input_ratio',
                 'nominal_release_to_completion_ms', 'output_completion_period_ms',
                 'input_resampling_ms', 'rade_rx_ms', 'fargan_ms', 'output_resampling_ms'):
        distribution = timing.get(name, {})
        count = integer(distribution.get('count'), name + '.count')
        expected_count = measured['frames']
        if name == 'b_service_feature_ratio':
            expected_count = measured['feature_blocks']
        elif name == 'output_completion_period_ms':
            expected_count = max(0, measured['feature_blocks'] - 1)
        elif name == 'nominal_release_to_completion_ms' and pacing != 'paced':
            expected_count = 0
        if count != expected_count:
            raise ValueError(name + ': timing population differs')
        values = [distribution.get(key) for key in ('mean', 'p99', 'max')]
        if not count:
            if values != [None, None, None]:
                raise ValueError(name + ': empty distribution has values')
        else:
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
                raise ValueError(name + ': invalid timing distribution')
            if values[1] > values[2] or values[0] > values[2] + 1e-9:
                raise ValueError(name + ': inconsistent maximum')
    if complete:
        integer(measured['frames'], 'measured.frames', positive=True)
        if result.get('failure_reason') is not None or q['dropped_blocks'] or q['abandoned_blocks']:
            raise ValueError('completed result discarded blocks or recorded failure')
        if result['processed_input_blocks'] != result['completed_blocks']:
            raise ValueError('complete run left processed records unfinished')
        if not seconds * 1e9 <= measured['audio_ns'] <= (seconds + 0.14) * 1e9 + 1:
            raise ValueError('measurement duration incomplete')
        if not warmup * 1e9 <= result['warmup_audio_ns'] <= (warmup + 0.14) * 1e9 + 1:
            raise ValueError('warm-up duration differs')
        if full['audio_ns'] != measured['audio_ns'] + result['warmup_audio_ns']:
            raise ValueError('warm-up and measured audio do not account for full stream')
        if fixture_name == 'silence':
            if measured['sync_blocks'] or measured['feature_blocks'] or measured['output_samples']:
                raise ValueError('silence unexpectedly synchronized or produced speech')
        elif not (measured['sync_blocks'] and measured['feature_blocks'] and measured['output_samples']):
            raise ValueError('signal fixture did not synchronize and decode')
        if workload == 'interrupt':
            expected_muted = interrupted_samples(result['warmup_audio_ns'], measured['audio_ns'])
            if measured['interrupted_input_samples'] != expected_muted:
                raise ValueError('interrupted receiver muted an unexpected sample count')
            if (expected_muted and full['audio_ns'] > 12_000_000_000 and
                    not measured['sync_blocks_after_first_interrupt']):
                raise ValueError('interrupted receiver did not demonstrate recovery')
    return {'execution_validated': complete, 'capacity_qualification': 'INCONCLUSIVE',
            'original_70_percent_assessment': 'unchanged_serial_reference_required'}


def compare_pipeline_pair(serial, parallel):
    """Compare functional regression evidence, never host/target timing."""
    if not serial.get('execution_complete') or not parallel.get('execution_complete'):
        return {'status': 'INCONCLUSIVE', 'reason': 'One or both executions were incomplete.'}
    equal = serial['full'] == parallel['full'] and serial['measured'] == parallel['measured']
    return {'status': 'MATCH' if equal else 'MISMATCH',
            'evidence': 'functional_counters_and_noncryptographic_regression_hashes',
            'waveform_proof': False, 'capacity_qualification': 'INCONCLUSIVE'}
