#!/usr/bin/env python3
"""Hand-calculated backend-tail contract fixtures; no Pulse or radio access."""
import copy
import unittest

from usb_output_contract import validate_output_contract


def fixture(turns=1):
    identity=dict(session_id='freedv-usb-hand-calculated',build_id='fixture',source_commit='e'*40)
    rows=[dict(kind='session',schema='x6200.usb-audio.v3',usb_output_contract_version=1,**identity)]
    for turn in range(1,turns+1):
        offset=(turn-1)*1000
        # 441 unread mono PCM16 frames take exactly 10000 us at 44100 Hz.
        # 20000 sink + 3000 transport + 10000 unread = 33000 us.
        rows.extend([
            dict(kind='event',event='state',state=3,ms=offset+100),
            dict(kind='event',event='state',state=4,ms=offset+200),
            dict(kind='backend-tail',schema='x6200.usb.playback-tail.v1',turn=turn,
                 drain_started_ms=offset+200,protocol_ack_ms=offset+250,
                 timing_issued_ms=offset+251,timing_issued_us=offset*1000+251200,
                 timing_ack_ms=offset+300,timing_ack_us=offset*1000+300100,
                 write_index_bytes=1082,read_index_bytes=200,sink_usec=20000,transport_usec=3000,
                 configured_sink_usec=100000,protocol_pending_usec=10000,wait_usec=33000,
                 wait_until_us=offset*1000+333101,completed_us=offset*1000+333900,
                 complete=True,rf_tx_confirmed=True,rf_disabled=turn<=3,
                 scope='pulse-reported-backend-tail',**identity),
            dict(kind='event',event='state',state=8,ms=offset+333)])
    final_ms=max(turns,1)*1000
    rows.append(dict(kind='sample',ms=final_ms,state=6,fault=1,turns=turns))
    completion=dict(kind='completion',schema='x6200.usb-completion.v1',usb_output_contract_version=1,
                    terminal=True,trace_complete=True,state=6,fault=1,turns=turns,
                    qualification_turns=min(turns,3),last_sample_ms=final_ms,
                    playback_tail_completed_intervals=turns,playback_tail_interrupted_intervals=0,**identity)
    return rows,completion


class OutputContractTests(unittest.TestCase):
    def check(self, rows, completion):
        return validate_output_contract(rows,completion)

    def test_hand_calculated_backend_components_and_same_millisecond_unkey(self):
        rows,completion=fixture()
        result=self.check(rows,completion)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['completed_turns'],1)
        self.assertEqual(result['receipt_count'],1)
        self.assertIn('physical RF-off timing unmeasured',result['scope'])

    def test_three_qualification_and_two_rf_turns_need_five_receipts(self):
        rows,completion=fixture(5)
        self.assertEqual(self.check(rows,completion)['completed_turns'],5)
        rows=[row for row in rows if not (row.get('kind')=='backend-tail' and row['turn']==2)]
        completion['playback_tail_completed_intervals']=4
        with self.assertRaisesRegex(ValueError,'missing.*completed turn'):
            self.check(rows,completion)

    def test_historical_version_zero_never_qualifies(self):
        result=validate_output_contract([{'kind':'session','schema':'x6200.usb-audio.v3'}],None,0)
        self.assertEqual(result['status'],'DIAGNOSTIC_ONLY')
        self.assertEqual(result['usb_output_contract_version'],0)

    def test_markers_are_type_exact_in_both_header_and_completion(self):
        for target in ('header','completion'):
            for marker in (None,True,'1',0,2):
                rows,completion=fixture()
                record=rows[0] if target=='header' else completion
                if marker is None:record.pop('usb_output_contract_version')
                else:record['usb_output_contract_version']=marker
                with self.subTest(target=target,marker=marker),self.assertRaises(ValueError):
                    self.check(rows,completion)

    def test_typed_receipts_and_identity_cannot_be_replaced_by_success_flags(self):
        mutations=[('schema',None),('schema','x6200.usb.playback-tail.v2'),('kind','flags'),
                   ('session_id','freedv-usb-other'),('build_id','other'),('source_commit','f'*40),
                   ('complete',1),('rf_tx_confirmed',False),('rf_disabled','false'),
                   ('scope','physical RF measured'),('turn',0),('turn',True),('turn',2)]
        for field,value in mutations:
            rows,completion=fixture();rows[3][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                self.check(rows,completion)
        rows,completion=fixture();rows[3]={'kind':'backend-tail','complete':True,'rf_tx_confirmed':True}
        with self.assertRaises(ValueError):self.check(rows,completion)

    def test_receipt_counts_and_completed_turns_are_bound_to_terminal_record(self):
        for field,value in (('turns',0),('turns',True),('playback_tail_completed_intervals',0),
                            ('playback_tail_interrupted_intervals',1),('qualification_turns',2),
                            ('last_sample_ms',999)):
            rows,completion=fixture();completion[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.check(rows,completion)
        rows,completion=fixture();rows.insert(4,copy.deepcopy(rows[3]))
        completion['playback_tail_completed_intervals']=2
        with self.assertRaisesRegex(ValueError,'duplicate'):self.check(rows,completion)

    def test_complete_barrier_rejects_bad_indices_arithmetic_or_stale_timing(self):
        mutations=[('read_index_bytes',-2),('write_index_bytes',1083),('read_index_bytes',True),
                   ('read_index_bytes',1<<63),
                   ('protocol_pending_usec',9999),('wait_usec',33001),('wait_until_us',333100),
                   ('completed_us',333100),('completed_us',334000),('timing_issued_us',250999),
                   ('timing_issued_ms',249),('timing_ack_us',250100),('drain_started_ms',201),
                   ('sink_usec',True),('transport_usec',-1)]
        for field,value in mutations:
            rows,completion=fixture();rows[3][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.check(rows,completion)

    def test_one_unread_frame_rounds_up_to_23_microseconds(self):
        rows,completion=fixture();receipt=rows[3]
        receipt.update(write_index_bytes=202,protocol_pending_usec=23,wait_usec=23023,
                       wait_until_us=323124,completed_us=323124)
        self.assertEqual(self.check(rows,completion)['status'],'PASS')
        receipt['protocol_pending_usec']=22
        with self.assertRaises(ValueError):self.check(rows,completion)

    def test_read_ahead_and_zero_backend_wait_are_valid(self):
        rows,completion=fixture();rows[3].update(read_index_bytes=2000,protocol_pending_usec=0,
            sink_usec=0,transport_usec=0,configured_sink_usec=0,wait_usec=0,
            wait_until_us=300101,completed_us=300101,playing=False)
        self.assertEqual(self.check(rows,completion)['status'],'PASS')

    def test_ack_one_second_and_drain_over_three_seconds_fail(self):
        rows,completion=fixture();rows[3].update(timing_ack_ms=1251,timing_ack_us=1251200,
            wait_until_us=1284201,completed_us=1284201)
        rows[4]['ms']=1284;rows[-1]['ms']=2000;completion['last_sample_ms']=2000
        with self.assertRaisesRegex(ValueError,'one-second'):self.check(rows,completion)
        rows,completion=fixture();rows[3].update(sink_usec=2900000,wait_usec=2913000,
            wait_until_us=3213101,completed_us=3213101)
        rows[4]['ms']=3213;rows[-1]['ms']=4000;completion['last_sample_ms']=4000
        with self.assertRaisesRegex(ValueError,'drain deadline'):self.check(rows,completion)

    def test_missing_normal_drain_or_unkey_cannot_hide_behind_turn_flags(self):
        for state in (3,4,8):
            rows,completion=fixture()
            rows=[row for row in rows if not (row.get('kind')=='event' and row['state']==state)]
            with self.subTest(state=state),self.assertRaises(ValueError):self.check(rows,completion)

    def test_zero_turn_stop_and_fault_require_no_tail_receipt(self):
        for state,fault in ((6,1),(5,9)):
            rows,completion=fixture(0);rows[-1].update(state=state,fault=fault)
            completion.update(state=state,fault=fault)
            self.assertEqual(self.check(rows,completion)['receipt_count'],0)

    def test_qualification_count_can_lag_a_completed_turn_during_loopback_stop(self):
        rows,completion=fixture();completion['qualification_turns']=0
        self.assertTrue(rows[3]['rf_disabled'])
        self.assertEqual(self.check(rows,completion)['completed_turns'],1)

    def test_expected_source_identity_must_match_runtime_not_just_receipts(self):
        rows,completion=fixture()
        expected={field:completion[field] for field in ('session_id','source_commit','build_id')}
        expected['source_commit']='f'*40
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            validate_output_contract(rows,completion,expected_identity=expected)

    def test_interrupted_raw_invalid_timing_is_retained_without_becoming_a_barrier(self):
        rows,completion=fixture()
        rows.pop(4)
        rows[3].update(complete=False,rf_tx_confirmed=False,read_index_bytes=-1,
                       timing_ack_us=0,wait_until_us=0,completed_us=0)
        rows[-1]['turns']=0
        completion.update(turns=0,qualification_turns=0,playback_tail_completed_intervals=0,
                          playback_tail_interrupted_intervals=1)
        result=self.check(rows,completion)
        self.assertEqual(result['completed_turns'],0)
        self.assertEqual(result['interrupted_intervals'],1)
        completion['turns']=1;rows[-1]['turns']=1
        with self.assertRaises(ValueError):self.check(rows,completion)

    def test_stop_after_backend_closure_before_normal_unkey_does_not_add_a_turn(self):
        rows,completion=fixture();rows.pop(4);rows[-1]['turns']=0
        completion.update(turns=0,qualification_turns=0)
        result=self.check(rows,completion)
        self.assertEqual(result['completed_turns'],0)
        self.assertEqual(result['completed_intervals'],1)
        rows.pop(2)
        with self.assertRaises(ValueError):self.check(rows,completion)

    def test_extra_completed_interval_cannot_follow_an_earlier_fault(self):
        rows,completion=fixture();rows.pop(4);rows[-1].update(turns=0,state=5,fault=9)
        rows.insert(4,dict(kind='event',event='first-fault',state=4,ms=332))
        completion.update(turns=0,qualification_turns=0,state=5,fault=9)
        with self.assertRaisesRegex(ValueError,'earlier fault'):self.check(rows,completion)


if __name__=='__main__':unittest.main()
