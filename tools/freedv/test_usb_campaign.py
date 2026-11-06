#!/usr/bin/env python3
"""Qualification-history and matrix-failure regressions; no audio/radio needed."""
import copy
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
                               immutable_json, sha, validate_campaign, INFRASTRUCTURE_PREREQUISITES, INFRASTRUCTURE_CHECKS, validate_tail_backpressure_run)


def infrastructure_fixture(root, identity, injector_sha):
    """Synthetic metadata for gate unit negatives; never actual C evidence."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    bodies={
        'case_result':{'status':'FAIL','case':'normal','controller_exit_code':2},
        'harness_failure':{'status':'FAIL','exit_code':2,'primary_fault':13,'primary_origin':'modem-buffer-budget',
                           'cleanup_requested':True,'stopped':True,'closed':True},
        'completion':{'terminal':True,'fault':13,'trace_complete':True,'rf_clear_confirmed':True},
        'collector_report':{'status':'BLOCKED','ready_for_rf_test':False},
        'first_fault':{'first_fault':{'code':13,'origin':'modem-buffer-budget'}},
        'trace':{'kind':'sample','state':5,'fault':13},
    }
    files={}
    for name,body in bodies.items():
        path=root/(name+'.json');atomic_json(path,body)
        files[name]={'path':str(path),'sha256':sha(path)}
    proof={'schema':'x6200.usb.infrastructure.v1','kind':'unexpected-runtime-failure','status':'PASS',
           'radio_accessed':False,'source_commit':identity['source_commit'],'build_id':identity['build_id'],
           'controller_binary_sha256':identity['test_binary_sha256'],'runner_sha256':identity['runner_sha256'],
           'fixture_sha256':{'speech':identity['speech_sha256'],'modem':identity['modem_sha256']},
           'fault_injector_sha256':injector_sha,
           'checks':{name:True for name in INFRASTRUCTURE_CHECKS},
           'driver_exit_code':1,'driver_status':'FAIL','harness_exit_code':2,'harness_status':'FAIL',
           'terminal_fault_code':13,'terminal_fault_origin':'modem-buffer-budget','collector_status':'BLOCKED',
           'evidence_files':files}
    report=root/'infrastructure-result.json';atomic_json(report,proof)
    log=root/'infrastructure.log';log.write_text('Expected failed child preserved; unit fixture only\n')
    return {'status':'PASS','record':proof,'record_sha256':digest(proof),
            'report_path':str(report),'report_sha256':sha(report),'log_path':str(log),'log_sha256':sha(log),
            'supervision':{'exit_code':0,'timed_out':False}}


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
             'turns':1,'eoo':1,'rf_clear_confirmed':True},
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
        self.plan = {'profile': 'qualification', 'repetitions': 2,
                     'expected_cases': ['normal'], 'selected_cases': ['normal'],
                     'debug_subset': False, 'case_timeout_seconds': {'normal': 90},
                     'infrastructure_prerequisites':list(INFRASTRUCTURE_PREREQUISITES)}
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
                         'case_record_sha256': sha(case)})
        result = {'schema': MATRIX_SCHEMA, 'status': status, 'identity': self.identity,
                  'plan': self.plan, 'complete_matrix': status == 'PASS',
                  'expected_executions': ['normal@1', 'normal@2'],
                  'executed': ['normal@1', 'normal@2'], 'not_executed': [], 'skipped': [],
                  'cases': {'normal': {'status': status, 'runs': runs}},
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
        entry['record']['driver_exit_code']=0
        atomic_json(entry['report_path'],entry['record'])
        entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'functional test'):
            validate_campaign(report,['normal'],verify_files=False)

    def test_infrastructure_artifact_mutation_blocks_promotion(self):
        campaign,evidence=self.begin()
        report=self.finish(campaign,self.result(evidence))
        proof=report['infrastructure_prerequisites']['unexpected_runtime_failure']['record']
        Path(proof['evidence_files']['completion']['path']).write_text('{}\n')
        with self.assertRaisesRegex(ValueError,'child evidence changed'):
            validate_campaign(report,['normal'])

    def test_infrastructure_summary_cannot_contradict_retained_child(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        item=entry['record']['evidence_files']['case_result']
        atomic_json(item['path'],{'status':'PASS','controller_exit_code':0})
        item['sha256']=sha(item['path'])
        atomic_json(entry['report_path'],entry['record'])
        entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'contradict'):
            validate_campaign(report,['normal'])

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
            if self.infrastructure_fake_exit: proof['driver_exit_code']=0
            if self.infrastructure_wrong_identity: proof['source_commit']='wrong-source'
            atomic_json(output/'infrastructure-result.json',proof)
            return {'exit_code':1 if self.infrastructure_fail else 0,'timed_out':False,'duration_ms':1}
        case = command[command.index('--case') + 1]
        output = Path(command[command.index('--output') + 1]); output.mkdir()
        if self.hang:
            return {'exit_code': -9, 'timed_out': True, 'duration_ms': 90000}
        details = {'status': 'FAIL' if self.fail else 'PASS', 'case': case,
                   'test_binary_sha256': sha(self.binary), 'harness': {'status': 'PASS'},
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
                mock.patch.object(matrix, 'source_snapshot', return_value=({}, 'b'*64)), \
                mock.patch.object(matrix.subprocess, 'check_output', return_value='a'*40+'\n'), \
                mock.patch.object(matrix, 'sha', stable_sha):
            status = matrix.main()
        return status, json.loads((output/'matrix-result.json').read_text())

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
