#!/usr/bin/env python3
"""Validate acquisition worker execution evidence; never grant a capacity pass."""
import math

from pipeline_evidence import validate_pipeline_result


def uint(value, name):
    if type(value) is not int or not 0 <= value <= (1 << 64) - 1:
        raise ValueError(name + ': unsigned 64-bit integer required')
    return value


def validate_acquisition_result(result, *, workers, execution, pacing, workload,
                                seconds, warmup, fixture_name, require_complete=True):
    """Check the original pipeline contract plus actual pool participation.

    Statistics include startup/warm-up and measured input. CPU counters describe
    helper jobs, not whole-chip utilization or steady-state processing demand.
    """
    if type(workers) is not int or workers not in (1, 2, 3, 4):
        raise ValueError('workers must be an integer from 1 through 4')
    assessment = validate_pipeline_result(
        result, execution=execution, pacing=pacing, workload=workload, seconds=seconds,
        warmup=warmup, fixture_name=fixture_name, require_complete=require_complete)
    if result.get('a_cpu_scope') != 'measured_caller_thread_only_excludes_acquisition_helpers':
        raise ValueError('caller CPU scope differs')
    stats = result.get('acquisition_workers')
    if not isinstance(stats, dict):
        raise ValueError('missing acquisition worker evidence')
    for field in ('requested', 'effective', 'helper_threads'):
        uint(stats.get(field), 'acquisition_workers.' + field)
    if stats['requested'] != workers or stats['effective'] != workers or stats['helper_threads'] != workers - 1:
        raise ValueError('requested/effective acquisition workers differ')
    if (stats.get('includes_calling_thread') is not True or
            stats.get('separate_fargan_thread') is not (execution == 'parallel') or
            stats.get('statistics_scope') != 'whole_stream_including_warmup'):
        raise ValueError('acquisition worker accounting scope differs')
    shutdown = stats.get('shutdown_ms')
    if stats.get('helper_threads_joined') is not True or type(shutdown) not in (int, float) or not math.isfinite(shutdown) or shutdown < 0:
        raise ValueError('acquisition helper shutdown was not confirmed')
    for field in ('calls', 'parallel_calls', 'serial_calls', 'dispatch_wait_ns'):
        uint(stats.get(field), 'acquisition_workers.' + field)
    if stats['calls'] != stats['parallel_calls'] + stats['serial_calls']:
        raise ValueError('acquisition call accounting differs')
    if stats['calls'] > result['processed_input_blocks']:
        raise ValueError('more acquisition calls than processed receiver inputs')
    arrays = {}
    for field in ('helper_jobs', 'helper_cpu_ns'):
        values = stats.get(field)
        if type(values) is not list or len(values) != 3:
            raise ValueError(field + ': exactly three helper slots required')
        arrays[field] = [uint(v, field) for v in values]
    expected_jobs = [stats['parallel_calls'] if slot < workers - 1 else 0 for slot in range(3)]
    if arrays['helper_jobs'] != expected_jobs:
        raise ValueError('actual helper jobs differ from selected worker count')
    if any(arrays['helper_jobs'][slot] and not arrays['helper_cpu_ns'][slot] for slot in range(workers - 1)):
        raise ValueError('active helper jobs have no CPU observation')
    if any(arrays['helper_cpu_ns'][slot] for slot in range(workers - 1, 3)):
        raise ValueError('inactive helper reports CPU work')
    timing = result['timing']
    measured_helpers = uint(timing.get('acquisition_helper_cpu_ns'), 'measured acquisition helper CPU')
    measured_total = uint(timing.get('total_dsp_cpu_ns'), 'total measured DSP CPU')
    if (timing.get('dsp_cpu_scope') != 'completed_measured_records_excludes_warmup' or
            measured_total != timing['a_cpu_ns'] + timing['b_cpu_ns'] + measured_helpers or
            measured_helpers > sum(arrays['helper_cpu_ns']) or (workers == 1 and measured_helpers)):
        raise ValueError('measured DSP/helper CPU populations differ')
    if workers == 1 and (stats['parallel_calls'] or stats['dispatch_wait_ns']):
        raise ValueError('one-worker reference unexpectedly used the pool')
    if result['execution_complete']:
        if not stats['calls']:
            raise ValueError('complete fixture did not exercise acquisition')
        if workers > 1 and not stats['parallel_calls']:
            raise ValueError('selected acquisition helpers did not execute')
    validate_recovery_evidence(result, workload=workload)
    assessment.update(acquisition_workers_checked=workers,
                      actual_helper_jobs_checked=True,
                      capacity_qualification='INCONCLUSIVE')
    return assessment


def compare_acquisition_results(reference, candidate):
    """Compare a complete one-worker reference with another count, never timings."""
    if not reference.get('execution_complete') or not candidate.get('execution_complete'):
        return {'status': 'INCONCLUSIVE', 'reason': 'One or both executions were incomplete.',
                'capacity_qualification': 'INCONCLUSIVE'}
    identity = ('execution', 'pacing', 'workload', 'warmup_audio_ns')
    if any(reference.get(k) != candidate.get(k) for k in identity):
        return {'status': 'MISMATCH', 'reason': 'Execution/input schedule differs.',
                'capacity_qualification': 'INCONCLUSIVE'}
    if reference.get('acquisition_workers', {}).get('effective') != 1:
        raise ValueError('comparison reference must use one acquisition worker')
    matches = (reference['full'] == candidate['full'] and reference['measured'] == candidate['measured']
               and reference.get('acquisition_recovery') == candidate.get('acquisition_recovery'))
    return {'status': 'MATCH' if matches else 'MISMATCH',
            'evidence': 'full_and_measured_functional_counters_and_noncryptographic_regression_hashes',
            'waveform_proof': False, 'capacity_qualification': 'INCONCLUSIVE'}


def validate_recovery_evidence(result, *, workload):
    recovery = result.get('acquisition_recovery')
    if not isinstance(recovery, dict) or recovery.get('scope') != 'whole_processed_input_including_warmup':
        raise ValueError('missing acquisition recovery scope')
    samples = uint(recovery.get('processed_input_samples'), 'processed_input_samples')
    completed_samples = result['full']['audio_ns'] * 8000 // 1_000_000_000
    if samples < completed_samples:
        raise ValueError('processed samples omit completed input')
    if result['execution_complete'] and samples != completed_samples:
        raise ValueError('completed recovery/input durations differ')
    if not result['processed_input_blocks'] * 800 <= samples <= result['processed_input_blocks'] * 1120:
        raise ValueError('processed recovery sample/block accounting differs')
    windows = recovery.get('windows')
    expected_count = samples // 96000 if workload == 'interrupt' else 0
    if type(windows) is not list or len(windows) != expected_count:
        raise ValueError('missing or unexpected completed interruption windows')
    for number, window in enumerate(windows, 1):
        if not isinstance(window, dict) or window.get('end_input_sample') != number * 96000:
            raise ValueError('interruption window identity differs')
        uint(window['end_input_sample'], 'end_input_sample')
        for name in ('sync_blocks_before_next_mute', 'feature_blocks_before_next_mute'):
            count = uint(window.get(name), name)
            if count > result['processed_input_blocks']:
                raise ValueError('interruption recovery count exceeds processed blocks')
            # The selected short fixtures leave at least two seconds after each
            # completed mute. An arbitrary run ending at the mute boundary does
            # not have enough post-mute input to require reacquisition evidence.
            if result['execution_complete'] and samples >= window['end_input_sample'] + 16000 and not count:
                raise ValueError('completed interruption did not recover before the next mute')
    return {'completed_mute_windows': expected_count, 'sample_accounting_checked': True}
