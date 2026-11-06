#!/usr/bin/env python3
"""Qualification-history and matrix-failure regressions; no audio/radio needed."""
import copy
import hashlib
from array import array
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import test_usb_audio_matrix as matrix
import build_native_development as builder
from usb_test_campaign import (Campaign, MATRIX_SCHEMA, atomic_json, digest,
                               immutable_json, sha, validate_campaign, INFRASTRUCTURE_PREREQUISITES, INFRASTRUCTURE_CHECKS, validate_tail_backpressure_run, validate_capture_close_run,
                               validate_capture_source_boundary_run, validate_capture_close_failure_run,
                               host_resource_configuration, validate_host_resources, PRESSURE_COUNTERS)


def resource_fixture(root,identity,configuration=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    config=configuration or identity.get('host_resource_configuration') or host_resource_configuration()
    def snapshot(current):
        limit=16*1024**3;head=limit-current
        return {'status':'KNOWN_BOUNDED','errors':[],'cgroup_path':'/sys/fs/cgroup/unit-fixture',
            'headroom_bytes':head,'memory':{'current_bytes':current,'max_bytes':limit,'high_bytes':None,
                'effective_limit_bytes':limit,'headroom_bytes':head,
                'events':{'high':3,'max':7,'oom':0,'oom_kill':0},
                'stat':{'anon':50*1024**2,'file':5*1024**3,'shmem':0,'pgscan_direct':11,'pgsteal_direct':9}},
            'cpu':{'quota_us':400000,'period_us':100000,'quota_cores':4.,'affinity_cores':4,
                   'stat':{'usage_usec':100,'nr_throttled':2,'throttled_usec':10}}}
    qualified=config['profile']=='qualification'
    pre={'schema':'x6200.usb.host-resources.v1','phase':'preflight','profile':config['profile'],
         'configuration':config,'status':'PASS' if qualified else 'RECORDED_NONPROMOTABLE','assessment':'PASS',
         'promotion_eligible':qualified,'min_headroom_bytes':config['min_headroom_bytes'],
         'min_end_headroom_bytes':config['min_end_headroom_bytes'],'pressure_counter_policy':list(PRESSURE_COUNTERS),
         'before':snapshot(10*1024**3),'after':snapshot(6*1024**3),'headroom_bytes':10*1024**3,
         'reclaim':{'status':'COMPLETE' if config['reclaim_roots'] else 'NOT_REQUESTED',
                    'roots':config['reclaim_roots'],'rejected_roots':[],'timed_out':False,'contents_modified':False,
                    'worker_still_running':False},'reasons':[]}
    end={'schema':'x6200.usb.host-resources.v1','phase':'end','profile':config['profile'],'configuration':config,
         'status':'PASS' if qualified else 'RECORDED_NONPROMOTABLE','assessment':'PASS','promotion_eligible':qualified,
         'min_headroom_bytes':config['min_end_headroom_bytes'],'before':pre['after'],'after':snapshot(7*1024**3),
         'headroom_bytes':9*1024**3,'counter_deltas':{key:0 for key in PRESSURE_COUNTERS},
         'new_memory_pressure':{},'reasons':[],'cpu_throttling_is_diagnostic_only':True}
    pp=root/'host-resource-preflight.json';ep=root/'host-resource-end.json';atomic_json(pp,pre);atomic_json(ep,end)
    return {'schema':'x6200.usb.host-resource-contract.v1','status':'PASS' if qualified else 'RECORDED_NONPROMOTABLE',
            'configuration':config,'preflight':pre,'end':end,'preflight_report_path':str(pp),'preflight_report_sha256':sha(pp),
            'end_report_path':str(ep),'end_report_sha256':sha(ep),
            **{key:identity[key] for key in ('source_commit','source_tree_sha256','build_id','test_binary_sha256')}}


def infrastructure_fixture(root, identity, injector_sha):
    """Synthetic metadata for gate unit negatives; never actual C evidence."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    shared={'radio_accessed':False,'source_commit':identity['source_commit'],'build_id':identity['build_id'],
            'controller_binary_sha256':identity['test_binary_sha256'],'runner_sha256':identity['runner_sha256'],
            'fixture_sha256':{'speech':identity['speech_sha256'],'modem':identity['modem_sha256']},
            'fault_injector_sha256':injector_sha}
    variants={}
    for name,case,code,origin,disabled in (('initial_buffer','normal',13,'modem-buffer-budget',False),
                                          ('qualification_telemetry','rf-disabled',4,'radio-packet-stale',True)):
        directory=root/name;directory.mkdir()
        bodies={
            'case_result':{'status':'FAIL','case':case,'controller_exit_code':2},
            'harness_failure':{'status':'FAIL','exit_code':2,'primary_fault':code,'primary_origin':origin,
                               'cleanup_requested':True,'stopped':True,'closed':True,'rf_disabled':disabled,'keys':0},
            'completion':{'terminal':True,'fault':code,'trace_complete':True,'rf_clear_confirmed':True,
                          'rf_disabled':disabled,'qualification_complete':False},
            'collector_report':{'status':'BLOCKED','ready_for_rf_test':False},
            'first_fault':{'first_fault':{'code':code,'origin':origin}},
        }
        rows=[{'kind':'sample','state':5,'fault':code}]
        q=None
        if disabled:
            q={'qualification_started_ms':1000,'freeze_started_ms':1100,'packet_ms':1090,'sequence':7,
               'freeze_duration_ms':350,'fault_ms':1341,'recovered_ms':1450,'qualification_complete':False,'keys':0}
            bodies['injection_witness']={'kind':'test-telemetry-freeze','qualification_running':True,'keys':0,
                'duration_ms':350,'ms':1100,'packet_ms':1090,'sequence':7}
            rows=[{'kind':'event','event':'qualification-start','ms':1000},
                  {'kind':'sample','qualification_running':True,'rf_disabled':True,'ms':1100},
                  {'kind':'event','event':'first-fault','status':4,'operation':origin,'ms':1341}]
        files={}
        for filename,body in bodies.items():
            path=directory/(filename+'.json');atomic_json(path,body)
            files[filename]={'path':str(path),'sha256':sha(path)}
        trace=directory/'trace.jsonl';trace.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        files['trace']={'path':str(trace),'sha256':sha(trace)}
        variant={'schema':'x6200.usb.infrastructure.v1','kind':'unexpected-runtime-failure','status':'PASS',
                 **shared,'variant':name,'driver_case':case,'rf_disabled':disabled,'keys':0,
                 'checks':{key:True for key in INFRASTRUCTURE_CHECKS},'driver_exit_code':1,'driver_status':'FAIL',
                 'harness_exit_code':2,'harness_status':'FAIL','terminal_fault_code':code,
                 'terminal_fault_origin':origin,'collector_status':'BLOCKED','evidence_files':files}
        if q:variant['qualification_failure']=q
        variants[name]=variant;atomic_json(directory/'variant-result.json',variant)
    proof={'schema':'x6200.usb.infrastructure.v2','kind':'unexpected-runtime-failures','status':'PASS',
           **shared,'checks':{key:True for key in INFRASTRUCTURE_CHECKS},'variants':variants}
    report=root/'infrastructure-result.json';atomic_json(report,proof)
    log=root/'infrastructure.log';log.write_text('Expected failed children preserved; unit fixture only\n')
    return {'status':'PASS','record':proof,'record_sha256':digest(proof),'report_path':str(report),
            'report_sha256':sha(report),'log_path':str(log),'log_sha256':sha(log),
            'supervision':{'exit_code':0,'timed_out':False}}


def capture_close_fixture(completed_rf_turns=0, extra_failed=False):
    """Synthetic finite-capture metadata for the release-gate unit tests."""
    rows=[]
    count=completed_rf_turns+int(extra_failed)
    for number in range(1,count+1):
        start=1000+(number-1)*144000;cutoff=start+96000
        rows.append({'status':'PASS','turn':number,'trace_file':'freedv-usb-session-unit.jsonl',
            'rf_completed':number<=completed_rf_turns,'start_index':start,'cutoff_index':cutoff,
            'consumed_index':cutoff,'accepted_frames':96000,'mic_tap_start':(number-1)*96000,'mic_tap_end':number*96000,
            'close_elapsed_ms':150,'write_index_bytes':(cutoff-96)*2,'source_usec':1000,'transport_usec':1000,
            'sink_usec':10000,'frontier_extra_frames':96,'release_fence_index':cutoff-100,'tail_frames':100,
            'cutoff_split_frames':200,'capture_started_ms':1000,'close_started_ms':3000,'timing_issued_ms':3000,
            'frozen_ms':3001,'reached_ms':3100,'closed_ms':3150,'frontier_basis':'Pulse capture production; no monitor playback correction',
            'physical_latency':'UNMEASURED','microphone_frame_budget':{'status':'PASS','stream':'microphone',
                'active_ms':2000,'accepted_frames':96000,'expected_frames':96000.,'minimum_frames':93120,
                'maximum_frames':98880,'fragment_frames':960,'boundary_frames':1920,
                'delivery_tolerance_percent':1,'evaluated':True}})
    return {'status':'PASS','deadline_ms':750,'frontier_basis':'Pulse capture production; no monitor playback correction',
            'physical_latency':'UNMEASURED','turns':rows,'completed_rf_turns':completed_rf_turns,
            'completed_intervals':len(rows),'abandoned_intervals':0}


def capture_source_boundary_fixture(case,root):
    root=Path(root);result=root/'result';result.mkdir(parents=True,exist_ok=True)
    close=capture_close_fixture(1);interval=close['turns'][0]
    accepted=array('h',((index%30000)-15000 for index in range(96000)))
    if sys.byteorder!='little':accepted.byteswap()
    accepted_pcm=accepted.tobytes();source_pcm=accepted_pcm+(30001).to_bytes(2,'little',signed=True)*200
    pcm=result/'mic-observer.pcm';pcm.write_bytes(source_pcm)
    rows=[];cursor=interval['start_index'];tap=0
    while tap<95040:
        rows.append({'valid':True,'index_start':cursor,'index_end':cursor+960,'tap_start':tap,'tap_frames':960})
        cursor+=960;tap+=960
    rows.append({'valid':True,'index_start':cursor,'index_end':interval['cutoff_index']+200,
                 'tap_start':tap,'tap_frames':1160})
    index=result/'mic-observer.jsonl';index.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (result/'freedv-usb-session-unit-mic.raw').write_bytes(accepted_pcm)
    mode='held150' if case.endswith('held150') else 'split'
    exercise={'hold_started_ms':2950 if mode=='held150' else 2800,
              'hold_ended_ms':3110 if mode=='held150' else 2950,
              'callback_started_ms':3110 if mode=='held150' else 2950,'barrier_started_ms':0 if mode=='held150' else 2999,
              'release_observed_ms':3000,'callback_ended_ms':3110 if mode=='held150' else 3001,
              'blocked_peeks':1 if mode=='held150' else 0,'cutoff_index':97000,'sentinel_frames':200}
    boundary={'status':'PASS','turn':1,'compared_frames':96000,'accepted_frames':96000,
              'expected_source_sha256':hashlib.sha256(accepted_pcm).hexdigest(),
              'accepted_sha256':hashlib.sha256(accepted_pcm).hexdigest(),'post_cutoff_frames_observed':200,
              'split_block':{'start_index':96040,'end_index':97200,'cutoff_index':97000},
              'post_cutoff_excluded':True,'mode':mode,'exercise':exercise,'sentinel_value':30001,
              'post_cutoff_sentinel_frames':200,'post_cutoff_sentinel_excluded':True}
    return {'harness':{'turns':1,'total_turns':1,'eoo':1,'fault':1,'keys':1,'physical_tx':False,'rf_clear_confirmed':True},
            'capture_close':close,'capture_source_boundary':{'status':'PASS','case':case,'turns':[boundary],
                'observer_pcm_sha256':sha(pcm),'observer_index_sha256':sha(index),'physical_latency':'UNMEASURED'}}


def capture_close_failure_fixture(case,root):
    from usb_test_support import validate_capture_close_failure
    root=Path(root);result=root/'result';result.mkdir(parents=True,exist_ok=True)
    mode=case.removeprefix('capture-close-')
    fault,origin=(9,'modem-payload-underflow') if mode=='missing' else ((23,'microphone-close-frontier-invalid') if mode=='invalid' else (1,'local-stop'))
    fault_ms=1424 if mode=='missing' else 1002
    abandoned={'kind':'capture-close','turn':1,'complete':False,'close_timeout_ms':750,
               'close_started_ms':1000,'timing_issued_ms':1001,'frozen_ms':0,'cutoff_index':0,
               'scope':'pulse-capture-pipeline-frontier'}
    rows=[{'kind':'event','event':'capture-close-start','state':4,'ms':1000},
          {'kind':'event','event':'first-fault','state':4,'ms':fault_ms,'status':fault,'operation':origin,'stream':'modem'},
          {'kind':'sample','state':4,'ms':fault_ms,'modem_underflows':1},abandoned]
    text='INJECT MIC close-timing requested_ms=1001 mode='+mode+' phase=drain\n'
    text+='INJECT MIC close-timing mode='+mode+' real_ack_ms=1002 success=1 withheld='+str(int(mode=='missing'))+' phase=drain\n'
    if mode=='invalid':text+='INJECT MIC close-timing retrograde write_index_bytes=0 source_usec=0 transport_usec=0 phase=drain\n'
    if mode=='stop':text+='INJECT MIC close-timing stop-before-forward stop_ms=1002 callback_invoked=1 phase=drain\n'
    (root/'test.log').write_text(text)
    (result/'freedv-usb-session-unit.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    harness={'fault':fault,'first_fault_origin':origin,'turns':0,'total_turns':0,'eoo':0,'keys':1,
             'rf_clear_confirmed':True,'physical_tx':False}
    close=capture_close_fixture();close['abandoned_intervals']=1
    return {'harness':harness,'capture_close':close,
            'capture_close_failure':validate_capture_close_failure(rows,harness,text,mode)}


def capture_case_fixture(case,root):
    if case in ('capture-release-split','capture-release-held150'):return capture_source_boundary_fixture(case,root)
    if case in ('capture-close-missing','capture-close-invalid','capture-close-stop'):return capture_close_failure_fixture(case,root)
    if case=='eoo-backpressure':return tail_backpressure_fixture()
    return {'harness':{'turns':0,'total_turns':0,'fault':1},'capture_close':capture_close_fixture()}


def tail_backpressure_fixture():
    """Gate-unit metadata only; actual render evidence comes from the C harness."""
    queue={'limit_frames':22050,'output_bound_frames':4608,'high_water_frames':18000,'deferred_calls':4,
           'final_deferred_calls':1,'final_flush_calls':2,'final_flush_frames':18,
           'generated_frames':50000,'written_frames':50000}
    hold={'started_ms':1000,'ended_ms':1150,'hits':3}
    proof={'status':'PASS','render_queue':queue,'render_hold':hold,
           'render_audit':{'status':'PASS','count':14,'generated_frames':50000,
                           'final_flush_calls':2,'final_flush_frames':18}}
    return {'tail_backpressure':proof,'harness':{'render_queue':queue,'render_hold':hold,
             'turns':1,'total_turns':1,'eoo':1,'fault':1,'rf_clear_confirmed':True},
            'capture_close':capture_close_fixture(1),
            'actual_sink_turns':[{'status':'PASS','content_match':{'status':'PASS',
                'method':'full_modem_waveform_and_100ms_windows','complete_reference_covered':True}}]}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identity = {'source_commit': 'a' * 40, 'source_tree_sha256': 'b' * 64,
                         'build_id': 'campaign-test', 'test_binary_sha256': 'c' * 64,
                         'runner_sha256': 'd' * 64, 'speech_sha256': 'e' * 64,
                         'modem_sha256': 'f' * 64, 'fault_injector_sha256': '1' * 64}
        self.identity['host_resource_configuration']=host_resource_configuration()
        self.plan = {'profile': 'qualification', 'repetitions': 2,
                     'expected_cases': ['normal'], 'selected_cases': ['normal'],
                     'debug_subset': False, 'case_timeout_seconds': {'normal': 90},
                     'infrastructure_prerequisites':list(INFRASTRUCTURE_PREREQUISITES),
                     'host_resource_configuration':self.identity['host_resource_configuration']}
        self.counter = 0

    def begin(self, label=None):
        self.counter += 1
        evidence = self.root / ('evidence-' + str(self.counter))
        evidence.mkdir()
        campaign = Campaign(self.root / 'history', self.identity, self.plan, evidence, label)
        self.addCleanup(campaign.close)
        return campaign, evidence

    def result(self, evidence, status='PASS'):
        runs = []
        for repeat in (1, 2):
            log = evidence / ('normal-%d.log' % repeat)
            log.write_text('retained bounded subprocess log\n')
            case = evidence / ('normal-%d.json' % repeat)
            case.write_text(json.dumps({'status': 'PASS', 'repeat': repeat}) + '\n')
            runs.append({'status': 'PASS', 'repeat': repeat, 'log_path': str(log),
                         'log_sha256': sha(log), 'case_record_path': str(case),
                         'case_record_sha256': sha(case),'harness':{'turns':0,'total_turns':0,'fault':1},
                         'capture_close':capture_close_fixture()})
        result = {'schema': MATRIX_SCHEMA, 'status': status, 'identity': self.identity,
                  'plan': self.plan, 'complete_matrix': status == 'PASS',
                  'expected_executions': ['normal@1', 'normal@2'],
                  'executed': ['normal@1', 'normal@2'], 'not_executed': [], 'skipped': [],
                  'cases': {'normal': {'status': status, 'runs': runs}},
                  'host_resources':resource_fixture(evidence,self.identity),
                  'fault_injector_sha256':self.identity['fault_injector_sha256'],
                  'infrastructure_prerequisites':{'unexpected_runtime_failure':infrastructure_fixture(
                      evidence/'infrastructure',self.identity,self.identity['fault_injector_sha256'])}}
        if status != 'PASS':
            runs[-1]['status'] = status
        return result

    def finish(self, campaign, result):
        summary = campaign.finish(result)
        campaign.close()
        return {**result, 'campaign': summary, 'promotion_eligible': summary['promotion_eligible']}

    def test_complete_repeated_campaign_is_promotable(self):
        campaign, evidence = self.begin()
        report = self.finish(campaign, self.result(evidence))
        self.assertTrue(validate_campaign(report, ['normal'])['promotion_eligible'])
        self.assertEqual(report['campaign']['ledger_head']['sequence'], 1)

    def test_missing_infrastructure_proof_cannot_promote_functional_passes(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        result['infrastructure_prerequisites']={}
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'infrastructure'):
            validate_campaign(report,['normal'])

    def test_infrastructure_child_failure_cannot_be_relabelled_success(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        entry['record']['variants']['initial_buffer']['driver_exit_code']=0
        atomic_json(entry['report_path'],entry['record'])
        entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'functional test'):
            validate_campaign(report,['normal'],verify_files=False)

    def test_infrastructure_artifact_mutation_blocks_promotion(self):
        campaign,evidence=self.begin()
        report=self.finish(campaign,self.result(evidence))
        proof=report['infrastructure_prerequisites']['unexpected_runtime_failure']['record']
        Path(proof['variants']['initial_buffer']['evidence_files']['completion']['path']).write_text('{}\n')
        with self.assertRaisesRegex(ValueError,'child evidence changed'):
            validate_campaign(report,['normal'])

    def test_infrastructure_summary_cannot_contradict_retained_child(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        item=entry['record']['variants']['initial_buffer']['evidence_files']['case_result']
        atomic_json(item['path'],{'status':'PASS','controller_exit_code':0})
        item['sha256']=sha(item['path'])
        atomic_json(entry['report_path'],entry['record'])
        entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'contradict'):
            validate_campaign(report,['normal'])

    def test_both_startup_and_qualification_failure_paths_are_mandatory(self):
        campaign,evidence=self.begin();result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        del entry['record']['variants']['qualification_telemetry']
        atomic_json(entry['report_path'],entry['record']);entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'both real failure paths'):validate_campaign(report,['normal'])

    def test_qualification_failure_requires_actual_stale_epoch_and_recovery(self):
        for mutation in ('stale-age','recovery','keys'):
            campaign,evidence=self.begin();result=self.result(evidence)
            entry=result['infrastructure_prerequisites']['unexpected_runtime_failure'];variant=entry['record']['variants']['qualification_telemetry']
            if mutation=='stale-age':variant['qualification_failure']['fault_ms']=1200
            elif mutation=='recovery':variant['qualification_failure']['recovered_ms']=1200
            else:variant['keys']=1
            atomic_json(entry['report_path'],entry['record']);entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
            report=self.finish(campaign,result)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_campaign(report,['normal'],verify_files=False)

    def test_failed_attempt_followed_by_pass_stays_blocked(self):
        first, evidence = self.begin('first-run')
        failed = self.finish(first, self.result(evidence, 'FAIL'))
        second, evidence = self.begin('different-label-and-output')
        passed = self.finish(second, self.result(evidence))
        self.assertEqual(first.root, second.root)
        self.assertEqual(len(passed['campaign']['history']), 2)
        self.assertEqual(passed['campaign']['blockers'], [failed['campaign']['current_attempt_id']])
        with self.assertRaises(ValueError):
            validate_campaign(passed, ['normal'])

    def test_abandoned_start_is_not_erased_by_successful_replay(self):
        first, _ = self.begin()
        first.close()  # Simulates death after the durable start, before closure.
        second, evidence = self.begin()
        report = self.finish(second, self.result(evidence))
        self.assertEqual(report['campaign']['history'][0]['status'], 'INTERRUPTED')
        with self.assertRaises(ValueError):
            validate_campaign(report, ['normal'])

    def test_removing_failed_attempt_cannot_clear_history(self):
        first, evidence = self.begin()
        self.finish(first, self.result(evidence, 'FAIL'))
        shutil.rmtree(first.attempt)
        with self.assertRaises((OSError, ValueError)):
            self.begin('new-label-does-not-reset-ledger')

    def test_removing_attempt_and_ledger_entry_still_fails_head(self):
        first, evidence = self.begin()
        self.finish(first, self.result(evidence, 'FAIL'))
        shutil.rmtree(first.attempt)
        (first.root / 'attempt-index/00000001.json').unlink()
        with self.assertRaises(ValueError):
            self.begin()

    def test_changed_immutable_result_is_detected(self):
        campaign, evidence = self.begin()
        report = self.finish(campaign, self.result(evidence))
        retained = campaign.attempt / 'result.json'
        retained.chmod(0o644)
        retained.write_text('{}\n')
        with self.assertRaises(ValueError):
            validate_campaign(report, ['normal'])

    def test_changed_case_evidence_is_detected(self):
        campaign, evidence = self.begin()
        report = self.finish(campaign, self.result(evidence))
        (evidence / 'normal-2.log').write_text('overwritten later\n')
        with self.assertRaises(ValueError):
            validate_campaign(report, ['normal'])

    def test_missing_second_repetition_never_qualifies(self):
        campaign, evidence = self.begin()
        result = self.result(evidence)
        result['cases']['normal']['runs'].pop()
        result['executed'].pop()
        result['not_executed'] = ['normal@2']
        result['skipped'] = ['normal']
        result['complete_matrix'] = False
        report = self.finish(campaign, result)
        with self.assertRaises(ValueError):
            validate_campaign(report, ['normal'])

    def test_older_success_is_invalidated_by_a_later_failed_attempt(self):
        first, evidence = self.begin()
        earlier = self.finish(first, self.result(evidence))
        second, evidence = self.begin()
        self.finish(second, self.result(evidence, 'FAIL'))
        with self.assertRaises(ValueError):
            validate_campaign(earlier, ['normal'])

    def test_omitted_failed_history_is_rejected_offline_too(self):
        first, evidence = self.begin()
        self.finish(first, self.result(evidence, 'FAIL'))
        second, evidence = self.begin()
        report = self.finish(second, self.result(evidence))
        forged = copy.deepcopy(report)
        campaign = forged['campaign']
        campaign['history'].pop(0)
        campaign['history_sha256'] = digest(campaign['history'])
        campaign.update(status='PASS', blockers=[], promotion_eligible=True)
        forged['promotion_eligible'] = True
        with self.assertRaises(ValueError):
            validate_campaign(forged, ['normal'], verify_files=False)

    def test_diagnostic_profile_never_qualifies(self):
        self.plan['profile'] = 'regression'
        campaign, evidence = self.begin()
        report = self.finish(campaign, self.result(evidence))
        self.assertFalse(report['promotion_eligible'])
        with self.assertRaises(ValueError):
            validate_campaign(report, ['normal'])

    def test_immutable_record_refuses_overwrite(self):
        path = self.root / 'immutable.json'
        immutable_json(path, {'status': 'FAIL'})
        with self.assertRaises(FileExistsError):
            immutable_json(path, {'status': 'PASS'})
        self.assertEqual(json.loads(path.read_text())['status'], 'FAIL')


class MatrixSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / 'controller'; self.binary.write_bytes(b'controller-fixture')
        self.runner = self.root / 'runner'; self.runner.write_bytes(b'runner-fixture')
        self.speech = self.root / 'speech.wav'; self.speech.write_bytes(b'speech-fixture')
        self.modem = self.root / 'modem.raw'; self.modem.write_bytes(b'modem-fixture')
        self.compiler = self.root/'fixture-cc'; self.compiler.write_bytes(b'compiler-fixture'); self.compiler.chmod(0o755)
        self.calls = []
        self.hang = False
        self.fail = False
        self.compile_fail = False
        self.old_build = False
        self.fixture_mismatch = False
        self.aggregate_only = False
        self.infrastructure_fail = False
        self.infrastructure_fake_exit = False
        self.infrastructure_wrong_identity = False
        self.counter = 0
        self.resource_blocked=False
        self.resource_end_pressure=False

    def resources_start(self,**kwargs):
        config=host_resource_configuration(kwargs['profile'],kwargs['reclaim_roots'],kwargs['min_headroom_bytes'],kwargs['min_end_headroom_bytes'])
        identity={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'build_id':'test','test_binary_sha256':sha(self.binary)}
        self.resource_template=resource_fixture(Path(kwargs['output']).parent,identity,config)
        pre=self.resource_template['preflight']
        if self.resource_blocked:pre.update(status='BLOCKED',assessment='BLOCKED',promotion_eligible=False,reasons=['headroom low'])
        atomic_json(kwargs['output'],pre)
        return pre

    def resources_end(self,preflight,**kwargs):
        end=copy.deepcopy(self.resource_template['end']);end['before']=preflight['after']
        if self.resource_end_pressure:
            end['after']['memory']['events']['max']+=1
            end.update(status='BLOCKED',assessment='BLOCKED',promotion_eligible=False,reasons=['pressure'],new_memory_pressure={'memory.events.max':1})
            end['counter_deltas']['memory.events.max']=1
        atomic_json(kwargs['output'],end)
        return end

    def supervised(self, command, log_path, timeout, **kwargs):
        expected_env = kwargs.get('env', {})
        log_path = Path(log_path); log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('simulated bounded child\n')
        self.calls.append((command, timeout))
        if '-shared' in command:
            Path(command[-1]).write_bytes(b'compiled-injector-fixture')
            return {'exit_code': 1 if self.compile_fail else 0, 'timed_out': False, 'duration_ms': 1}
        if str(command[1]).endswith('usb_harness_failure_selftest.py'):
            output=Path(command[command.index('--output')+1])
            identity={'source_commit':expected_env['USB_TEST_EXPECTED_SOURCE_COMMIT'],
                      'build_id':expected_env['USB_TEST_EXPECTED_BUILD_ID'],
                      'test_binary_sha256':sha(self.binary),'runner_sha256':sha(self.runner),
                      'speech_sha256':sha(self.speech),'modem_sha256':sha(self.modem)}
            entry=infrastructure_fixture(output,identity,sha(Path(command[command.index('--fault-inject-so')+1])))
            proof=entry['record']
            if self.infrastructure_fail: proof['status']='FAIL'
            if self.infrastructure_fake_exit: proof['variants']['initial_buffer']['driver_exit_code']=0
            if self.infrastructure_wrong_identity: proof['source_commit']='wrong-source'
            atomic_json(output/'infrastructure-result.json',proof)
            return {'exit_code':1 if self.infrastructure_fail else 0,'timed_out':False,'duration_ms':1}
        case = command[command.index('--case') + 1]
        output = Path(command[command.index('--output') + 1]); output.mkdir()
        if self.hang:
            return {'exit_code': -9, 'timed_out': True, 'duration_ms': 90000}
        details = {'status': 'FAIL' if self.fail else 'PASS', 'case': case,
                   'test_binary_sha256': sha(self.binary), 'harness': {'status':'PASS','turns':0,'total_turns':0,'fault':1},
                   'capture_close':capture_close_fixture(),
                   'identity': {'status':'PASS', 'source_commit':expected_env.get('USB_TEST_EXPECTED_SOURCE_COMMIT'),
                                'build_id':'older-build' if self.old_build else expected_env.get('USB_TEST_EXPECTED_BUILD_ID'),
                                'binary_sha256':sha(self.binary)},
                   'fixture_sha256':{'speech':sha(self.speech),'modem':'0'*64 if self.fixture_mismatch else sha(self.modem)},
                   'runner_sha256':sha(self.runner),
                   'fault_injector_sha256':sha(Path(command[command.index('--fault-inject-so')+1])),
                   'collector_contract':{'status':'PASS','radio_accessed':False,
                         'source_commit':expected_env.get('USB_TEST_EXPECTED_SOURCE_COMMIT'),
                         'build_id':expected_env.get('USB_TEST_EXPECTED_BUILD_ID'),
                         'controller_binary_sha256':sha(self.binary),
                         'mutations':{name:{'status':'PASS'} for name in ('missing-completion','truncated-completion','truncated-trace')}}}
        if self.aggregate_only: details['actual_sink_decode']={'synthesis_blocks':20}
        (output / 'case-result.json').write_text(json.dumps(details) + '\n')
        return {'exit_code': 1 if self.fail else 0, 'timed_out': False, 'duration_ms': 1}

    def invoke(self, *, subset=False):
        self.counter += 1
        output = self.root / ('matrix-' + str(self.counter))
        argv = ['matrix', str(self.binary), str(self.speech), str(self.modem),
                '--runner', str(self.runner), '--rade-library',str(self.runner), '--output', str(output),
                '--campaign-root', str(self.root/'campaigns'), '--build-id', 'test', '--cc',str(self.compiler)]
        if subset:
            argv += ['--only', 'normal']
        real_sha = sha
        repository = Path(matrix.__file__).resolve().parents[2]
        def stable_sha(path):
            return '2' * 64 if repository in Path(path).resolve().parents else real_sha(path)
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(matrix, 'CASES', ('normal', 'other') if subset else ('normal',)), \
                mock.patch.object(matrix, 'SPEECH_CASES', {'normal'} if self.aggregate_only else set()), \
                mock.patch.object(matrix, 'run_bounded', self.supervised), \
                mock.patch.object(matrix,'run_resource_preflight',self.resources_start), \
                mock.patch.object(matrix,'observe_resource_end',self.resources_end), \
                mock.patch.object(matrix, 'source_snapshot', return_value=({}, 'b'*64)), \
                mock.patch.object(matrix.subprocess, 'check_output', return_value='a'*40+'\n'), \
                mock.patch.object(matrix, 'sha', stable_sha):
            status = matrix.main()
        return status, json.loads((output/'matrix-result.json').read_text())

    def test_resource_preflight_blocks_before_any_audio_prerequisite(self):
        self.resource_blocked=True
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertEqual(record['cases'],{})
        self.assertEqual(record['infrastructure_prerequisites'],{})
        self.assertFalse(record['promotion_eligible'])

    def test_resource_end_pressure_blocks_otherwise_passing_campaign(self):
        self.resource_end_pressure=True
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertEqual(record['status'],'FAIL')
        self.assertTrue(all(case['status']=='PASS' for case in record['cases'].values()))
        self.assertFalse(record['promotion_eligible'])

    def test_end_resource_failure_does_not_replace_original_controller_failure(self):
        self.fail=True;self.resource_end_pressure=True
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertIn('USB integration case failed',record['reason'])
        self.assertIn('host_resource_error',record)
        self.assertEqual(record['cases']['normal']['status'],'FAIL')

    def test_resource_counter_change_does_not_reset_same_configuration_history(self):
        self.resource_blocked=True
        self.invoke();self.resource_blocked=False
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertEqual(record['campaign']['status'],'BLOCKED')

    def test_failed_infrastructure_stops_before_functional_cases(self):
        self.infrastructure_fail=True
        status,record=self.invoke()
        self.assertEqual(status,1)
        self.assertEqual(record['status'],'FAIL')
        self.assertEqual(record['started'],[])
        self.assertEqual(record['executed'],[])
        self.assertEqual(record['cases'],{})
        self.assertFalse(record['complete_matrix'])
        self.assertFalse(record['promotion_eligible'])

    def test_faked_infrastructure_success_is_rejected_before_cases(self):
        self.infrastructure_fake_exit=True
        status,record=self.invoke()
        self.assertEqual(status,1)
        self.assertEqual(record['cases'],{})
        self.assertIn('functional test',record['reason'])

    def test_infrastructure_identity_must_match_candidate(self):
        self.infrastructure_wrong_identity=True
        status,record=self.invoke()
        self.assertEqual(status,1)
        self.assertEqual(record['cases'],{})
        self.assertIn('different source',record['reason'])

    def test_prerequisite_is_not_counted_as_functional_pass(self):
        status,record=self.invoke()
        self.assertEqual(status,0)
        self.assertEqual(set(record['cases']),{'normal'})
        self.assertEqual(record['executed'],['normal@1','normal@2'])
        self.assertEqual(len(record['infrastructure_prerequisites']),1)
        self.assertIn('infrastructure_failure_preservation',record['coverage'])

    def test_outer_hung_case_retains_timeout_and_unexecuted_repeat(self):
        self.hang = True
        status, record = self.invoke()
        self.assertEqual(status, 1)
        self.assertEqual(record['status'], 'TIMEOUT')
        self.assertEqual(record['executed'], ['normal@1'])
        self.assertEqual(record['not_executed'], ['normal@2'])
        self.assertEqual(record['skipped'], ['normal'])
        self.assertFalse(record['complete_matrix'])
        self.assertFalse(record['promotion_eligible'])
        self.assertTrue(record['cases']['normal']['runs'][0]['supervision']['timed_out'])

    def test_failed_case_then_success_cannot_retry_to_green(self):
        self.fail = True
        first_status, _ = self.invoke()
        self.fail = False
        second_status, record = self.invoke()
        self.assertEqual(first_status, 1)
        self.assertEqual(second_status, 1)
        self.assertEqual(record['status'], 'PASS')
        self.assertEqual(record['campaign']['status'], 'BLOCKED')
        self.assertEqual(len(record['campaign']['history']), 2)
        self.assertFalse(record['promotion_eligible'])

    def test_injector_compile_failure_is_retained_before_replay(self):
        self.compile_fail = True
        first_status, failed = self.invoke()
        self.assertEqual(first_status, 1)
        self.assertEqual(failed['executed'], [])
        self.compile_fail = False
        second_status, passed = self.invoke()
        self.assertEqual(second_status, 1)
        self.assertEqual(passed['campaign']['history'][0]['result']['injector_compilation']['exit_code'], 1)
        self.assertFalse(passed['promotion_eligible'])

    def test_case_from_old_build_cannot_claim_current_source(self):
        self.old_build = True
        status, record = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn('identity', record['cases']['normal']['runs'][0]['reason'])
        self.assertFalse(record['promotion_eligible'])

    def test_case_fixture_mismatch_is_a_failure(self):
        self.fixture_mismatch = True
        status, record = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn('fixture', record['cases']['normal']['runs'][0]['reason'])

    def test_aggregate_decode_without_every_turn_is_insufficient(self):
        self.aggregate_only = True
        status, record = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn('per-turn', record['cases']['normal']['runs'][0]['reason'])

    def test_subset_pass_is_explicitly_nonpromotable(self):
        status, record = self.invoke(subset=True)
        self.assertEqual(status, 0)
        self.assertEqual(record['status'], 'PASS')
        self.assertEqual(record['skipped'], ['other'])
        self.assertFalse(record['complete_matrix'])
        self.assertFalse(record['promotion_eligible'])

    def test_full_repeated_success_is_eligible(self):
        status, record = self.invoke()
        self.assertEqual(status, 0)
        self.assertTrue(record['promotion_eligible'])
        self.assertEqual(record['executed'], ['normal@1', 'normal@2'])
        validate_campaign(record, ['normal'])


class HostResourceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.config=host_resource_configuration()
        self.identity={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'build_id':'unit-resource',
                       'test_binary_sha256':'c'*64,'host_resource_configuration':self.config}

    def fixture(self):
        return {'identity':self.identity,'plan':{'host_resource_configuration':self.config},
                'host_resources':resource_fixture(self.root,self.identity,self.config)}

    def test_known_host_with_past_pressure_and_no_new_pressure_passes(self):
        validate_host_resources(self.fixture())

    def test_start_end_headroom_are_independent_fixed_minima(self):
        for phase,current in (('preflight',13*1024**3),('end',16*1024**3-512*1024**2)):
            evidence=self.fixture();report=evidence['host_resources'][phase];snapshot=report['after']
            head=16*1024**3-current;snapshot['memory'].update(current_bytes=current,headroom_bytes=head)
            snapshot['headroom_bytes']=report['headroom_bytes']=head
            with self.subTest(phase=phase),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_unknown_cgroup_does_not_use_unbounded_host_fallback(self):
        evidence=self.fixture();evidence['host_resources']['preflight']['after']['memory']['max_bytes']=None
        with self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_new_or_reset_pressure_counter_cannot_be_hidden_by_pass_flag(self):
        for delta in (-1,1):
            evidence=self.fixture();evidence['host_resources']['end']['after']['memory']['events']['max']+=delta
            with self.subTest(delta=delta),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_changed_limits_or_postreclaim_baseline_block_promotion(self):
        for mutation in ('cpuquota','baseline'):
            evidence=self.fixture()
            if mutation=='cpuquota':evidence['host_resources']['end']['after']['cpu']['quota_us']=200000
            else:evidence['host_resources']['end']['before']=copy.deepcopy(evidence['host_resources']['end']['before']);evidence['host_resources']['end']['before']['memory']['events']['max']=0
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_cpu_throttling_alone_is_diagnostic_not_a_fabricated_audio_fault(self):
        evidence=self.fixture();cpu=evidence['host_resources']['end']['after']['cpu']['stat']
        cpu.update(nr_throttled=200,throttled_usec=999999)
        validate_host_resources(evidence,verify_files=False)

    def test_preflight_alone_cannot_promote(self):
        evidence=self.fixture();del evidence['host_resources']['end']
        validate_host_resources(evidence,require_end=False)
        with self.assertRaises(ValueError):validate_host_resources(evidence)

    def test_cache_preparation_must_remain_readonly_and_bounded(self):
        for key,value in (('contents_modified',True),('timed_out',True),('worker_still_running',True)):
            evidence=self.fixture();evidence['host_resources']['preflight']['reclaim'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_thresholds_and_resource_config_cannot_be_weakened(self):
        for key,value in (('min_headroom_bytes',1024**3),('min_end_headroom_bytes',0)):
            evidence=self.fixture();evidence['plan']['host_resource_configuration'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)
            self.config=host_resource_configuration();self.identity['host_resource_configuration']=self.config

    def test_retained_resource_receipts_cannot_be_replaced(self):
        evidence=self.fixture();Path(evidence['host_resources']['end_report_path']).write_text('{}\n')
        with self.assertRaises(ValueError):validate_host_resources(evidence)


class CaptureCloseGateTests(unittest.TestCase):
    def run_fixture(self,rf=1,extra=False,fault=1):
        return {'harness':{'turns':rf,'total_turns':rf,'fault':fault},
                'capture_close':capture_close_fixture(rf,extra)}

    def test_finite_closed_interval_and_later_rf_fault_are_distinct(self):
        validate_capture_close_run(self.run_fixture())
        validate_capture_close_run(self.run_fixture(0,extra=True,fault=4))

    def test_missing_proof_or_rf_interval_is_rejected(self):
        for evidence in ({'harness':{'turns':1}},self.run_fixture()):
            if 'capture_close' in evidence:evidence['capture_close']['turns']=[]
            with self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_postcutoff_acceptance_or_missing_frame_cannot_conserve_interval(self):
        for key,delta in (('consumed_index',1),('accepted_frames',-1),('mic_tap_end',1)):
            evidence=self.run_fixture();evidence['capture_close']['turns'][0][key]+=delta
            with self.subTest(key=key),self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_monitor_latency_cannot_rewrite_capture_frontier(self):
        evidence=self.run_fixture();row=evidence['capture_close']['turns'][0]
        row['cutoff_index']-=480;row['consumed_index']-=480;row['accepted_frames']-=480;row['mic_tap_end']-=480
        row['microphone_frame_budget']['accepted_frames']-=480
        with self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_deadline_and_existing_source_budget_cannot_be_relaxed(self):
        for mutation in ('deadline','tolerance','fragments'):
            evidence=self.run_fixture();row=evidence['capture_close']['turns'][0]
            if mutation=='deadline':row['close_elapsed_ms']=751
            elif mutation=='tolerance':row['microphone_frame_budget']['delivery_tolerance_percent']=2
            else:row['microphone_frame_budget']['boundary_frames']+=1
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_closed_capture_without_completed_rf_requires_actual_fault(self):
        with self.assertRaises(ValueError):validate_capture_close_run(self.run_fixture(0,extra=True,fault=1))

    def test_abandoned_interval_never_counts_as_completed_rf(self):
        evidence=self.run_fixture(0);evidence['capture_close']['abandoned_intervals']=1
        evidence['harness']['total_turns']=1;evidence['capture_close']['completed_rf_turns']=1
        with self.assertRaises(ValueError):validate_capture_close_run(evidence)


class CaptureBoundaryProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.count=0

    def fixture(self,case='capture-release-split'):
        self.count+=1;root=self.root/str(self.count);root.mkdir()
        evidence=capture_source_boundary_fixture(case,root)
        path=root/'case-result.json';path.write_text('{}\n');evidence['case_record_path']=str(path)
        return evidence,root

    def test_exact_source_boundary_both_delivery_histories(self):
        for case in ('capture-release-split','capture-release-held150'):
            evidence,_=self.fixture(case);validate_capture_source_boundary_run(evidence,case)

    def test_matching_counts_cannot_replace_missing_prefix_with_future_audio(self):
        evidence,root=self.fixture()
        path=root/'result/freedv-usb-session-unit-mic.raw';original=path.read_bytes()
        path.write_bytes(original[20:]+(30001).to_bytes(2,'little',signed=True)*10)
        with self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_faked_hash_labels_do_not_replace_independent_pcm_evidence(self):
        evidence,_=self.fixture();row=evidence['capture_source_boundary']['turns'][0]
        row['expected_source_sha256']=row['accepted_sha256']='0'*64
        with self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_postcutoff_exclusion_and_straddling_block_are_mandatory(self):
        for field in ('post_cutoff_excluded','post_cutoff_sentinel_excluded'):
            evidence,_=self.fixture();evidence['capture_source_boundary']['turns'][0][field]=False
            with self.subTest(field=field),self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_held_delivery_must_overlap_async_frontier_freeze(self):
        evidence,_=self.fixture('capture-release-held150');row=evidence['capture_close']['turns'][0]
        row['frozen_ms']=3110;row['reached_ms']=3120
        with self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-held150')


class CaptureCloseFailureProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.count=0

    def fixture(self,mode):
        self.count+=1;root=self.root/str(self.count);root.mkdir()
        case='capture-close-'+mode;evidence=capture_close_failure_fixture(case,root)
        path=root/'case-result.json';path.write_text('{}\n');evidence['case_record_path']=str(path)
        return evidence,case

    def test_all_actual_drain_failure_paths_keep_clear_and_no_eoo(self):
        for mode in ('missing','invalid','stop'):
            evidence,case=self.fixture(mode);validate_capture_close_failure_run(evidence,case)

    def test_initial_phase_cannot_claim_drain_failure_coverage(self):
        evidence,case=self.fixture('missing');evidence['capture_close_failure']['phase']='initial-rx'
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)

    def test_late_successful_callback_cannot_commit_after_stop(self):
        evidence,case=self.fixture('stop');evidence['capture_close_failure']['abandoned_interval']['frozen_ms']=1003
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)

    def test_unrelated_fault_cannot_prove_invalid_frontier(self):
        evidence,case=self.fixture('invalid');evidence['capture_close_failure']['fault']=evidence['harness']['fault']=13
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)

    def test_withheld_ack_must_be_real_and_no_completed_voice_turn_claimed(self):
        evidence,case=self.fixture('missing');evidence['capture_close_failure']['withheld']=False
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)
        evidence,case=self.fixture('missing');evidence['harness']['eoo']=1
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)


class TailBackpressureGateTests(unittest.TestCase):
    def test_complete_bounded_tail_witness_is_accepted(self):
        validate_tail_backpressure_run(tail_backpressure_fixture())

    def test_nominal_pass_without_exercised_pressure_is_rejected(self):
        evidence=tail_backpressure_fixture()
        evidence['tail_backpressure']['render_queue']['high_water_frames']=17442
        with self.assertRaises(ValueError):validate_tail_backpressure_run(evidence)

    def test_missing_or_unwritten_tail_is_rejected(self):
        for key,value in (('final_flush_frames',0),('final_flush_calls',1),('written_frames',49999)):
            evidence=tail_backpressure_fixture()
            evidence['tail_backpressure']['render_queue'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_tail_backpressure_run(evidence)

    def test_partial_waveform_cannot_claim_complete_eoo_delivery(self):
        evidence=tail_backpressure_fixture()
        evidence['actual_sink_turns'][0]['content_match']['complete_reference_covered']=False
        with self.assertRaises(ValueError):validate_tail_backpressure_run(evidence)

    def test_audit_counters_must_match_actual_harness(self):
        evidence=tail_backpressure_fixture()
        evidence['tail_backpressure']['render_audit']['generated_frames']=1
        with self.assertRaises(ValueError):validate_tail_backpressure_run(evidence)


class BuildPrerequisiteHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root/'repository'
        (self.repository/'lvgl').mkdir(parents=True)
        (self.repository/'lvgl/CMakeLists.txt').write_text('# fixture')
        self.work = self.root/'work'

    def invoke(self, failure=False):
        def perform(args, parser, work, attempt, record, before, tree, revision, host, host_native):
            del args, parser, work, before, tree, revision, host, host_native
            log = attempt/'voice-engine.log'
            log.write_text('FAIL retained prerequisite' if failure else 'PASS prerequisite')
            record['logs']['voice-engine.log'] = {'path':str(log),'sha256':sha(log)}
            if failure:
                raise ValueError('Mandatory prerequisite failed')
            record['status'] = 'PASS'
        argv = ['builder','--variant','host','--work',str(self.work),'--build-id','build-test']
        with mock.patch.object(sys,'argv',argv), mock.patch.object(builder,'ROOT',self.repository), \
                mock.patch.object(builder,'source_snapshot',return_value=({},'b'*64)), \
                mock.patch.object(builder.subprocess,'check_output',return_value='a'*40+'\n'), \
                mock.patch.object(builder,'_perform_build',perform):
            builder.main()
        return json.loads((self.work/'build-record.json').read_text())

    def test_failed_prerequisite_then_later_pass_remains_blocked(self):
        with self.assertRaisesRegex(ValueError,'Mandatory prerequisite'):
            self.invoke(failure=True)
        failed = json.loads((self.work/'build-record.json').read_text())
        with self.assertRaisesRegex(ValueError,'blocked by prerequisite history'):
            self.invoke()
        latest = json.loads((self.work/'build-record.json').read_text())
        self.assertEqual(latest['status'],'PASS')  # Actual replay outcome is preserved.
        self.assertFalse(latest['build_promotion_eligible'])
        self.assertEqual(latest['build_history']['status'],'BLOCKED')
        self.assertEqual(len(latest['build_history']['history']),2)
        self.assertEqual(json.loads(Path(failed['immutable_record_path']).read_text())['status'],'FAIL')
        self.assertEqual(len(list((self.work/'attempts').iterdir())),2)

    def test_current_build_binds_immutable_record_and_logs(self):
        record = self.invoke()
        builder.validate_build_record(record)
        log = Path(record['logs']['voice-engine.log']['path'])
        log.write_text('accidentally overwritten')
        with self.assertRaises(ValueError):
            builder.validate_build_record(record)


if __name__ == '__main__':
    unittest.main()
