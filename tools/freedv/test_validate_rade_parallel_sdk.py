#!/usr/bin/env python3
"""Reject incomplete or mislabeled correctness evidence; never tests capacity."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from validate_rade_parallel_sdk import (
    EXPECTED_DIFFERENTIAL_CASES, LIFECYCLE_CASES, RADE_SOURCES, STREAM_CASE_CALLS,
    Runner, compare_stream_rows, json_rows, strict_json, validate_default_off_rows,
    validate_detector_rows, validate_layout_commands, validate_lifecycle_rows,
    validate_stream_rows,
)


def detector_rows(workers=4):
    rows = []
    for index, name in enumerate(sorted(EXPECTED_DIFFERENTIAL_CASES)):
        neon = index < 34
        parallel = neon and workers > 1
        row = dict(case=name, workers=workers, nmf=960, nfreq=40, neon_used=neon,
                   expected_neon=neon, parallel_used=parallel, expected_parallel=parallel,
                   path_expected=True, bit_exact=True, decision_equal=True,
                   grid_bit_differences=0, grid_value_differences=0,
                   max_absolute_grid_difference=0, rms_grid_difference=0)
        for key in ('detection', 't', 'f', 'threshold', 'max'):
            row['reference_' + key] = row['candidate_' + key] = 0
        rows.append(row)
    rows.append(dict(summary=True, workers=workers, radio_qualified=False, cases=47,
                     strict_failures=0, path_failures=0, parallel_cases=34 if workers > 1 else 0,
                     neon_cases=34, scalar_cases=13))
    return rows


def stream_rows(workers=4):
    rows = [dict(case=name, receiver_calls=count, parallel_calls=1 if workers > 1 else 0,
                 feature_calls=3 if name == 'valid-modem-and-recovery' else 0,
                 sync_entries=2 if name == 'valid-modem-and-recovery' else 0,
                 failures=0) for name, count in STREAM_CASE_CALLS.items()]
    rows.append(dict(summary=True, workers=workers, quick=False, radio_qualified=False,
                     receiver_calls=247, search_calls=244, sync_calls=3,
                     parallel_calls=19 if workers > 1 else 0, feature_calls=3,
                     states_seen=7, failures=0))
    return rows


def lifecycle_rows():
    return [dict(case=name, checks=count, failures=0) for name, count in LIFECYCLE_CASES.items()] + [
        dict(summary=True, cases=11, checks=130, failures=0, live_helpers=0, radio_qualified=False)]


def compiler_rows(source):
    rows = []
    for name in sorted(RADE_SOURCES):
        args = ['arm-buildroot-linux-gnueabihf-gcc', '-DRADE_ACQ_PARALLEL=1']
        if name == 'rade_acq.c':
            args.append('-Drade_acq_detect_pilots=rade_acq_detect_pilots_serial')
        args += ['-o', 'CMakeFiles/rade.dir/' + name + '.o', '-c', str(source / name)]
        rows.append(dict(directory=str(source.parent), file=str(source / name), arguments=args))
    return rows


class JsonTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_constants_rejected(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json(text)
        self.assertEqual(json_rows('\n{"x":1}\n'), [{'x': 1}])


class DetectorTests(unittest.TestCase):
    def test_complete_corpus_all_worker_counts(self):
        for workers in (1, 2, 3, 4):
            self.assertEqual(validate_detector_rows(detector_rows(workers), workers=workers)['cases'], 47)

    def test_missing_repeated_or_corrupt_case_rejected(self):
        mutations = [
            lambda r: r.pop(0), lambda r: r.__setitem__(0, copy.deepcopy(r[1])),
            lambda r: r[0].update(workers=2), lambda r: r[0].update(workers=True),
            lambda r: r[0].update(bit_exact=False), lambda r: r[0].update(grid_bit_differences=1),
            lambda r: r[0].update(candidate_t=1), lambda r: r[0].update(nmf=7),
            lambda r: r[0].update(parallel_used=False), lambda r: r[0].update(nfreq=0),
            lambda r: r[-1].update(cases=46), lambda r: r[-1].update(parallel_cases=0),
            lambda r: r[-1].update(radio_qualified=True), lambda r: r[-1].update(neon_cases=33),
        ]
        for change in mutations:
            rows = detector_rows()
            change(rows)
            with self.subTest(rows=rows[0]), self.assertRaises(ValueError):
                validate_detector_rows(rows, workers=4)

    def test_vector_domain_count_cannot_be_self_reported_as_scalar(self):
        rows = detector_rows()
        rows[0].update(neon_used=False, expected_neon=False, parallel_used=False, expected_parallel=False)
        rows[-1].update(neon_cases=33, scalar_cases=14, parallel_cases=33)
        with self.assertRaises(ValueError):
            validate_detector_rows(rows, workers=4)

    def test_group_count_requires_full_serial_fallback(self):
        rows = detector_rows()
        rows[0].update(nmf=8, parallel_used=False, expected_parallel=False)
        rows[-1]['parallel_cases'] -= 1
        validate_detector_rows(rows, workers=4)
        rows[0].update(parallel_used=True, expected_parallel=True)
        rows[-1]['parallel_cases'] += 1
        with self.assertRaises(ValueError):
            validate_detector_rows(rows, workers=4)


class StreamTests(unittest.TestCase):
    def test_complete_corpus_compares_same_target_counts(self):
        reference = stream_rows(1)
        validate_stream_rows(reference, workers=1)
        for workers in (2, 3, 4):
            candidate = stream_rows(workers)
            validate_stream_rows(candidate, workers=workers)
            compare_stream_rows(reference, candidate)
        # Alternative target state counts are permitted when every worker agrees.
        for rows in (reference, candidate):
            rows[-1]['search_calls'] -= 1
            rows[-1]['sync_calls'] += 1
        compare_stream_rows(reference, candidate)

    def test_missing_coverage_or_reacquisition_rejected(self):
        mutations = [lambda r: r.pop(0), lambda r: r[-1].update(quick=True),
                     lambda r: r[-1].update(receiver_calls=246), lambda r: r[-1].update(states_seen=3),
                     lambda r: r[-1].update(parallel_calls=0), lambda r: r[0].update(failures=1),
                     lambda r: r[0].update(receiver_calls=4),
                     lambda r: next(x for x in r if x.get('case') == 'valid-modem-and-recovery').update(sync_entries=1)]
        for change in mutations:
            rows = stream_rows()
            change(rows)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_stream_rows(rows, workers=4)

    def test_weak_case_state_changes_cannot_hide_in_summary(self):
        reference, candidate = stream_rows(1), stream_rows(4)
        next(x for x in candidate if x.get('case') == 'weak-between-bins')['sync_entries'] = 1
        validate_stream_rows(candidate, workers=4)
        with self.assertRaises(ValueError):
            compare_stream_rows(reference, candidate)


class LifecycleTests(unittest.TestCase):
    def test_complete_lifecycle_and_off_apis(self):
        self.assertEqual(validate_lifecycle_rows(lifecycle_rows())['checks'], 130)
        off = [dict(summary=True, experiment_enabled=False, radio_qualified=False, checks=14, failures=0)]
        validate_default_off_rows(off)
        for field, value in [('checks', 13), ('failures', 1), ('experiment_enabled', True), ('radio_qualified', True)]:
            altered = copy.deepcopy(off)
            altered[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_default_off_rows(altered)

    def test_missing_lifecycle_check_or_join_rejected(self):
        for change in [lambda r: r.pop(0), lambda r: r[-1].update(live_helpers=1),
                       lambda r: r[0].update(checks=1), lambda r: r[0].update(failures=1),
                       lambda r: r[-1].update(checks=129)]:
            rows = lifecycle_rows()
            change(rows)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_lifecycle_rows(rows)


class LayoutTests(unittest.TestCase):
    def test_all_library_translation_units(self):
        source = Path('/nonexistent/pinned/rade/src')
        self.assertEqual(set(validate_layout_commands(compiler_rows(source), source)), RADE_SOURCES)

    def test_mixed_layout_test_hooks_or_incorrect_rename_rejected(self):
        source = Path('/nonexistent/pinned/rade/src')
        mutations = [lambda r: r.pop(), lambda r: r.append(copy.deepcopy(r[0])),
                     lambda r: r[0]['arguments'].append('-DRADE_ACQ_PARALLEL=0'),
                     lambda r: r[0]['arguments'].append('-DRADE_ACQ_TEST_HOOKS=1'),
                     lambda r: r[0].update(file=str(source / 'unexpected.c')),
                     lambda r: r[0]['arguments'].remove('-Drade_acq_detect_pilots=rade_acq_detect_pilots_serial'),
                     lambda r: r[1]['arguments'].append('-Drade_acq_detect_pilots=rade_acq_detect_pilots_serial')]
        for change in mutations:
            rows = compiler_rows(source)
            change(rows)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_layout_commands(rows, source)


class TimeoutTests(unittest.TestCase):
    def test_timeout_preserves_partial_bytes_and_text_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            failure = subprocess.TimeoutExpired(['test'], 1, output=b'{"case":"started"}\n', stderr='worker waiting\n')
            with patch('validate_rade_parallel_sdk.subprocess.run', side_effect=failure), self.assertRaisesRegex(RuntimeError, 'timed out'):
                Runner(output)('deadlock-check', ['test'], timeout=1)
            self.assertEqual((output / 'deadlock-check.stdout').read_text(), '{"case":"started"}\n')
            self.assertEqual((output / 'deadlock-check.stderr').read_text(), 'worker waiting\n')


if __name__ == '__main__':
    unittest.main()
