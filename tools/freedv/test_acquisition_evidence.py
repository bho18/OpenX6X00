#!/usr/bin/env python3
"""Reject incomplete/misattributed acquisition-worker evidence."""
import copy
import unittest

from test_pipeline_collection import example
from acquisition_evidence import validate_acquisition_result, compare_acquisition_results, validate_recovery_evidence


def evidence(workers=2):
    result = example()
    result['execution'] = 'parallel'
    result['a_cpu_scope'] = 'measured_caller_thread_only_excludes_acquisition_helpers'
    result['acquisition_workers'] = {
        'requested': workers, 'effective': workers, 'helper_threads': workers - 1,
        'includes_calling_thread': True, 'separate_fargan_thread': True,
        'statistics_scope': 'whole_stream_including_warmup',
        'helper_threads_joined': True, 'shutdown_ms': 0.1,
        'calls': 25, 'parallel_calls': 25 if workers > 1 else 0,
        'serial_calls': 0 if workers > 1 else 25,
        'helper_jobs': [25 if i < workers - 1 else 0 for i in range(3)],
        'helper_cpu_ns': [100 if i < workers - 1 else 0 for i in range(3)],
        'dispatch_wait_ns': 100 if workers > 1 else 0,
    }
    helper_cpu = 40 * (workers - 1)
    result['timing'].update(acquisition_helper_cpu_ns=helper_cpu,
                            total_dsp_cpu_ns=result['timing']['a_cpu_ns']+result['timing']['b_cpu_ns']+helper_cpu,
                            dsp_cpu_scope='completed_measured_records_excludes_warmup')
    result['acquisition_recovery'] = {'scope': 'whole_processed_input_including_warmup',
                                      'processed_input_samples': 24000, 'windows': []}
    return result


def validate(result, workers=2, **options):
    return validate_acquisition_result(result, workers=workers, execution='parallel', pacing='unpaced',
                                      workload='continuous', seconds=3, warmup=0,
                                      fixture_name='silence', **options)


class WorkerEvidenceTests(unittest.TestCase):
    def test_all_worker_counts_keep_qualification_inconclusive(self):
        for workers in range(1, 5):
            with self.subTest(workers=workers):
                result = validate(evidence(workers), workers=workers)
                self.assertEqual(result['capacity_qualification'], 'INCONCLUSIVE')

    def test_reject_wrong_worker_count_or_missing_participation(self):
        changes = [lambda r: r['acquisition_workers'].update(requested=True),
                   lambda r: r['acquisition_workers'].update(effective=1),
                   lambda r: r['acquisition_workers'].update(helper_threads=2),
                   lambda r: r['acquisition_workers'].update(calls=24),
                   lambda r: r['acquisition_workers'].update(calls=26, parallel_calls=26, helper_jobs=[26,0,0]),
                   lambda r: r['acquisition_workers'].update(helper_jobs=[0,0,0]),
                   lambda r: r['acquisition_workers'].update(helper_jobs=[25,1,0]),
                   lambda r: r['acquisition_workers'].update(helper_cpu_ns=[100,1,0]),
                   lambda r: r['acquisition_workers'].update(helper_cpu_ns=[0,0,0]),
                   lambda r: r['acquisition_workers'].update(helper_jobs=[25,0]),
                   lambda r: r['acquisition_workers'].update(helper_threads_joined=False),
                   lambda r: r['acquisition_workers'].update(shutdown_ms=float('nan')),
                   lambda r: r['acquisition_workers'].update(statistics_scope='measured_only'),
                   lambda r: r['acquisition_workers'].update(separate_fargan_thread=False),
                   lambda r: r['acquisition_workers'].update(includes_calling_thread=False),
                   lambda r: r.update(a_cpu_scope='all_workers'),
                   lambda r: r['timing'].update(acquisition_helper_cpu_ns=101),
                   lambda r: r['timing'].update(total_dsp_cpu_ns=1),
                   lambda r: r['timing'].update(dsp_cpu_scope='whole_stream'),
                   lambda r: r['acquisition_workers'].update(parallel_calls=0,serial_calls=25,helper_jobs=[0,0,0],helper_cpu_ns=[0,0,0])]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                result = evidence(); change(result)
                with self.assertRaises(ValueError): validate(result)

    def test_one_worker_cannot_claim_pool_execution(self):
        result = evidence(1); result['acquisition_workers']['dispatch_wait_ns'] = 1
        with self.assertRaises(ValueError): validate(result, workers=1)

    def test_incomplete_run_keeps_failure_without_qualifying(self):
        result = evidence()
        result.update(execution_complete=False, failure_reason='interrupted')
        stats = result['acquisition_workers']
        stats.update(calls=0, parallel_calls=0, serial_calls=0, helper_jobs=[0,0,0], helper_cpu_ns=[0,0,0])
        result['timing'].update(acquisition_helper_cpu_ns=0, total_dsp_cpu_ns=result['timing']['a_cpu_ns']+result['timing']['b_cpu_ns'])
        self.assertFalse(validate(result, require_complete=False)['execution_validated'])
        with self.assertRaises(ValueError): validate(result)

    def test_pair_compares_regression_evidence_not_worker_counts(self):
        reference, candidate = evidence(1), evidence(4)
        self.assertEqual(compare_acquisition_results(reference, candidate)['status'], 'MATCH')
        candidate['measured']['pcm_s16_hash'] = '1111111111111111'
        self.assertEqual(compare_acquisition_results(reference, candidate)['status'], 'MISMATCH')
        candidate['execution_complete'] = False
        self.assertEqual(compare_acquisition_results(reference, candidate)['status'], 'INCONCLUSIVE')

    def test_pair_schedule_and_reference_must_match(self):
        reference, candidate = evidence(1), evidence(2)
        candidate['warmup_audio_ns'] = 120000000
        self.assertEqual(compare_acquisition_results(reference, candidate)['status'], 'MISMATCH')
        with self.assertRaises(ValueError): compare_acquisition_results(evidence(2), evidence(4))


class RecoveryEvidenceTests(unittest.TestCase):
    def interrupted(self, seconds=29.04):
        result = evidence()
        frames = round(seconds / 0.12)
        result['processed_input_blocks'] = frames
        result['full']['audio_ns'] = round(seconds * 1e9)
        samples = round(seconds * 8000)
        result['acquisition_recovery']['processed_input_samples'] = samples
        result['acquisition_recovery']['windows'] = [
            {'end_input_sample': end, 'sync_blocks_before_next_mute': 10, 'feature_blocks_before_next_mute': 9}
            for end in range(96000, samples + 1, 96000)]
        return result

    def test_two_interruption_windows_are_required_and_compared(self):
        result = self.interrupted()
        self.assertEqual(validate_recovery_evidence(result, workload='interrupt')['completed_mute_windows'], 2)
        result['acquisition_recovery']['windows'][1]['feature_blocks_before_next_mute'] = 0
        with self.assertRaises(ValueError): validate_recovery_evidence(result, workload='interrupt')

    def test_missing_or_corrupt_window_or_duration_rejected(self):
        changes = [lambda r: r['acquisition_recovery']['windows'].pop(),
                   lambda r: r['acquisition_recovery']['windows'][1].update(end_input_sample=96000),
                   lambda r: r['acquisition_recovery'].update(processed_input_samples=24000),
                   lambda r: r['acquisition_recovery'].update(scope='measured_only'),
                   lambda r: r['acquisition_recovery']['windows'][0].update(sync_blocks_before_next_mute=10000)]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                result = self.interrupted(); change(result)
                with self.assertRaises(ValueError): validate_recovery_evidence(result, workload='interrupt')

    def test_boundary_ending_run_does_not_claim_recovery_opportunity(self):
        result = self.interrupted(seconds=12)
        result['acquisition_recovery']['windows'][0].update(sync_blocks_before_next_mute=0, feature_blocks_before_next_mute=0)
        self.assertEqual(validate_recovery_evidence(result, workload='interrupt')['completed_mute_windows'], 1)


if __name__ == '__main__':
    unittest.main()
