#!/usr/bin/env python3
"""Hand-calculated opening/frontier evidence; no Pulse, codec or RF access."""
import copy
import unittest

from usb_capture_open_contract import validate_capture_open_contract


def fixture(turns=1):
    identity = dict(session_id='capture-frontier-fixture', build_id='fixture', source_commit='e' * 40)
    rows = [dict(kind='session', schema='x6200.usb-audio.v4', operational=True,
                 usb_capture_open_contract_version=1, trace_pcm_seconds=20, **identity),
            dict(kind='stream', stream='mic', rate=48000, channels=1, fragsize=1920)]
    for turn in range(1, turns + 1):
        epoch, start = (turn - 1) * 2000, (turn - 1) * 100000 + 10000
        # 10000 source + 1000 transport usec produce exactly 528 frames.
        opening = dict(kind='capture-open', schema='x6200.usb.capture-open.v1', turn=turn,
                       complete=True, canceled=False, generation=turn * 2,
                       index_gap=False, read_index_corrupt=False, write_index_corrupt=False,
                       begin_requested_ms=epoch + 100, timing_issued_ms=epoch + 101,
                       frozen_ms=epoch + 102, reached_ms=epoch + 120, opened_ms=epoch + 120,
                       capture_started_ms=epoch + 102, begin_read_index=start - 1000,
                       read_index_bytes=(start - 1000) * 2, write_index_bytes=(start - 528) * 2,
                       source_usec=10000, transport_usec=1000, sink_usec=20000,
                       frontier_extra_frames=528, start_index=start, discarded_prefix_frames=1000,
                       first_submitted_index=start, open_timeout_ms=750,
                       scope='pulse-capture-production-frontier', **identity)
        cutoff = start + 48000
        close = dict(kind='capture-close', turn=turn, generation=turn * 2, start_index=start, cutoff_index=cutoff,
                     consumed_index=cutoff, submitted_first_index=start, submitted_end_index=cutoff,
                     mic_tap_start=(turn - 1) * 48000, mic_tap_end=turn * 48000,
                     capture_started_ms=epoch + 102, release_fence_index=cutoff - 480,
                     close_started_ms=epoch + 1102, timing_issued_ms=epoch + 1103,
                     frozen_ms=epoch + 1105, reached_ms=epoch + 1120, closed_ms=epoch + 1121,
                     complete=True, index_gap=False, tail_frames=480,
                     write_index_bytes=(cutoff - 528) * 2, source_usec=10000,
                     transport_usec=1000, sink_usec=20000, frontier_extra_frames=528,
                     cutoff_split_frames=0, close_timeout_ms=750, scope='pulse-capture-pipeline-frontier')
        accounting = dict(kind='source-accounting', turn=turn, ms=epoch + 1122,
                          active_ms=1000, accepted_mic_frames=48000, expected_mic_frames=48000,
                          required_mic_frames=45600, boundary_frames=1920,
                          delivery_tolerance_percent=1, evaluated=True, passed=True)
        rows.extend([opening, dict(kind='event', event='state', state=3, ms=epoch + 120),
                     dict(kind='event', event='state', state=4, ms=epoch + 1102), close,
                     accounting, dict(kind='event', event='state', state=8, ms=epoch + 1300)])
    final_ms = max(turns, 1) * 2000
    rows.append(dict(kind='sample', state=6, fault=1, turns=turns, ms=final_ms))
    completion = dict(kind='completion', schema='x6200.usb-completion.v1', terminal=True,
                      trace_complete=True, state=6, fault=1, turns=turns, last_sample_ms=final_ms,
                      usb_capture_open_contract_version=1, capture_open_completed_intervals=turns,
                      capture_open_interrupted_intervals=0, **identity)
    return rows, completion


def record(rows, kind, turn=1):
    return next(row for row in rows if row.get('kind') == kind and row.get('turn') == turn)


class CaptureOpenContractTests(unittest.TestCase):
    def check(self, rows, completion):
        return validate_capture_open_contract(rows, completion)

    def test_exact_frontier_closure_and_two_turns(self):
        rows, completion = fixture(2)
        result = self.check(rows, completion)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['completed_turns'], 2)
        self.assertEqual(result['completed_openings'], 2)
        self.assertEqual(result['completed_intervals'], 2)
        self.assertEqual(result['intervals'][0]['accepted_frames'], 48000)
        self.assertIn('physical microphone/PTT latency unmeasured', result['scope'])

    def test_sixty_ms_preopen_prefix_is_excluded_exactly(self):
        rows, completion = fixture()
        opening = record(rows, 'capture-open')
        opening['begin_read_index'] = opening['start_index'] - 2880
        opening['read_index_bytes'] = opening['begin_read_index'] * 2
        opening['discarded_prefix_frames'] = 2880
        self.assertEqual(self.check(rows, completion)['completed_turns'], 1)
        record(rows, 'capture-close')['submitted_first_index'] -= 2880
        with self.assertRaisesRegex(ValueError, 'exact admitted source interval'):
            self.check(rows, completion)

    def test_historical_v3_v4_require_explicit_marker_and_zero_never_qualifies(self):
        for schema in ('x6200.usb-audio.v3', 'x6200.usb-audio.v4'):
            rows, completion = fixture(); rows[0]['schema'] = schema
            self.assertEqual(self.check(rows, completion)['status'], 'PASS')
            rows[0].pop('usb_capture_open_contract_version')
            with self.assertRaises(ValueError): self.check(rows, completion)
        result = validate_capture_open_contract(None, None, required_version=0)
        self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY')
        self.assertEqual(result['usb_capture_open_contract_version'], 0)

    def test_marker_and_counters_are_type_exact(self):
        for target in ('session', 'completion'):
            for value in (None, True, '1', 0, 2):
                rows, completion = fixture(); destination = rows[0] if target == 'session' else completion
                if value is None: destination.pop('usb_capture_open_contract_version')
                else: destination['usb_capture_open_contract_version'] = value
                with self.subTest(target=target, value=value), self.assertRaises(ValueError):
                    self.check(rows, completion)
        for key, value in (('capture_open_completed_intervals', 0), ('capture_open_completed_intervals', True),
                           ('capture_open_interrupted_intervals', 1)):
            rows, completion = fixture(); completion[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.check(rows, completion)

    def test_missing_duplicate_reordered_and_wrong_identity_receipts(self):
        for kind in ('capture-open', 'capture-close', 'source-accounting'):
            rows, completion = fixture(); rows.remove(record(rows, kind))
            with self.subTest(kind=kind, action='missing'), self.assertRaises(ValueError): self.check(rows, completion)
            rows, completion = fixture(); rows.insert(2, copy.deepcopy(record(rows, kind)))
            with self.subTest(kind=kind, action='duplicate'), self.assertRaises(ValueError): self.check(rows, completion)
        for field in ('session_id', 'build_id', 'source_commit'):
            rows, completion = fixture(); record(rows, 'capture-open')[field] = 'another-session'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.check(rows, completion)
        rows, completion = fixture(); opening = record(rows, 'capture-open'); rows.remove(opening); rows.insert(5, opening)
        with self.assertRaises(ValueError): self.check(rows, completion)
        rows, completion = fixture(); account = record(rows, 'source-accounting'); rows.remove(account); rows.insert(4, account)
        with self.assertRaises(ValueError): self.check(rows, completion)

    def test_corruption_gap_frontier_and_epoch_cannot_be_relabelled_success(self):
        changes = [('schema', 'x6200.usb.capture-open.v2'), ('kind', 'flag'), ('complete', 1),
                   ('read_index_corrupt', True), ('write_index_corrupt', True), ('index_gap', True),
                   ('generation', 0), ('canceled', True),
                   ('read_index_bytes', -2), ('read_index_bytes', 18001), ('write_index_bytes', -2),
                   ('write_index_bytes', 18945), ('start_index', 10001), ('first_submitted_index', 9999),
                   ('discarded_prefix_frames', 999), ('frontier_extra_frames', 527),
                   ('capture_started_ms', 120), ('source_usec', 750001), ('open_timeout_ms', 751)]
        for field, value in changes:
            rows, completion = fixture(); record(rows, 'capture-open')[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.check(rows, completion)

    def test_sink_latency_is_retained_but_never_subtracted(self):
        rows, completion = fixture()
        record(rows, 'capture-open')['sink_usec'] = 999999999
        record(rows, 'capture-close')['sink_usec'] = 999999999
        self.assertEqual(self.check(rows, completion)['status'], 'PASS')
        record(rows, 'capture-open')['start_index'] -= 480
        with self.assertRaisesRegex(ValueError, 'raw capture production frontier'): self.check(rows, completion)

    def test_original_750ms_deadlines_remain(self):
        for kind, key, value in (('capture-open', 'reached_ms', 851),
                                 ('capture-open', 'frozen_ms', 851),
                                 ('capture-close', 'closed_ms', 1853),
                                 ('capture-close', 'source_usec', 750001)):
            rows, completion = fixture(); record(rows, kind)[key] = value
            with self.subTest(kind=kind, key=key), self.assertRaises(ValueError): self.check(rows, completion)

    def test_source_conservation_and_budget_cannot_be_weakened(self):
        for kind, key, value in (('capture-close', 'start_index', 9999),
                                 ('capture-close', 'submitted_end_index', 57999),
                                 ('capture-close', 'consumed_index', 57999),
                                 ('capture-close', 'mic_tap_end', 48001),
                                 ('source-accounting', 'accepted_mic_frames', 47999),
                                 ('source-accounting', 'expected_mic_frames', 47999),
                                 ('source-accounting', 'required_mic_frames', 45000),
                                 ('source-accounting', 'delivery_tolerance_percent', 2),
                                 ('source-accounting', 'evaluated', False),
                                 ('source-accounting', 'passed', 1)):
            rows, completion = fixture(); record(rows, kind)[key] = value
            with self.subTest(kind=kind, key=key), self.assertRaises(ValueError): self.check(rows, completion)

    def test_interrupted_open_preserves_raw_invalid_indices_without_admitting_turn(self):
        rows, completion = fixture()
        opening = record(rows, 'capture-open')
        opening.update(complete=False, read_index_corrupt=True, read_index_bytes=-2,
                       frozen_ms=0, reached_ms=0, opened_ms=0, capture_started_ms=0)
        rows = rows[:3] + [dict(kind='sample', state=5, fault=23, turns=0, ms=2000)]
        completion.update(state=5, fault=23, turns=0, capture_open_completed_intervals=0,
                          capture_open_interrupted_intervals=1)
        result = self.check(rows, completion)
        self.assertEqual(result['completed_turns'], 0)
        self.assertEqual(result['completed_openings'], 0)
        self.assertEqual(result['interrupted_openings'], 1)
        self.assertEqual(result['completed_intervals'], 0)
        rows.insert(3, dict(kind='event', event='state', state=3, ms=500))
        with self.assertRaisesRegex(ValueError, 'TX attempts'): self.check(rows, completion)

    def test_stop_after_complete_open_or_capture_does_not_invent_rf_turn(self):
        rows, completion = fixture(); rows = rows[:4] + [rows[-1]]
        rows[-1]['turns'] = 0; completion['turns'] = 0
        self.assertEqual(self.check(rows, completion)['completed_turns'], 0)
        rows, completion = fixture(); rows.pop(-2)
        rows[-1]['turns'] = 0; completion['turns'] = 0
        result = self.check(rows, completion)
        self.assertEqual(result['completed_turns'], 0)
        self.assertEqual(result['completed_intervals'], 1)

    def test_capture_pcm_tap_may_be_absent_or_bounded_without_changing_wire_conservation(self):
        for retained in (0, 12000):
            rows, completion = fixture(); rows[0]['trace_pcm_seconds'] = 0
            record(rows, 'capture-close')['mic_tap_end'] = retained
            self.assertEqual(self.check(rows, completion)['intervals'][0]['accepted_frames'], 48000)
        rows, completion = fixture(); close = record(rows, 'capture-close')
        close.pop('mic_tap_start'); close.pop('mic_tap_end')
        self.assertEqual(self.check(rows, completion)['status'], 'PASS')

    def test_cancelled_attempt_can_precede_unique_complete_admission_at_same_turn(self):
        rows, completion = fixture()
        cancelled = copy.deepcopy(record(rows, 'capture-open'))
        cancelled.update(complete=False, canceled=True, generation=1, begin_requested_ms=50, timing_issued_ms=51,
                         frozen_ms=0, reached_ms=0, opened_ms=0, capture_started_ms=0)
        rows.insert(2, cancelled); completion['capture_open_interrupted_intervals'] = 1
        result = self.check(rows, completion)
        self.assertEqual(result['receipt_count'], 2)
        self.assertEqual(result['completed_openings'], 1)
        self.assertEqual(result['interrupted_openings'], 1)
        self.assertEqual(result['completed_turns'], 1)
        rows.remove(cancelled); rows.insert(4, cancelled)
        with self.assertRaises(ValueError): self.check(rows, completion)

    def test_coordinator_pickup_after_timely_reached_frontier_does_not_move_source_epoch(self):
        rows, completion = fixture(); opening = record(rows, 'capture-open')
        opening['opened_ms'] = 851
        next(row for row in rows if row.get('event') == 'state' and row['state'] == 3)['ms'] = 851
        self.assertEqual(self.check(rows, completion)['intervals'][0]['capture_started_ms'], 102)

    def test_close_generation_cannot_be_substituted_or_omitted(self):
        for value in (None, True, 1, 3):
            rows, completion = fixture(); close = record(rows, 'capture-close')
            if value is None: close.pop('generation')
            else: close['generation'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.check(rows, completion)

    def test_opening_raw_frontier_cannot_exceed_bounded_callback_cursor_horizon(self):
        rows, completion = fixture(); opening = record(rows, 'capture-open')
        start = opening['read_index_bytes'] // 2 + 36001
        opening.update(start_index=start, first_submitted_index=start,
                       write_index_bytes=(start - 528) * 2,
                       discarded_prefix_frames=start - opening['begin_read_index'])
        with self.assertRaisesRegex(ValueError, 'unread production horizon'):
            self.check(rows, completion)


if __name__ == '__main__':
    unittest.main()
