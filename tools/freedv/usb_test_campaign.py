#!/usr/bin/env python3
"""Durable, fail-closed history for the USB controller qualification campaign.

The files here are software evidence, never RF qualification. An attempt can be
replayed for diagnosis, but a later successful attempt cannot erase an earlier
failed or abandoned attempt with the same source/configuration identity.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

MATRIX_SCHEMA = 'x6200.usb.integration.matrix.v3'
CAMPAIGN_SCHEMA = 'x6200.usb.campaign.v1'
MIN_QUALIFICATION_REPEATS = 2
INFRASTRUCTURE_PREREQUISITES = ('unexpected_runtime_failure',)
INFRASTRUCTURE_CHECKS = ('driver_failed', 'harness_failed', 'terminal_fault_retained', 'collector_blocked')
INFRASTRUCTURE_FILES = ('case_result', 'harness_failure', 'completion', 'collector_report', 'first_fault', 'trace')


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    """Publish a derived pointer/summary atomically, with data flushed first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def immutable_json(path, value):
    """Create once; existing evidence must never be silently replaced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        path.chmod(0o444)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _load(path):
    return json.loads(Path(path).read_text())


def _ledger_entries(root):
    """Validate the append-only start ledger before looking at attempt results.

    Keeping this separately from attempt directories means deleting a failed
    directory cannot remove its blocker. A chained entry plus durable head
    also detects a missing final or middle ledger entry.
    """
    index = root / 'attempt-index'
    head_path = root / 'ledger-head.json'
    files = sorted(index.glob('*.json'))
    directories = {p.name for p in (root / 'attempts').iterdir() if p.is_dir()}
    if not head_path.exists():
        if files or directories:
            raise ValueError('Campaign contains attempts without its durable ledger head')
        return []
    head = _load(head_path)
    if not isinstance(head,dict): raise ValueError('Campaign ledger head is not an object')
    count = head.get('sequence', 0)
    if not isinstance(count, int) or count < 1 or len(files) != count:
        raise ValueError('Campaign attempt ledger is missing or has uncommitted entries')
    entries = []
    previous = None
    for sequence in range(1, count + 1):
        path = index / ('%08d.json' % sequence)
        entry = _load(path)
        if not isinstance(entry,dict): raise ValueError('Campaign ledger entry is not an object')
        if entry.get('sequence') != sequence or entry.get('previous_sha256') != previous:
            raise ValueError('Campaign attempt ledger chain is incomplete')
        attempt = root / 'attempts' / entry['attempt_id']
        if not attempt.is_dir() or sha(attempt / 'start.json') != entry.get('start_sha256'):
            raise ValueError('An indexed campaign attempt was removed or changed')
        previous = sha(path)
        entries.append((entry, previous))
    if (head != {'sequence': count, 'entry_sha256': previous} or
            directories != {entry['attempt_id'] for entry, _ in entries}):
        raise ValueError('Campaign history omits or adds an unindexed attempt')
    return entries


def _history(root):
    history = []
    try:
        ledger = _ledger_entries(root)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return [{'attempt_id': 'invalid-ledger', 'status': 'CORRUPT', 'reason': str(error)}]
    for ledger_entry, ledger_sha in ledger:
        directory = root / 'attempts' / ledger_entry['attempt_id']
        entry = {'attempt_id': directory.name, 'path': str(directory),
                 'ledger': ledger_entry, 'ledger_sha256': ledger_sha}
        try:
            start = _load(directory / 'start.json')
            if not isinstance(start,dict): raise ValueError('Attempt start is not an object')
            entry.update(start=start, start_sha256=sha(directory / 'start.json'))
            if not (directory / 'result.json').exists():
                entry.update(status='INTERRUPTED', reason='Attempt has no immutable final result')
            else:
                result = _load(directory / 'result.json')
                if not isinstance(result,dict): raise ValueError('Attempt result is not an object')
                seal = _load(directory / 'seal.json')
                result_sha = sha(directory / 'result.json')
                if seal != {'start_sha256': entry['start_sha256'], 'result_sha256': result_sha}:
                    raise ValueError('Attempt seal does not match retained records')
                entry.update(result=result, result_sha256=result_sha,
                             seal=seal, seal_sha256=sha(directory / 'seal.json'),
                             status=result.get('status', 'FAIL'))
        except (OSError, ValueError, TypeError) as error:
            entry.update(status='CORRUPT', reason=str(error))
        history.append(entry)
    return history


class Campaign:
    """One source/configuration identity, with an append-only attempt history."""
    def __init__(self, root, identity, plan, evidence_root, label=None):
        self.identity = identity
        self.identity_sha256 = digest(identity)
        self.plan = plan
        self.root = Path(root).resolve() / self.identity_sha256
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'attempts').mkdir(exist_ok=True)
        (self.root / 'attempt-index').mkdir(exist_ok=True)
        self._lock = (self.root / 'campaign.lock').open('a+')
        # Matrix execution is Linux-only (private PulseAudio); use the kernel
        # lock so a killed process cannot leave a stale "busy" lock behind.
        import fcntl
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise ValueError('This source/configuration campaign is already running')
        identity_path = self.root / 'identity.json'
        if identity_path.exists():
            if _load(identity_path) != identity:
                self.close()
                raise ValueError('Campaign identity differs from its immutable record')
        else:
            immutable_json(identity_path, identity)
        try:
            ledger = _ledger_entries(self.root)
        except (OSError, ValueError, TypeError, KeyError):
            self.close()
            raise
        self.attempt_id = '%020d-%s' % (time.time_ns(), uuid.uuid4().hex[:12])
        self.attempt = self.root / 'attempts' / self.attempt_id
        self.attempt.mkdir()
        self.start = {'schema': CAMPAIGN_SCHEMA, 'attempt_id': self.attempt_id,
                      'identity_sha256': self.identity_sha256, 'plan': plan,
                      'plan_sha256': digest(plan), 'created_ns': time.time_ns(),
                      'evidence_root': str(Path(evidence_root).resolve()), 'label': label}
        immutable_json(self.attempt / 'start.json', self.start)
        sequence = len(ledger) + 1
        entry = {'sequence': sequence, 'attempt_id': self.attempt_id,
                 'start_sha256': sha(self.attempt / 'start.json'),
                 'previous_sha256': ledger[-1][1] if ledger else None}
        entry_path = self.root / 'attempt-index' / ('%08d.json' % sequence)
        immutable_json(entry_path, entry)
        atomic_json(self.root / 'ledger-head.json', {'sequence': sequence, 'entry_sha256': sha(entry_path)})
        self.finished = False
        self.snapshot()

    def snapshot(self):
        history = _history(self.root)
        blockers = [entry['attempt_id'] for entry in history if entry['status'] != 'PASS']
        current = next((entry for entry in history if entry['attempt_id'] == self.attempt_id), {})
        qualifying = (self.plan.get('profile') == 'qualification' and
                      self.plan.get('repetitions', 0) >= MIN_QUALIFICATION_REPEATS and
                      not self.plan.get('debug_subset'))
        build_profile = self.plan.get('profile') == 'build'
        complete = current.get('result', {}).get('status') == 'PASS' and (build_profile or current.get('result', {}).get('complete_matrix', False))
        eligible = (qualifying or build_profile) and not blockers and complete
        summary = {'schema': CAMPAIGN_SCHEMA, 'identity': self.identity,
                   'identity_sha256': self.identity_sha256, 'path': str(self.root),
                   'current_attempt_id': self.attempt_id, 'plan': self.plan,
                   'plan_sha256': digest(self.plan), 'history': history,
                   'history_sha256': digest(history), 'blockers': blockers,
                   'ledger_head': _load(self.root / 'ledger-head.json'),
                   'status': 'PASS' if eligible else ('RUNNING' if not self.finished else 'BLOCKED'),
                   'promotion_eligible': eligible,
                   'policy': ('All mandatory build prerequisites must pass' if build_profile else 'Every declared qualification repetition must pass') + '; failed, unfinished or corrupt attempts remain blockers'}
        atomic_json(self.root / 'summary.json', summary)
        return summary

    def finish(self, result):
        if self.finished:
            raise ValueError('Attempt was already finalized')
        immutable_json(self.attempt / 'result.json', result)
        immutable_json(self.attempt / 'seal.json', {
            'start_sha256': sha(self.attempt / 'start.json'),
            'result_sha256': sha(self.attempt / 'result.json')})
        self.finished = True
        return self.snapshot()

    def close(self):
        if self._lock and not self._lock.closed:
            self._lock.close()


def validate_campaign(matrix, expected_cases=None, verify_files=True):
    try:
        return _validate_campaign(matrix, expected_cases, verify_files)
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise ValueError('USB campaign evidence is missing or malformed: '+str(error)) from error


def _validate_campaign(matrix, expected_cases=None, verify_files=True):
    """Reject an incomplete, retried-to-green, stale or tampered promotion record.

    Image assembly should pass its current complete CASES registry. Published
    metadata can be checked with verify_files=False; assembly checks the actual
    immutable attempt records and case evidence on disk as well.
    """
    if matrix.get('schema') != MATRIX_SCHEMA or matrix.get('status') != 'PASS':
        raise ValueError('USB qualification requires a passing matrix v3 record')
    plan = matrix.get('plan', {})
    expected = list(expected_cases if expected_cases is not None else plan.get('expected_cases', []))
    if (plan.get('profile') != 'qualification' or plan.get('debug_subset') or
            plan.get('repetitions', 0) < MIN_QUALIFICATION_REPEATS):
        raise ValueError('USB promotion requires the declared repeated qualification profile')
    if not expected or len(expected) != len(set(expected)):
        raise ValueError('USB expected-case registry is empty or duplicated')
    validate_infrastructure_prerequisites(matrix, verify_files=verify_files)
    if plan.get('expected_cases') != expected or plan.get('selected_cases') != expected:
        raise ValueError('USB qualification case plan differs from the complete registry')
    required = [name + '@' + str(repeat + 1)
                for repeat in range(plan['repetitions']) for name in expected]
    if (not matrix.get('complete_matrix') or matrix.get('skipped') or
            matrix.get('not_executed') or matrix.get('expected_executions') != required or
            matrix.get('executed') != required):
        raise ValueError('USB qualification has missing, skipped or incomplete executions')
    cases = matrix.get('cases', {})
    if set(cases) != set(expected):
        raise ValueError('USB qualification is missing a required case result')
    paths_seen = set()
    for name in expected:
        case = cases[name]
        runs = case.get('runs', [])
        if (case.get('status') != 'PASS' or len(runs) != plan['repetitions'] or
                any(run.get('status') != 'PASS' for run in runs)):
            raise ValueError('USB qualification case did not pass every repetition: ' + name)
        for repeat, run in enumerate(runs,1):
            if name == 'eoo-backpressure':
                validate_tail_backpressure_run(run)
            if run.get('repeat') != repeat:
                raise ValueError('USB qualification has a missing or duplicated repetition: '+name)
            for key in ('log_path','case_record_path'):
                if not run.get(key) or run[key] in paths_seen:
                    raise ValueError('USB repetitions reused or omitted retained evidence: '+name)
                paths_seen.add(run[key])
        if verify_files:
            for run in runs:
                for path_key, sha_key in (('log_path', 'log_sha256'), ('case_record_path', 'case_record_sha256')):
                    if not run.get(path_key) or sha(run[path_key]) != run.get(sha_key):
                        raise ValueError('Retained USB case evidence changed: ' + name)
    campaign = matrix.get('campaign', {})
    if campaign.get('plan') != plan or not matrix.get('promotion_eligible'):
        raise ValueError('USB campaign is not the declared qualifying plan')
    bare = {key: value for key, value in matrix.items() if key not in ('campaign', 'promotion_eligible')}
    validate_history(campaign, matrix.get('identity'), bare, verify_files=verify_files)
    return campaign


def validate_tail_backpressure_run(run):
    """A nominal case PASS cannot substitute for exercising finite SRC pressure."""
    proof = run.get('tail_backpressure', {})
    queue, hold, audit = (proof.get(key, {}) for key in ('render_queue','render_hold','render_audit'))
    if proof.get('status') != 'PASS' or audit.get('status') != 'PASS':
        raise ValueError('Tail backpressure exercise or independent render audit is missing')
    required = ('limit_frames','output_bound_frames','high_water_frames','deferred_calls',
                'final_flush_calls','final_flush_frames','generated_frames','written_frames')
    if (any(type(queue.get(key)) is not int for key in required) or
            queue['limit_frames'] != 22050 or queue['output_bound_frames'] != 4608 or
            not 17442 < queue['high_water_frames'] <= 22050 or queue['deferred_calls'] <= 0 or
            queue['final_flush_calls'] < 2 or queue['final_flush_frames'] <= 0 or
            queue['generated_frames'] <= 0 or queue['generated_frames'] != queue['written_frames']):
        raise ValueError('Tail backpressure did not exercise the declared full-capacity SRC bound and complete flush')
    if (any(type(hold.get(key)) is not int for key in ('started_ms','ended_ms','hits')) or
            hold['ended_ms']-hold['started_ms'] < 150 or hold['hits'] <= 0 or audit.get('count',0) <= 0 or
            any(audit.get(key) != queue[key] for key in ('generated_frames','final_flush_calls','final_flush_frames'))):
        raise ValueError('Tail backpressure hold, audit and final counters disagree')
    harness = run.get('harness', {})
    if (harness.get('render_queue') != queue or harness.get('render_hold') != hold or
            harness.get('turns') != 1 or harness.get('eoo') != 1 or harness.get('rf_clear_confirmed') is not True):
        raise ValueError('Tail backpressure proof does not match the completed controller turn')
    turns = run.get('actual_sink_turns', [])
    if (len(turns) != 1 or turns[0].get('status') != 'PASS' or
            turns[0].get('content_match',{}).get('status') != 'PASS' or
            turns[0].get('content_match',{}).get('method') != 'full_modem_waveform_and_100ms_windows' or
            turns[0].get('content_match',{}).get('complete_reference_covered') is not True):
        raise ValueError('Tail backpressure lacks complete actual-sink waveform evidence')
    return proof


def validate_infrastructure_prerequisites(matrix, verify_files=True):
    """Prove that a real unexpected runtime fault remains a failed child test.

    This is a prerequisite about the test machinery. Its expected-negative
    child must never be counted as a passing functional controller case.
    """
    if matrix.get('plan', {}).get('infrastructure_prerequisites') != list(INFRASTRUCTURE_PREREQUISITES):
        raise ValueError('USB campaign omits its mandatory infrastructure failure-preservation prerequisite')
    prerequisites = matrix.get('infrastructure_prerequisites', {})
    if set(prerequisites) != set(INFRASTRUCTURE_PREREQUISITES):
        raise ValueError('USB infrastructure prerequisite evidence is missing')
    identity = matrix.get('identity', {})
    for name in INFRASTRUCTURE_PREREQUISITES:
        entry = prerequisites[name]
        proof = entry.get('record', {})
        if (entry.get('status') != 'PASS' or entry.get('supervision',{}).get('exit_code') != 0 or
                entry.get('supervision',{}).get('timed_out') is not False or proof.get('status') != 'PASS' or
                proof.get('schema') != 'x6200.usb.infrastructure.v1' or
                proof.get('kind') != 'unexpected-runtime-failure' or
                entry.get('record_sha256') != digest(proof) or proof.get('radio_accessed') is not False):
            raise ValueError('USB infrastructure prerequisite is not a valid expected-negative proof')
        expected_identity = {'source_commit': identity.get('source_commit'), 'build_id': identity.get('build_id'),
                             'controller_binary_sha256': identity.get('test_binary_sha256'),
                             'runner_sha256': identity.get('runner_sha256'),
                             'fault_injector_sha256': matrix.get('fault_injector_sha256'),
                             'fixture_sha256': {'speech': identity.get('speech_sha256'), 'modem': identity.get('modem_sha256')}}
        if any(proof.get(key) != value for key, value in expected_identity.items()):
            raise ValueError('USB infrastructure proof belongs to a different source, build, binary or fixture')
        checks = proof.get('checks', {})
        if set(checks) != set(INFRASTRUCTURE_CHECKS) or any(checks[key] is not True for key in INFRASTRUCTURE_CHECKS):
            raise ValueError('USB infrastructure failure was not preserved at every required stage')
        if (type(proof.get('driver_exit_code')) is not int or proof['driver_exit_code'] == 0 or
                proof.get('driver_status') != 'FAIL' or type(proof.get('harness_exit_code')) is not int or
                proof['harness_exit_code'] <= 0 or proof.get('harness_status') != 'FAIL' or
                proof.get('terminal_fault_code') != 13 or proof.get('terminal_fault_origin') != 'modem-buffer-budget' or
                proof.get('collector_status') != 'BLOCKED'):
            raise ValueError('Unexpected runtime failure was converted into a successful functional test')
        files = proof.get('evidence_files', {})
        if not set(INFRASTRUCTURE_FILES) <= set(files):
            raise ValueError('USB infrastructure proof did not retain its failed child evidence')
        if verify_files:
            report = Path(entry['report_path'])
            if (sha(report) != entry.get('report_sha256') or _load(report) != proof or
                    sha(entry['log_path']) != entry.get('log_sha256')):
                raise ValueError('USB infrastructure report was removed or changed')
            for filename in INFRASTRUCTURE_FILES:
                item = files[filename]
                if sha(item['path']) != item.get('sha256'):
                    raise ValueError('USB infrastructure child evidence changed: ' + filename)
            case = _load(files['case_result']['path'])
            failure = _load(files['harness_failure']['path'])
            completion = _load(files['completion']['path'])
            collector = _load(files['collector_report']['path'])
            fault = _load(files['first_fault']['path']).get('first_fault', {})
            if (case.get('status') != 'FAIL' or case.get('controller_exit_code') != proof['harness_exit_code'] or
                    failure.get('status') != 'FAIL' or failure.get('exit_code') != proof['harness_exit_code'] or
                    failure.get('primary_fault') != 13 or failure.get('primary_origin') != 'modem-buffer-budget' or
                    any(failure.get(key) is not True for key in ('cleanup_requested','stopped','closed')) or
                    completion.get('terminal') is not True or completion.get('fault') != 13 or
                    collector.get('status') != 'BLOCKED' or collector.get('ready_for_rf_test') is not False or
                    fault.get('code') != 13 or fault.get('origin') != 'modem-buffer-budget'):
                raise ValueError('Retained infrastructure child artifacts contradict the failure-preservation proof')
    return prerequisites


def validate_history(campaign, identity, current_result, verify_files=True):
    """Validate sealed attempt history shared by builds and controller matrices."""
    try:
        return _validate_history(campaign, identity, current_result, verify_files)
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise ValueError('Campaign evidence is missing or malformed: ' + str(error)) from error


def _validate_history(campaign, identity, current_result, verify_files):
    history = campaign.get('history', [])
    if (not isinstance(identity,dict) or not isinstance(history,list) or campaign.get('schema') != CAMPAIGN_SCHEMA or campaign.get('status') != 'PASS' or
            not campaign.get('promotion_eligible') or campaign.get('blockers') or not history or
            digest(history) != campaign.get('history_sha256') or
            digest(campaign.get('identity')) != campaign.get('identity_sha256') or
            campaign.get('identity') != identity):
        raise ValueError('Campaign has unresolved failures or invalid history')
    if verify_files:
        campaign_path = Path(campaign['path'])
        if _load(campaign_path / 'identity.json') != identity or _history(campaign_path) != history:
            raise ValueError('Campaign history has changed or omits a retained attempt')
    current = None
    previous = None
    for sequence, entry in enumerate(history, 1):
        start, result, seal = entry.get('start'), entry.get('result'), entry.get('seal')
        if (entry.get('status') != 'PASS' or not isinstance(start, dict) or not isinstance(result, dict) or
                result.get('status') != 'PASS' or digest(start) != entry.get('start_sha256') or
                digest(result) != entry.get('result_sha256') or
                seal != {'start_sha256': entry.get('start_sha256'), 'result_sha256': entry.get('result_sha256')} or
                digest(seal) != entry.get('seal_sha256') or
                start.get('identity_sha256') != campaign['identity_sha256'] or
                digest(start.get('plan')) != start.get('plan_sha256')):
            raise ValueError('Campaign contains failed, unfinished or altered evidence')
        ledger = entry.get('ledger', {})
        if (ledger != {'sequence': sequence, 'attempt_id': entry['attempt_id'],
                       'start_sha256': entry['start_sha256'], 'previous_sha256': previous} or
                digest(ledger) != entry.get('ledger_sha256')):
            raise ValueError('Campaign attempt ledger has missing or altered entries')
        previous = entry['ledger_sha256']
        if verify_files:
            path = Path(entry['path'])
            for name, digest_key in (('start', 'start_sha256'), ('result', 'result_sha256'), ('seal', 'seal_sha256')):
                if sha(path / (name + '.json')) != entry[digest_key]:
                    raise ValueError('Immutable campaign evidence changed: ' + entry['attempt_id'])
        if entry['attempt_id'] == campaign.get('current_attempt_id'):
            if start.get('plan') != campaign.get('plan'):
                raise ValueError('Campaign plan differs from its immutable current attempt')
            current = result
    if campaign.get('ledger_head') != {'sequence': len(history), 'entry_sha256': previous}:
        raise ValueError('Campaign ledger head does not match its retained history')
    if current != current_result:
        raise ValueError('Report is not the current sealed campaign result')
    return campaign
