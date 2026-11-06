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
