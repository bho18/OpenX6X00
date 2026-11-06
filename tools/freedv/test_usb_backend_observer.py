#!/usr/bin/env python3
"""Independent timing-boundary tests for the host backend observer.

The FIFO fixtures describe a paced software endpoint, never physical hardware.
They deliberately distinguish the last EOO signal from its finite quiet tail.
"""
from array import array
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from usb_test_support import fifo_rf_window,output_alignment_budget,validate_backend_failure


class OutputAlignmentBudgetTest(unittest.TestCase):
    @staticmethod
    def stream(rate=44100,channels=1,backend=410000,ms=10,name='modem'):
        return {'kind':'stream','stream':name,'ms':ms,'rate':rate,'channels':channels,
                'tlength':rate*channels*2*450//1000,'configured_sink_usec':backend,
                'reported_sink_usec':0}

    def test_budget_uses_actual_stream_and_backend_domains_at_both_rates(self):
        for rate in (44100,48000):
            with self.subTest(rate=rate):
                result=output_alignment_budget([self.stream(rate=rate)],{'end_ms':100})
                self.assertEqual(result['stream_target_ms'],450)
                self.assertEqual(result['backend_usec'],410000)
                self.assertEqual(result['maximum_lag_ms'],1360)
                self.assertEqual(sum(result[key] for key in ('io_service_ms','codec_quantum_ms','margin_ms','crop_context_ms')),500)

    def test_channel_width_is_accounted_for(self):
        result=output_alignment_budget([self.stream(channels=2,backend=205000)],{'end_ms':100})
        self.assertEqual(result['stream_target_ms'],450)
        self.assertEqual(result['maximum_lag_ms'],1155)

    def test_same_millisecond_completed_receipt_is_included(self):
        rows=[self.stream(backend=50000),{'kind':'backend-tail','complete':True,
              'completed_us':100999,'sink_usec':900000,'configured_sink_usec':800000}]
        result=output_alignment_budget(rows,{'end_ms':100})
        self.assertEqual(result['backend_usec'],900000)
        self.assertEqual(result['maximum_lag_ms'],1850)

    def test_future_and_interrupted_observations_cannot_inflate_current_budget(self):
        rows=[self.stream(backend=50000),self.stream(backend=2000000,ms=101),
              {'kind':'backend-tail','complete':False,'completed_us':99000,'sink_usec':2000000,'configured_sink_usec':2000000},
              {'kind':'backend-tail','complete':True,'completed_us':101000,'sink_usec':2000000,'configured_sink_usec':2000000}]
        result=output_alignment_budget(rows,{'end_ms':100})
        self.assertEqual(result['backend_usec'],50000)
        self.assertEqual(result['maximum_lag_ms'],1000)

    def test_headset_does_not_borrow_modem_backend_latency(self):
        rows=[self.stream(name='headset',backend=50000),self.stream(backend=900000),
              {'kind':'backend-tail','complete':True,'completed_us':99000,'sink_usec':900000,'configured_sink_usec':900000}]
        self.assertEqual(output_alignment_budget(rows,{'end_ms':100},stream='headset')['maximum_lag_ms'],1000)

    def test_missing_current_descriptor_is_rejected(self):
        with self.assertRaises(ValueError):
            output_alignment_budget([self.stream(ms=101)],{'end_ms':100})


class FifoRfWindowTest(unittest.TestCase):
    origin=1_000_000_000
    rate=44100
    aligned=441

    def fixture(self,*,late_eoo=True):
        signal=array('h',[0]*882)
        signal[10]=1000
        if late_eoo:signal[700]=-1200
        if sys.byteorder!='little':signal.byteswap()
        rows=[{'kind':'session','schema':'x6200.usb.fifo-clock.v1',
               'scope':'paced FIFO backend model; not physical hardware','rate_hz':self.rate,
               'clock_origin_ns':self.origin}]
        for begin,end in ((0,441),(441,882),(882,1323)):
            rows.append({'kind':'block','frame_start':begin,'frame_end':end,
                         'before_ns':self.origin+begin*1_000_000_000//self.rate,
                         'after_ns':self.origin+end*1_000_000_000//self.rate})
        rows.append({'kind':'terminal','status':'STOPPED','exit_code':0,'partial_frame_bytes':0,'frames':1323})
        return rows,signal.tobytes()

    def check(self,rows,signal,*,on=5_000_000,off=35_000_000):
        return fifo_rf_window(rows,signal,self.aligned,self.origin+on,self.origin+off)

    def test_complete_payload_and_quiet_tail_are_inside_rf_window(self):
        result=self.check(*self.fixture())
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(result['all_signal_consumed_while_rf'])
        self.assertTrue(result['finite_deadline_before_rf_off'])
        self.assertEqual(result['quiet_tail_frames'],181)
        self.assertEqual(result['scope'],'paced FIFO backend model; not physical hardware')

    def test_last_eoo_block_after_off_fails_even_if_earlier_speech_fits(self):
        result=self.check(*self.fixture(),off=25_000_000)
        self.assertEqual(result['status'],'FAIL')
        self.assertFalse(result['all_signal_consumed_while_rf'])
        self.assertEqual(result['late_signal_ns'],5_000_000)

    def test_finite_zero_tail_deadline_is_required_even_after_all_signal(self):
        rows,signal=self.fixture(late_eoo=False)
        result=self.check(rows,signal,off=25_000_000)
        self.assertTrue(result['all_signal_consumed_while_rf'])
        self.assertFalse(result['finite_deadline_before_rf_off'])
        self.assertEqual(result['status'],'FAIL')
        deadline=result['finite_last_deadline_ns']-self.origin
        self.assertEqual(self.check(rows,signal,off=deadline)['status'],'PASS')
        self.assertEqual(self.check(rows,signal,off=deadline-1)['status'],'FAIL')

    def test_signal_before_confirmed_on_is_rejected(self):
        result=self.check(*self.fixture(),on=15_000_000)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['early_signal_ns'],5_000_000)

    def test_missing_late_eoo_clock_boundary_is_not_a_complete_recording(self):
        rows,signal=self.fixture();rows=[row for row in rows if not(row.get('kind')=='block' and row['frame_start']==882)]
        with self.assertRaises(ValueError):self.check(rows,signal)

    def test_truncated_or_partial_final_frame_is_rejected(self):
        for field,value in (('frames',1000),('partial_frame_bytes',1)):
            with self.subTest(field=field):
                rows,signal=self.fixture();rows[-1][field]=value
                with self.assertRaises(ValueError):self.check(rows,signal)

    def test_wrong_clock_domain_or_failed_consumer_cannot_prove_rf_timing(self):
        base,signal=self.fixture()
        for changed in ('hardware_scope','wrong_rate','failed_exit'):
            with self.subTest(changed=changed):
                rows=copy.deepcopy(base)
                if changed=='hardware_scope':rows[0]['scope']='physical hardware'
                elif changed=='wrong_rate':rows[0]['rate_hz']=48000
                else:rows[-1]['exit_code']=1
                with self.assertRaises(ValueError):self.check(rows,signal)

    def test_silence_or_negative_alignment_is_not_a_signal_proof(self):
        rows,signal=self.fixture()
        with self.assertRaises(ValueError):self.check(rows,bytes(len(signal)))
        with self.assertRaises(ValueError):fifo_rf_window(rows,signal,-1,self.origin,self.origin+40_000_000)


class BackendFailureEvidenceTest(unittest.TestCase):
    variants={'missing':(14,'modem-backend-timing',2001),
              'invalid':(9,'modem-backend-timing-invalid',1003),
              'stop-before-timing':(1,'local-stop',1003),
              'stop-during-wait':(1,'local-stop',1100)}

    def fixture(self,mode):
        fault,origin,when=self.variants[mode]
        rows=[{'kind':'event','event':'first-fault','ms':when,'state':4,'status':fault,'operation':origin},
              {'kind':'backend-tail','complete':False,'rf_tx_confirmed':False,'protocol_ack_ms':1000},
              {'kind':'sample','ms':when+30,'state':6 if fault==1 else 5,
               'rf_valid':True,'rf_tx':False,'rf_clear_confirmed':True}]
        harness={'fault':fault,'first_fault_origin':origin,'turns':0,'eoo':1,'keys':1,
                 'physical_tx':False,'rf_clear_confirmed':True}
        text=('INJECT backend timing requested_ms=1001 mode='+mode+' phase=drain\n'+
              'INJECT backend timing mode='+mode+' real_ack_ms=1002 success=1 withheld='+str(int(mode=='missing'))+' phase=drain\n')
        if mode=='invalid':text+='INJECT backend timing invalid write_index_bytes=-1 phase=drain\n'
        if mode=='stop-before-timing':
            # The producer logs this after Stop returns, so the fault event
            # may precede that receipt by a millisecond without violating causality.
            text+='INJECT backend timing stop-before-forward stop_ms=1004 action_invoked=1 phase=drain\n'
        if mode=='stop-during-wait':
            text+='BACKEND_STOP_DURING_WAIT request_before_ns=1100000000 request_after_ns=1101000000 wait_started_ms=1050 wait_until_us=1500000\n'
        return rows,harness,text

    def test_all_four_interruption_variants_preserve_their_actual_fault_and_eoo(self):
        for mode in self.variants:
            with self.subTest(mode=mode):
                result=validate_backend_failure(*self.fixture(mode),mode)
                self.assertEqual(result['status'],'PASS')
                self.assertEqual(result['completed_rf_turns'],0)
                self.assertEqual(result['eoo'],1)

    def test_actual_ack_witness_is_required_even_when_forwarding_is_withheld(self):
        rows,harness,text=self.fixture('missing')
        text='\n'.join(line for line in text.splitlines() if 'real_ack_ms' not in line)
        with self.assertRaises(ValueError):validate_backend_failure(rows,harness,text,'missing')

    def test_wrong_phase_or_no_real_request_is_rejected(self):
        for change in ('state','phase','request'):
            with self.subTest(change=change):
                rows,harness,text=self.fixture('invalid')
                if change=='state':rows[0]['state']=3
                elif change=='phase':text=text.replace('phase=drain','phase=initial-rx')
                else:text='\n'.join(line for line in text.splitlines() if 'requested_ms' not in line)
                with self.assertRaises(ValueError):validate_backend_failure(rows,harness,text,'invalid')

    def test_pre_eoo_failure_or_falsely_completed_turn_cannot_credit_backend_interruption(self):
        for field,value in (('eoo',0),('turns',1),('keys',0),('physical_tx',True)):
            with self.subTest(field=field):
                rows,harness,text=self.fixture('invalid');harness[field]=value
                with self.assertRaises(ValueError):validate_backend_failure(rows,harness,text,'invalid')

    def test_stale_or_unconfirmed_rx_is_not_reported_as_confirmed(self):
        for field,value in (('rf_valid',False),('rf_tx',True),('rf_clear_confirmed',False)):
            with self.subTest(field=field):
                rows,harness,text=self.fixture('invalid')
                rows[-1][field]=value
                if field=='rf_clear_confirmed':harness[field]=False
                with self.assertRaises(ValueError):validate_backend_failure(rows,harness,text,'invalid')

    def test_missing_ack_timeout_cannot_be_credited_before_the_original_one_second_deadline(self):
        rows,harness,text=self.fixture('missing');rows[0]['ms']=2000
        with self.assertRaises(ValueError):validate_backend_failure(rows,harness,text,'missing')

    def test_stop_after_call_receipt_may_follow_the_fault_but_cannot_precede_it(self):
        rows,harness,text=self.fixture('stop-before-timing')
        self.assertEqual(validate_backend_failure(rows,harness,text,'stop-before-timing')['status'],'PASS')
        with self.assertRaises(ValueError):
            validate_backend_failure(rows,harness,text.replace('stop_ms=1004','stop_ms=1002'),'stop-before-timing')

    def test_stop_cannot_wait_one_hundred_ms_or_until_the_backend_deadline(self):
        rows,harness,text=self.fixture('stop-during-wait')
        for replacement in ('request_after_ns=1200000000','request_after_ns=1500000000'):
            with self.subTest(replacement=replacement):
                with self.assertRaises(ValueError):
                    validate_backend_failure(rows,harness,text.replace('request_after_ns=1101000000',replacement),'stop-during-wait')

    def test_a_wait_that_has_not_started_cannot_credit_stop_during_wait(self):
        rows,harness,text=self.fixture('stop-during-wait')
        with self.assertRaises(ValueError):
            validate_backend_failure(rows,harness,text.replace('wait_started_ms=1050','wait_started_ms=1101'),'stop-during-wait')


if __name__=='__main__':unittest.main()
