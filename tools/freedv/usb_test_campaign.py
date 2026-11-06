#!/usr/bin/env python3
"""Durable, fail-closed history for the USB controller qualification campaign.

The files here are software evidence, never RF qualification. An attempt can be
replayed for diagnosis, but a later successful attempt cannot erase an earlier
failed or abandoned attempt with the same source/configuration identity.
"""
import hashlib
import json
import os
import re
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
            validate_capture_close_run(run)
            if name in ('capture-release-split','capture-release-held150'):
                validate_capture_source_boundary_run(run,name,verify_files=verify_files)
            if name in ('capture-close-missing','capture-close-invalid','capture-close-stop'):
                validate_capture_close_failure_run(run,name,verify_files=verify_files)
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


def validate_capture_close_run(run):
    """Validate finite capture intervals; this is not physical PTT latency."""
    proof = run.get('capture_close', {})
    harness = run.get('harness', {})
    expected_rf = harness.get('total_turns', harness.get('turns'))
    turns = proof.get('turns')
    if (proof.get('status') != 'PASS' or proof.get('deadline_ms') != 750 or
            proof.get('frontier_basis') != 'Pulse capture production; no monitor playback correction' or
            proof.get('physical_latency') != 'UNMEASURED' or not isinstance(turns, list) or
            type(expected_rf) is not int or expected_rf < 0 or
            type(proof.get('completed_rf_turns')) is not int or type(proof.get('completed_intervals')) is not int or
            proof.get('completed_rf_turns') != expected_rf or proof.get('completed_intervals') != len(turns) or
            type(proof.get('abandoned_intervals')) is not int or proof['abandoned_intervals'] < 0):
        raise ValueError('Finite capture-close proof or completed RF interval accounting is missing')
    if len(turns) < expected_rf or sum(turn.get('rf_completed') is True for turn in turns) != expected_rf:
        raise ValueError('A completed RF turn is missing its finite capture interval')
    if len(turns) > expected_rf and not (type(harness.get('fault')) is int and harness['fault'] > 1):
        raise ValueError('An uncompleted RF interval cannot count as a successful capture/voice turn')
    seen = set()
    per_trace = {}
    for turn in turns:
        trace, number = turn.get('trace_file'), turn.get('turn')
        if (turn.get('status') != 'PASS' or type(turn.get('rf_completed')) is not bool or
                not isinstance(trace, str) or not trace or Path(trace).name != trace or
                type(number) is not int or number < 1 or (trace, number) in seen):
            raise ValueError('Capture-close interval identity is missing or duplicated')
        seen.add((trace, number))
        if number != per_trace.get(trace, 0) + 1:
            raise ValueError('Capture-close intervals are not contiguous within their session')
        per_trace[trace] = number
        fields = ('start_index','cutoff_index','consumed_index','accepted_frames','mic_tap_start','mic_tap_end',
                  'close_elapsed_ms','write_index_bytes','source_usec','transport_usec','sink_usec','frontier_extra_frames')
        if any(type(turn.get(key)) is not int or turn[key] < 0 for key in fields):
            raise ValueError('Capture-close indices, timing or counts are invalid')
        start, cutoff = turn['start_index'], turn['cutoff_index']
        extra = ((turn['source_usec'] + turn['transport_usec']) * 48000 + 999999) // 1000000
        if (turn['write_index_bytes'] % 2 or turn['frontier_extra_frames'] != extra or
                cutoff != turn['write_index_bytes']//2 + extra or start > cutoff or
                turn['consumed_index'] != cutoff or turn['accepted_frames'] != cutoff-start or
                turn['mic_tap_end']-turn['mic_tap_start'] != cutoff-start or turn['close_elapsed_ms'] > 750):
            raise ValueError('Capture-close PCM does not conserve its raw frozen source frontier')
        if ('submitted_first_index' in turn or 'submitted_end_index' in turn) and (
                turn.get('submitted_first_index') != start or turn.get('submitted_end_index') != cutoff):
            raise ValueError('Capture submission omitted or extended its declared finite interval')
        if 'release_fence_index' in turn or 'tail_frames' in turn:
            fence = turn.get('release_fence_index')
            if (type(fence) is not int or not start <= fence <= cutoff or
                    turn.get('tail_frames') != cutoff-fence):
                raise ValueError('Capture-close tail differs from its release fence')
        time_keys=('capture_started_ms','close_started_ms','timing_issued_ms','frozen_ms','reached_ms','closed_ms')
        if any(key in turn for key in time_keys):
            times=[turn.get(key) for key in time_keys]
            if (any(type(value) is not int or value<0 for value in times) or times!=sorted(times) or
                    turn['closed_ms']-turn['close_started_ms']!=turn['close_elapsed_ms'] or
                    turn.get('microphone_frame_budget',{}).get('active_ms')!=turn['close_started_ms']-turn['capture_started_ms']):
                raise ValueError('Capture-close epoch, asynchronous freeze and finite timing disagree')
        budget = turn.get('microphone_frame_budget', {})
        active, fragment = budget.get('active_ms'), budget.get('fragment_frames')
        if (budget.get('status') != 'PASS' or type(active) is not int or active < 0 or
                type(fragment) is not int or fragment <= 0 or budget.get('delivery_tolerance_percent') != 1 or
                budget.get('boundary_frames') != 2*fragment or budget.get('accepted_frames') != cutoff-start or
                budget.get('evaluated') is not (active >= 750)):
            raise ValueError('Capture-close changed or omitted the existing source delivery budget')
        expected = active*48
        lower, upper = max(0, expected*99//100 - 2*fragment), (expected*101 + 99)//100 + 2*fragment
        if (budget.get('minimum_frames') != lower or budget.get('maximum_frames') != upper or
                (active >= 750 and not lower <= cutoff-start <= upper)):
            raise ValueError('Capture-close source delivery did not meet the unchanged 1% plus two-fragment budget')
    return proof


def validate_capture_source_boundary_run(run, case, verify_files=True):
    close=validate_capture_close_run(run)
    proof=run.get('capture_source_boundary',{})
    turns=proof.get('turns',[])
    if (proof.get('status')!='PASS' or proof.get('case')!=case or proof.get('physical_latency')!='UNMEASURED' or
            close['completed_rf_turns']!=1 or close['completed_intervals']!=1 or close['abandoned_intervals']!=0 or
            len(turns)!=1 or any(re.fullmatch('[0-9a-f]{64}',str(proof.get(key,''))) is None
                                for key in ('observer_pcm_sha256','observer_index_sha256'))):
        raise ValueError('Independent indexed capture source-boundary proof is missing')
    row,interval=turns[0],close['turns'][0]
    count=interval['cutoff_index']-interval['start_index']
    split=row.get('split_block',{});exercise=row.get('exercise',{})
    mode='held150' if case.endswith('held150') else 'split'
    keys=('hold_started_ms','hold_ended_ms','callback_started_ms','barrier_started_ms','release_observed_ms',
          'callback_ended_ms','blocked_peeks','cutoff_index','sentinel_frames')
    if (row.get('status')!='PASS' or row.get('turn')!=interval['turn'] or row.get('mode')!=mode or count<=0 or
            row.get('compared_frames')!=count or row.get('accepted_frames')!=count or
            re.fullmatch('[0-9a-f]{64}',str(row.get('expected_source_sha256',''))) is None or
            row.get('expected_source_sha256')!=row.get('accepted_sha256') or
            row.get('post_cutoff_excluded') is not True or row.get('post_cutoff_sentinel_excluded') is not True or
            row.get('sentinel_value')!=30001 or type(row.get('post_cutoff_frames_observed')) is not int or
            row['post_cutoff_frames_observed']<=0 or type(row.get('post_cutoff_sentinel_frames')) is not int or
            row['post_cutoff_sentinel_frames']<=0 or row['post_cutoff_sentinel_frames']>row['post_cutoff_frames_observed'] or
            any(type(split.get(key)) is not int for key in ('start_index','end_index','cutoff_index')) or
            not split['start_index']<split['cutoff_index']<split['end_index'] or split['cutoff_index']!=interval['cutoff_index'] or
            any(type(exercise.get(key)) is not int or exercise[key]<0 for key in keys)):
        raise ValueError('Indexed capture did not prove exact pre-cutoff acceptance and post-cutoff exclusion')
    if (exercise['hold_ended_ms']-exercise['hold_started_ms']<150 or exercise['cutoff_index']!=interval['cutoff_index'] or
            exercise['sentinel_frames']!=row['post_cutoff_sentinel_frames'] or
            not exercise['release_observed_ms']<=interval['close_started_ms']<=interval['timing_issued_ms']):
        raise ValueError('Capture boundary was not exercised at the declared retained-release epoch')
    if mode=='held150':
        if not (exercise['hold_started_ms']+50<=exercise['release_observed_ms']<exercise['hold_ended_ms'] and
                interval['timing_issued_ms']<exercise['hold_ended_ms'] and interval['frozen_ms']<exercise['hold_ended_ms'] and
                exercise['blocked_peeks']>0):
            raise ValueError('Held capture delivery did not overlap release and asynchronous frontier freeze')
    elif not (exercise['hold_ended_ms']<=exercise['callback_started_ms']<=exercise['barrier_started_ms']<=
              exercise['release_observed_ms']<exercise['callback_ended_ms'] and interval.get('cutoff_split_frames',0)>0):
        raise ValueError('Capture callback did not straddle the retained release and finite cutoff')
    if verify_files:
        from usb_test_support import indexed_capture_source_match
        root=Path(run['case_record_path']).parent/'result'
        pcm,index=root/'mic-observer.pcm',root/'mic-observer.jsonl'
        if sha(pcm)!=proof['observer_pcm_sha256'] or sha(index)!=proof['observer_index_sha256']:
            raise ValueError('Independent capture source artifacts changed')
        source=pcm.read_bytes();rows=[json.loads(line) for line in index.read_text().splitlines() if line.strip()]
        microphone=(root/(Path(interval['trace_file']).stem+'-mic.raw')).read_bytes()
        actual=indexed_capture_source_match(interval,microphone,rows,source)
        if any(actual[key]!=row.get(key) for key in actual):
            raise ValueError('Indexed capture source-boundary summary disagrees with exact retained PCM')
        accepted=microphone[interval['mic_tap_start']*2:interval['mic_tap_end']*2]
        sentinel=(30001).to_bytes(2,'little',signed=True)
        if any(accepted[offset:offset+2]==sentinel for offset in range(0,len(accepted),2)):
            raise ValueError('Post-cutoff sentinel entered the accepted microphone interval')
    return proof


def validate_capture_close_failure_run(run,case,verify_files=True):
    close=validate_capture_close_run(run);harness=run.get('harness',{});proof=run.get('capture_close_failure',{})
    mode=case.removeprefix('capture-close-')
    abandoned=proof.get('abandoned_interval',{})
    times=[proof.get(key) for key in ('requested_ms','real_ack_ms','fault_ms')]
    if (proof.get('status')!='PASS' or proof.get('mode')!=mode or proof.get('phase')!='drain' or
            any(type(value) is not int for value in times) or times!=sorted(times) or
            proof.get('withheld') is not (mode=='missing') or proof.get('fault')!=harness.get('fault') or
            proof.get('origin')!=harness.get('first_fault_origin') or proof.get('eoo')!=0 or
            proof.get('completed_turns')!=0 or harness.get('turns')!=0 or harness.get('eoo')!=0 or
            harness.get('keys')!=1 or harness.get('rf_clear_confirmed') is not True or
            harness.get('physical_tx') is not False or close['completed_intervals']!=0 or close['abandoned_intervals']<1 or
            proof.get('close_timeout_ms')!=750 or abandoned.get('complete') is not False or
            abandoned.get('close_timeout_ms')!=750 or abandoned.get('scope')!='pulse-capture-pipeline-frontier' or
            type(abandoned.get('close_started_ms')) is not int or abandoned['close_started_ms']>times[0]):
        raise ValueError('Asynchronous capture-close failure did not preserve its active DRAIN boundary and bounded clear')
    if mode=='invalid':
        if proof['fault']!=23 or proof['origin']!='microphone-close-frontier-invalid':
            raise ValueError('Invalid capture frontier was not rejected by its actual source guard')
    elif mode=='missing':
        causes={6:('pcm-queue-full','aggregate-queue-limit'),9:('modem-payload-underflow',),17:('microphone-close-timeout',)}
        if proof['fault'] not in causes or proof['origin'] not in causes[proof['fault']]:
            raise ValueError('An unrelated failure cannot prove withheld capture timing was bounded')
    elif mode=='stop':
        stopped=proof.get('stop_requested_ms')
        if (proof['fault']!=1 or proof['origin']!='local-stop' or type(stopped) is not int or
                not times[1]<=stopped<=times[2] or abandoned.get('frozen_ms')!=0 or abandoned.get('cutoff_index')!=0):
            raise ValueError('Late capture timing callback committed a frontier after local Stop')
    else:
        raise ValueError('Unknown asynchronous capture-close failure scenario')
    if verify_files:
        from usb_test_support import validate_capture_close_failure
        root=Path(run['case_record_path']).parent
        traces=sorted((root/'result').glob('freedv-usb-session*.jsonl'))
        rows=[json.loads(line) for path in traces for line in path.read_text().splitlines() if line.strip()]
        actual=validate_capture_close_failure(rows,harness,(root/'test.log').read_text(),mode)
        if actual!=proof:
            raise ValueError('Capture-close failure witness disagrees with its retained DRAIN trace')
    return proof


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
