#!/usr/bin/env python3
"""Prevent execution success or rounded measurements from hiding timing failures."""
import unittest

from run_benchmarks import assess_timing, timing_summary


class TimingAssessmentTest(unittest.TestCase):
    def test_first_radio_results(self):
        # Scalar observations from the first operator-returned result bundle.
        observations = [(0.238157, 0, 'WITHIN_LIMITS'), (0.208744, 0, 'WITHIN_LIMITS'),
                        (0.292924, 0, 'WITHIN_LIMITS'), (0.700613, 0, 'FAIL'),
                        (1.311725, 25, 'FAIL'), (1.540519, 25, 'FAIL'), (1.541795, 117, 'FAIL')]
        cases = []
        for ratio, misses, expected in observations:
            with self.subTest(ratio=ratio):
                timing = assess_timing({'p99_frame_ratio': ratio, 'deadline_misses': misses})
                self.assertEqual(timing['status'], expected)
                self.assertFalse(timing['full_qualification'])
                cases.append({'validated': True, 'return_code': 0, 'timing': timing})
        self.assertEqual(timing_summary(cases, execution_complete=True), 'FAIL')

    def test_exact_boundary_and_deadline_review(self):
        self.assertEqual(assess_timing({'p99_frame_ratio': 0.70, 'deadline_misses': 0})['status'], 'WITHIN_LIMITS')
        self.assertEqual(assess_timing({'p99_frame_ratio': 0.70, 'deadline_misses': 1})['status'], 'REVIEW')

    def test_missing_or_invalid_measurements(self):
        invalid = [None, -1, float('nan'), float('inf'), True, '0.2']
        for ratio in invalid:
            self.assertEqual(assess_timing({'p99_frame_ratio': ratio, 'deadline_misses': 0})['status'], 'INCONCLUSIVE')
        for misses in [None, -1, 0.5, False, '0']:
            self.assertEqual(assess_timing({'p99_frame_ratio': 0.2, 'deadline_misses': misses})['status'], 'INCONCLUSIVE')

    def test_missing_cases_cannot_produce_within_limits(self):
        within = {'timing': {'status': 'WITHIN_LIMITS'}}
        self.assertEqual(timing_summary([within], execution_complete=False), 'INCONCLUSIVE')
        self.assertEqual(timing_summary([within, {}], execution_complete=True), 'INCONCLUSIVE')
        self.assertEqual(timing_summary([], execution_complete=True), 'INCONCLUSIVE')
        self.assertEqual(timing_summary([within], execution_complete=True), 'WITHIN_LIMITS')


if __name__ == '__main__':
    unittest.main()
