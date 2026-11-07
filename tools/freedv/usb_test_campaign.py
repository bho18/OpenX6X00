#!/usr/bin/env python3
"""Durable, fail-closed history for the USB controller qualification campaign.

The files here are software evidence, never RF qualification. An attempt can be
replayed for diagnosis, but a later successful attempt cannot erase an earlier
failed or abandoned attempt with the same source/configuration identity.
"""
import hashlib
import json
import math
import os
import re
from pathlib import Path
import time
import uuid
from usb_host_resources import PRESSURE_COUNTERS, MIN_START_HEADROOM_BYTES, MIN_END_HEADROOM_BYTES

MATRIX_SCHEMA = 'x6200.usb.integration.matrix.v3'
CAMPAIGN_SCHEMA = 'x6200.usb.campaign.v1'
MIN_QUALIFICATION_REPEATS = 2
USB_OUTPUT_CONTRACT_VERSION = 1
INFRASTRUCTURE_PREREQUISITES = ('unexpected_runtime_failure',)
INFRASTRUCTURE_CHECKS = ('driver_failed', 'harness_failed', 'terminal_fault_retained', 'collector_blocked')
INFRASTRUCTURE_FILES = ('case_result', 'harness_failure', 'completion', 'collector_report', 'first_fault', 'trace')


def host_resource_configuration(profile='qualification', reclaim_roots=(), min_start=MIN_START_HEADROOM_BYTES, min_end=MIN_END_HEADROOM_BYTES):
    return {'profile':profile,'reclaim_roots':sorted(set(str(Path(root).absolute()) for root in reclaim_roots)),
            'min_headroom_bytes':min_start,'min_end_headroom_bytes':min_end,'max_seconds':20,
            'max_files':200000,'max_advised_bytes':64*1024**3,'min_file_bytes':1024**2,
            'min_file_age_seconds':60,'pressure_counter_policy':list(PRESSURE_COUNTERS),
            'method':'explicit_readonly_regular_file_DONTNEED'}


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
    if any(type(value) is not int or value!=USB_OUTPUT_CONTRACT_VERSION for value in (
            matrix.get('usb_output_contract_version'),plan.get('usb_output_contract_version'),
            matrix.get('identity',{}).get('usb_output_contract_version'))):
        raise ValueError('USB qualification requires explicit playback output contract version1')
    if not expected or len(expected) != len(set(expected)):
        raise ValueError('USB expected-case registry is empty or duplicated')
    if re.fullmatch('[0-9a-f]{64}',str(matrix.get('identity',{}).get('transport_library_sha256',''))) is None:
        raise ValueError('Qualification did not bind its loaded transport library dependency')
    validate_host_resources(matrix, verify_files=verify_files)
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
            validate_output_contract_run(run,verify_files=verify_files,expected_identity=matrix['identity'])
            validate_backend_profile_run(run,name,verify_files=verify_files)
            validate_observer_admission_run(run,name,verify_files=verify_files)
            validate_capture_close_run(run,verify_files=verify_files)
            validate_operational_run(run,name,verify_files=verify_files)
            validate_sink_turns_run(run,name,verify_files=verify_files,expected_transport_sha256=matrix['identity']['transport_library_sha256'])
            if name in ('capture-release-split','capture-release-held150'):
                validate_capture_source_boundary_run(run,name,verify_files=verify_files)
            if name in ('capture-close-missing','capture-close-invalid','capture-close-stop'):
                validate_capture_close_failure_run(run,name,verify_files=verify_files)
            if name == 'eoo-backpressure':
                validate_tail_backpressure_run(run)
            if name == 'capture-close-tail120':
                validate_capture_pipeline_tail_run(run,verify_files=verify_files)
            if name in ('backend-timing-missing','backend-timing-invalid','backend-stop-before-timing','backend-stop-during-wait'):
                validate_backend_failure_run(run,name,verify_files=verify_files)
            if name == 'audio-controls':
                validate_audio_controls_run(run,verify_files=verify_files)
            if name == 'audio-gain-turn':
                validate_audio_gain_turn_run(run,verify_files=verify_files)
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
                details=_load(run['case_record_path'])
                for key in ('harness','capture_close','actual_sink_turns','capture_source_boundary',
                            'capture_close_failure','tail_backpressure','capture_pipeline_tail','usb_output_contract_version','output_contract','backend_profile','backend_failure','observer_admission','audio_controls','audio_gain_turn','operational','actual_headset_output'):
                    retained=details.get(key)
                    if key=='actual_sink_turns' and key not in details:
                        from test_usb_audio_integration import WAVEFORM_CASES
                        if name not in WAVEFORM_CASES:retained=[]
                    if run.get(key)!=retained:
                        raise ValueError('USB campaign summary differs from retained per-turn case evidence: '+name)
    campaign = matrix.get('campaign', {})
    if campaign.get('plan') != plan or not matrix.get('promotion_eligible'):
        raise ValueError('USB campaign is not the declared qualifying plan')
    bare = {key: value for key, value in matrix.items() if key not in ('campaign', 'promotion_eligible')}
    validate_history(campaign, matrix.get('identity'), bare, verify_files=verify_files)
    return campaign




def validate_output_contract_run(run,verify_files=True,expected_identity=None):
    try:
        return _validate_output_contract_run(run,verify_files,expected_identity)
    except (OSError,KeyError,TypeError,AttributeError) as error:
        raise ValueError('Required playback completion evidence is missing or malformed: '+str(error)) from error


def operational_proof(rows, completion, harness, case):
    """Grade operational semantics from the produced records, not UI labels."""
    from usb_rf_evidence import summarize_trace, validate_completion, validate_operational_readiness
    summary=summarize_trace(rows)
    headers=[row for row in rows if row.get('kind')=='session']
    if len(headers)!=1:raise ValueError('Operational exercise has no unique session')
    header=headers[0]
    manifest=dict(usb_startup_contract_version=4, firmware_commit=header.get('source_commit'), build_id=header.get('build_id'))
    validate_completion(summary,manifest,completion)
    receipts=[row for row in rows if row.get('kind')=='startup-readiness']
    if len(receipts)!=1:raise ValueError('Operational exercise never reached actual startup readiness')
    validate_operational_readiness(summary,rows,manifest,receipts[0])
    ready=receipts[0]
    if (harness.get('operational') is not True or harness.get('startup_ready') is not True or
            harness.get('qualification_complete') is not False or harness.get('qualification_turns')!=0 or
            harness.get('tx_timeout_seconds')!=(30 if case=='operational-timeout' else 180) or harness.get('physical_tx') is not False or
            harness.get('rf_clear_confirmed') is not True or
            any(harness.get(key)!=1 for key in ('rade_creations','encoder_creations','decoder_creations')) or
            any(row.get('event') in ('qualification-start','qualification_complete') for row in rows)):
        raise ValueError('Operational exercise lost automatic readiness, warm codec, RX clearance, or truthful qualification')
    fault=10 if case=='operational-timeout' else 2 if case=='operational-device-loss' else 1 if case=='operational-abort' else 0
    expected_turns=int(case in ('operational-leave-tx','operational-key-retune'))
    if (harness.get('fault')!=fault or harness.get('turns')!=expected_turns or
            harness.get('eoo')!=expected_turns or
            completion.get('completion_reason')!=('fault' if fault>1 else 'stop' if fault else 'navigation')):
        raise ValueError('Operational exit did not preserve graceful completion versus abort/fault semantics')
    revision=2 if case in ('operational-retune','operational-receive-only','operational-key-retune') else 1
    if harness.get('tuning_revision')!=revision or harness.get('configuration_ready') is not True:
        raise ValueError('Operational configuration revision was not restored before exit')
    if case=='operational-retune' and harness.get('rx_synthesis_blocks',0)<4:
        raise ValueError('Operational RX retune never decoded the public RF signal')
    if case=='operational-held-ptt' and ready['ptt_release_sequence']<1:
        raise ValueError('Held startup PTT was admitted without a real release')
    if case in ('operational-leave-tx','operational-abort','operational-device-loss','operational-timeout'):
        if harness.get('physical_ptt_pressed') is not True or harness.get('physical_release_sequence')!=0:
            raise ValueError('Navigation/abort fabricated a physical PTT release')
    closes=[row for row in rows if row.get('kind')=='capture-close' and row.get('complete') is True]
    if case=='operational-leave-tx' and (len(closes)!=1 or closes[0].get('close_trigger')!='navigation' or
            closes[0].get('physical_release_sequence')!=0):
        raise ValueError('Graceful navigation omitted its independent finite capture boundary')
    if case=='operational-key-retune':
        cancellations=[row for row in rows if row.get('event')=='key-admission-cancelled' and row.get('operation')=='tuning-revision' and row.get('state') in (2,7)]
        tx=[row for row in rows if row.get('kind')=='event' and row.get('event')=='state' and row.get('state')==3]
        if (len(cancellations)!=1 or len(tx)!=1 or rows.index(cancellations[0])>=rows.index(tx[0]) or
                cancellations[0]['ms']>=tx[0]['ms'] or harness.get('key_attempts')!=2 or harness.get('key_denials')!=1 or
                harness.get('keys')!=1 or harness.get('physical_release_sequence')!=2 or
                harness.get('physical_ptt_pressed') is not False or len(closes)!=1 or closes[0].get('close_trigger')!='ptt-release'):
            raise ValueError('Retune key rejection did not cancel without RF then require a real release/new TX press')
        early=[row for row in rows[:rows.index(tx[0])] if row.get('kind')=='sample']
        if any(row.get('submitted_mic',0) or row.get('turns',0) or row.get('rf_tx',False) for row in early):
            raise ValueError('Retune cancellation admitted RF or microphone speech before the next press')
    if case=='operational-timeout':
        on=[row for row in rows if row.get('kind')=='event' and row.get('event')=='state' and row.get('state')==3]
        fault_row=next((row for row in rows if row.get('event')=='first-fault' and row.get('status')==10 and row.get('operation')=='turn-deadline'),{})
        windows=harness.get('rf_intervals',[])
        if (len(on)!=1 or not fault_row or not 29500<=fault_row['ms']-on[0]['ms']<=31500 or
                len(windows)!=1 or not 29500000000<=windows[0]['off_after_ns']-windows[0]['on_before_ns']<=31500000000 or
                harness.get('first_fault_origin')!='turn-deadline'):
            raise ValueError('Operational timeout did not retain held TX beyond old20s and clear at configured30s')
    return dict(schema='x6200.usb.operational-exercise.v1', status='PASS', case=case,
                startup_readiness=ready, completion_reason=completion['completion_reason'],
                final_tuning_revision=revision, physical_release_sequence=harness.get('physical_release_sequence'),
                close_trigger='navigation' if case=='operational-leave-tx' else None,
                qualifier='Actual controller records with simulated radio control; no RF hardware qualification')


def validate_operational_headset_output(run,verify_files=True):
    proof=run.get('actual_headset_output',{});waveform=proof.get('waveform',{})
    if (proof.get('status')!='PASS' or type(proof.get('rendered_frames')) is not int or proof['rendered_frames']<=0 or
            waveform.get('status')!='PASS' or waveform.get('minimum_correlation')!=.95 or
            waveform.get('expected_gain')!=1.0 or waveform.get('allowed_window_normalized_error')!=.35 or
            proof.get('capture_frame_budget',{}).get('status')!='PASS'):
        raise ValueError('Operational retune requires complete decoded speech at the actual headset output')
    if verify_files:
        artifacts=proof.get('artifacts',{})
        if set(artifacts)!={'decoded','monitor','ledger','trace'}:raise ValueError('Operational RX output artifacts are incomplete')
        for artifact in artifacts.values():
            if sha(artifact['path'])!=artifact['sha256']:raise ValueError('Operational RX output artifact changed')
        from usb_test_support import modem_waveform_match
        reference=Path(artifacts['decoded']['path']).read_bytes();raw=Path(artifacts['monitor']['path']).read_bytes()
        begin,frames=proof.get('monitor_frame_start'),proof.get('observed_frames')
        if (len(reference)//2!=proof['rendered_frames'] or type(begin) is not int or begin<0 or
                type(frames) is not int or frames<=0 or (begin+frames)*2>len(raw)):
            raise ValueError('Operational headset waveform is missing its complete decoded interval')
        observed=raw[begin*2:(begin+frames)*2]
        matched=modem_waveform_match(reference,48000,observed,48000,expected_gain=1.0,
                                   maximum_lag_ms=proof['alignment_budget']['maximum_lag_ms'])
        if matched!=waveform:raise ValueError('Operational RX output waveform differs from retained PCM')
    return proof


def validate_operational_run(run,case,verify_files=True):
    try:
        return _validate_operational_run(run,case,verify_files)
    except (OSError,KeyError,TypeError,AttributeError) as error:
        raise ValueError('Operational startup/exit evidence is missing or malformed: '+str(error)) from error


def _validate_operational_run(run,case,verify_files=True):
    if not case.startswith('operational-'):return
    proof=run.get('operational')
    if not isinstance(proof,dict) or proof.get('schema')!='x6200.usb.operational-exercise.v1' or proof.get('status')!='PASS' or proof.get('case')!=case:
        raise ValueError('Required operational startup/exit exercise is missing')
    if case=='operational-retune':validate_operational_headset_output(run,verify_files=verify_files)
    if verify_files:
        entries=run.get('output_contract',{}).get('sessions',[])
        if len(entries)!=1:raise ValueError('Operational exercise requires one full session')
        directory=Path(run['case_record_path']).parent/'result';entry=entries[0]
        rows=[json.loads(line) for line in (directory/entry['trace_file']).read_text().splitlines() if line.strip()]
        completion=_load(directory/entry['completion_file'])
        if operational_proof(rows,completion,run['harness'],case)!=proof:
            raise ValueError('Operational proof differs from immutable controller records')
        readiness=_load(directory/(Path(entry['trace_file']).stem+'-readiness.json'))
        if readiness!=proof['startup_readiness']:
            raise ValueError('Operational proof has no matching immutable readiness sidecar')
    return proof


def validate_audio_controls_run(run,verify_files=True):
    """Require real sink master readbacks and fresh RX-only microphone previews."""
    proof=run.get('audio_controls',{});harness=run.get('harness',{})
    phases=proof.get('phases',[]);initial=proof.get('initial_sinks',{})
    names=('baseline','up','down','gain6','gain24','gainminus12','gain0')
    gains=(0,0,0,6,24,-12,0);volumes=(40,43,40,40,40,40,40)
    selected='alsa_output.usb-fixture';others=('radio_sink_fixture','unrelated_sink')
    if (proof.get('schema')!='x6200.usb.audio-controls.v1' or proof.get('status')!='PASS' or
            len(phases)!=len(names) or set(initial)!={selected,*others} or
            harness.get('keys')!=0 or harness.get('turns')!=0 or harness.get('total_turns',0)!=0 or
            harness.get('physical_tx') is not False or harness.get('fault')!=1):
        raise ValueError('Audio control exercise omitted its RX-only phases or attempted TX')
    for name,sink in initial.items():
        if (not isinstance(sink,dict) or type(sink.get('mute')) is not bool or
                len(sink.get('volume',[]))!=2 or
                any(type(value) is not int or not 0<=value<=65536 for value in sink['volume'])):
            raise ValueError('Audio control initial sink master readback is invalid')
    if (initial[selected]['mute'] is not True or
            any(abs(value-65536*percent/100)>1 for value,percent in zip(initial[selected]['volume'],(40,30)))):
        raise ValueError('Audio control fixture did not preserve an asymmetric muted initial headset master')
    generation=None;previous_ms=-1
    for index,(entry,name,gain,volume) in enumerate(zip(phases,names,gains,volumes)):
        s=entry.get('snapshot',{});pulse=entry.get('pulse',{})
        if (s.get('phase')!=name or s.get('state')!=1 or s.get('armed') is not False or
                s.get('rf_tx') is not False or s.get('keys')!=0 or s.get('turns')!=0 or
                s.get('submitted_mic')!=0 or s.get('mic_clipped_samples')!=0 or
                s.get('mic_meter_ready') is not True or s.get('mic_gain_db')!=gain or
                s.get('headset_volume_ready') is not True or s.get('headset_volume_percent')!=volume or
                s.get('headset_muted') is not (index==0) or
                any(s.get(key)!=count for key,count in (('rade_creations',1),('encoder_creations',1),
                    ('decoder_creations',1),('resampler_creations',4),('worker_creations',2)))):
            raise ValueError('Audio control phase lacks fresh RX preview, actual volume, or persistent codec objects')
        if (type(s.get('generation')) is not int or type(s.get('monotonic_ms')) is not int or
                s['monotonic_ms']<previous_ms or (generation is not None and generation!=s['generation'])):
            raise ValueError('Audio controls transitioned the codec generation or reused unordered receipts')
        generation=s['generation'];previous_ms=s['monotonic_ms']
        raw_rms,raw_peak,rms,peak=(s.get(key) for key in
            ('mic_input_rms_dbfs10','mic_input_peak_dbfs10','mic_rms_dbfs10','mic_peak_dbfs10'))
        if (any(type(value) is not int for value in (raw_rms,raw_peak,rms,peak)) or
                not -155<=raw_rms<=-145 or not -125<=raw_peak<=-115 or
                (gain!=24 and (abs(peak-raw_peak-gain*10)>2 or abs(rms-raw_rms-gain*10)>2)) or
                (gain==24 and (peak!=0 or not -30<=rms<=0))):
            raise ValueError('Audio control preview did not meter the known live input and selected saturating gain')
        if set(pulse)!=set(initial) or any(pulse.get(other)!=initial[other] for other in others):
            raise ValueError('Headset volume control changed an unrelated or modem sink')
        headset=pulse[selected];actual=headset.get('volume',[])
        if (headset.get('mute') is not (index==0) or len(actual)!=2 or
                any(type(value) is not int for value in actual) or
                abs(max(actual)-65536*volume/100)>1 or
                abs(actual[1]*initial[selected]['volume'][0]-actual[0]*initial[selected]['volume'][1])>65536):
            raise ValueError('Actual headset master lost rapid detents, channel balance, or unmute')
        if verify_files:
            for kind in ('snapshot','pulse'):
                path=entry.get(kind+'_path')
                if not path or sha(path)!=entry.get(kind+'_sha256') or _load(path)!=entry[kind]:
                    raise ValueError('Retained audio control readback changed')
    return proof


def validate_audio_gain_turn_run(run,verify_files=True):
    """Live controls must preserve real codec TX/drain/RX and other sink masters."""
    proof=run.get('audio_gain_turn',{});harness=run.get('harness',{})
    phases=proof.get('phases',[]);initial=proof.get('initial_sinks',{})
    selected='alsa_output.usb-fixture';others=('radio_sink_fixture','unrelated_sink')
    if (proof.get('schema')!='x6200.usb.audio-gain-turn.v1' or proof.get('status')!='PASS' or
            len(phases)!=3 or set(initial)!={selected,*others} or harness.get('keys')!=3 or
            harness.get('turns')!=2 or harness.get('eoo')!=2 or harness.get('physical_tx') is not False or
            harness.get('fault')!=1 or harness.get('rf_clear_confirmed') is not True):
        raise ValueError('Live audio controls did not complete both TX/tail/RX turns')
    if (initial[selected].get('mute') is not True or len(initial[selected].get('volume',[]))!=2 or
            any(abs(value-65536*percent/100)>1 for value,percent in zip(initial[selected]['volume'],(40,30)))):
        raise ValueError('Live audio controls omitted the asymmetric muted headset baseline')
    previous=None
    for entry,name,gain,volume,state,keys,turns in zip(phases,
            ('gain-turn-up','gain-turn-down','gain-turn-rx'),(6,-6,0),(43,40,40),(3,3,1),(2,2,3),(0,0,2)):
        s=entry.get('snapshot',{});pulse=entry.get('pulse',{})
        if (s.get('phase')!=name or s.get('state')!=state or s.get('armed') is not True or
                s.get('rf_tx') is not (state==3) or s.get('keys')!=keys or s.get('turns')!=turns or
                type(s.get('submitted_mic')) is not int or s['submitted_mic']<0 or
                type(s.get('mic_clipped_samples')) is not int or s['mic_clipped_samples']<0 or
                s.get('mic_meter_ready') is not True or s.get('mic_gain_db')!=gain or
                s.get('headset_volume_ready') is not True or s.get('headset_volume_percent')!=volume or
                s.get('headset_muted') is not False or
                any(s.get(key)!=count for key,count in (('rade_creations',1),('encoder_creations',1),
                    ('decoder_creations',1),('resampler_creations',4),('worker_creations',2)))):
            raise ValueError('Live audio controls lost its applied gain, volume, or persistent codec objects')
        if (type(s.get('generation')) is not int or type(s.get('monotonic_ms')) is not int or
                (previous is not None and (s['monotonic_ms']<previous['monotonic_ms'] or
                 s['submitted_mic']<=previous['submitted_mic'] or s['mic_clipped_samples']<previous['mic_clipped_samples'] or
                 (state==3 and s['generation']!=previous['generation']) or
                 (state==1 and s['generation']<=previous['generation'])))):
            raise ValueError('Gain updates reset codec generation or interrupted accepted microphone progress')
        if set(pulse)!=set(initial) or any(pulse.get(other)!=initial[other] for other in others):
            raise ValueError('Live headset control changed an unrelated or modem sink')
        actual=pulse[selected].get('volume',[])
        if (pulse[selected].get('mute') is not False or len(actual)!=2 or
                any(type(value) is not int for value in actual) or
                abs(max(actual)-65536*volume/100)>1 or
                abs(actual[1]*initial[selected]['volume'][0]-actual[0]*initial[selected]['volume'][1])>65536):
            raise ValueError('Live headset master lost detents, balance, or unmute')
        if verify_files:
            for kind in ('snapshot','pulse'):
                path=entry.get(kind+'_path')
                if not path or sha(path)!=entry.get(kind+'_sha256') or _load(path)!=entry[kind]:
                    raise ValueError('Retained live audio control readback changed')
        previous=s
    return proof


def _validate_output_contract_run(run,verify_files=True,expected_identity=None):
    """A new build must prove the advertised backend contract in every session."""
    from usb_output_contract import validate_output_contract,SCOPE
    proof=run.get('output_contract',{});identity=run.get('identity',{});harness=run.get('harness',{})
    expected=expected_identity or identity
    if not all(isinstance(value,dict) for value in (proof,identity,harness,expected)):
        raise ValueError('Required advertised playback completion contract is missing or malformed')
    if (type(run.get('usb_output_contract_version')) is not int or run['usb_output_contract_version']!=1 or
            proof.get('schema')!='x6200.usb.output-contract-set.v1' or proof.get('status')!='PASS' or
            type(proof.get('usb_output_contract_version')) is not int or proof['usb_output_contract_version']!=1 or
            identity.get('status')!='PASS' or any(identity.get(key)!=expected.get(key) for key in ('source_commit','build_id'))):
        raise ValueError('Required advertised playback completion contract is missing or belongs to another build')
    if 'test_binary_sha256' in expected and identity.get('binary_sha256')!=expected['test_binary_sha256']:
        raise ValueError('Playback completion contract identifies another controller binary')
    sessions=proof.get('sessions')
    if not isinstance(sessions,list) or not sessions:
        raise ValueError('Playback completion contract omits all controller sessions')
    completed=0;seen=set();ids=[]
    for session in sessions:
        if not isinstance(session,dict):raise ValueError('Playback contract session is malformed')
        for key in ('trace_file','completion_file'):
            value=session.get(key)
            if not isinstance(value,str) or not value or Path(value).name!=value or value in seen:
                raise ValueError('Playback contract session files are absent, unsafe or reused')
            seen.add(value)
        trace=Path(session['trace_file'])
        if (trace.suffix!='.jsonl' or session['completion_file']!=trace.stem+'-completion.json' or
                session.get('schema')!='x6200.usb.output-contract.v1' or session.get('status')!='PASS' or
                type(session.get('usb_output_contract_version')) is not int or session['usb_output_contract_version']!=1 or session.get('scope')!=SCOPE or
                any(type(session.get(key)) is not int or session[key]<0 for key in
                    ('completed_turns','completed_intervals','interrupted_intervals','receipt_count')) or
                session['completed_intervals']<session['completed_turns'] or session['interrupted_intervals']>1 or
                session['receipt_count'] not in (session['completed_turns'],session['completed_turns']+1) or
                session['receipt_count']!=session['completed_intervals']+session['interrupted_intervals'] or
                any(re.fullmatch('[0-9a-f]{64}',str(session.get(key,''))) is None
                    for key in ('trace_sha256','completion_sha256'))):
            raise ValueError('Playback contract session lacks complete typed turn/receipt accounting')
        completed+=session['completed_turns']
        if verify_files:
            root=Path(run['case_record_path']).parent/'result'
            trace_path,completion_path=root/session['trace_file'],root/session['completion_file']
            if sha(trace_path)!=session['trace_sha256'] or sha(completion_path)!=session['completion_sha256']:
                raise ValueError('Retained playback completion evidence changed')
            rows=[json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
            header=next((row for row in rows if row.get('kind')=='session'),{})
            expected_session={key:expected.get(key) for key in ('source_commit','build_id')}
            if header.get('session_id')!=trace.stem or header.get('session_id') in ids:
                raise ValueError('Playback completion session identity was reused or differs from its actual trace basename')
            expected_session['session_id']=trace.stem
            actual=validate_output_contract(rows,_load(completion_path),required_version=1,expected_identity=expected_session)
            if session!={**actual,'trace_file':trace.name,'completion_file':completion_path.name,
                          'trace_sha256':session['trace_sha256'],'completion_sha256':session['completion_sha256']}:
                raise ValueError('Playback completion summary differs from the actual typed backend receipts')
            ids.append(header['session_id'])
    expected_turns=harness.get('total_turns',harness.get('turns'))
    if (type(expected_turns) is not int or completed!=expected_turns or
            ('session_count' in harness and harness['session_count']!=len(sessions)) or
            (verify_files and identity.get('session_ids')!=ids)):
        raise ValueError('Playback contract sessions do not cover the controller completed turns and identities')
    return proof



def validate_headset_volume_flags(rows,required=False):
    """A unity headset client must respect the user's existing sink master."""
    streams=[row for row in rows if row.get('kind') in ('stream','audio-budget')]
    headset=[row for row in streams if row.get('stream')=='headset']
    if required and not headset:
        raise ValueError('Headset relative-volume request descriptor is missing')
    for row in streams:
        if row.get('stream') not in ('mic','radio-rx','headset','modem'):continue
        flags=row.get('requested_flags')
        if type(flags) is not int or bool(flags&0x40000) is not (row['stream']=='headset'):
            raise ValueError('Only the headset stream must request RELATIVE_VOLUME to preserve its sink master')


def validate_backend_profile_run(run,case,verify_files=True):
    profile=run.get('backend_profile',{})
    names=('legacy-low-latency','default','fixed-pipe')
    expected={'backend-fixed-pipe':'fixed-pipe','io-pause150':'fixed-pipe','rx-pause150':'default'}.get(case,'legacy-low-latency')
    if (not isinstance(profile,dict) or profile.get('schema')!='x6200.usb.backend-profile.v1' or
            profile.get('name') not in names or (expected is not None and profile['name']!=expected) or
            profile.get('passive_monitor') is not (case!='cold-idle') or
            profile.get('observer_excluded_for_idle') is not (case=='cold-idle') or type(profile.get('competing_monitor')) is not bool or
            type(profile.get('null_norewinds')) is not bool or
            profile.get('competing_monitor') is not False or
            profile.get('modem_sink_module')!=('module-pipe-sink' if profile.get('name')=='fixed-pipe' else 'module-null-sink') or
            profile.get('null_norewinds') is not (profile.get('name')=='legacy-low-latency')):
        raise ValueError('USB backend profile is missing or does not exercise the declared buffering/observer configuration')
    if verify_files:
        text=(Path(run['case_record_path']).parent/'default.pa').read_text()
        line=next((line for line in text.splitlines() if line.startswith('load-module ') and 'sink_name=radio_sink_fixture' in line),'')
        if (profile['modem_sink_module'] not in line or ('norewinds=1' in line)!=profile['null_norewinds']):
            raise ValueError('Declared backend profile differs from the actual private Pulse sink configuration')
        seen=set()
        for session in run['output_contract']['sessions']:
            rows=[json.loads(value) for value in (Path(run['case_record_path']).parent/'result'/session['trace_file']).read_text().splitlines() if value.strip()]
            validate_headset_volume_flags(rows,required=case in ('audio-controls','audio-gain-turn'))
            for row in rows:
                name=row.get('stream')
                if row.get('kind') not in ('stream','audio-budget') or name not in ('mic','radio-rx','headset','modem'):continue
                capture=name in ('mic','radio-rx');flags=row.get('requested_flags');seen.add(name)
                if (type(flags) is not int or flags<0 or type(row.get('adjust_latency')) is not bool or
                        row['adjust_latency'] is not capture or bool(flags&0x2000) is not capture or
                        bool(flags&0x4000) is capture or
                        row.get('request_domain')!=('capture-fragments' if capture else 'per-stream-playback')):
                    raise ValueError('Playback requires per-stream EARLY_REQUESTS without ADJUST_LATENCY; capture retains its fragment-domain flags')
                if not capture and case!='buffer-contract-reject':
                    rate,channels=row.get('rate'),row.get('channels')
                    if (type(rate) is not int or rate<=0 or channels!=1 or
                            any(type(row.get(key)) is not int or row[key]<0 for key in ('maxlength','tlength','minreq','prebuf')) or
                            row['maxlength']>rate*2 or not 0<row['minreq']<row['tlength']<=row['maxlength'] or
                            (row['tlength']-row['minreq'])*1000<300*rate*2 or row['prebuf']*1000<300*rate*2):
                        raise ValueError('Actual playback attributes do not preserve the unchanged 300ms reserve contract')
        if run['harness'].get('total_turns',run['harness'].get('turns',0))>0 and seen!={'mic','radio-rx','headset','modem'}:
            raise ValueError('Completed output profile omitted a negotiated stream/request-domain descriptor')
    return profile



def validate_observer_admission_run(run,case,verify_files=True):
    from test_usb_audio_integration import WAVEFORM_CASES
    if case not in WAVEFORM_CASES and case!='rx-pause150':return
    proof=run.get('observer_admission',{});fixed=run.get('backend_profile',{}).get('name')=='fixed-pipe'
    if (not isinstance(proof,dict) or proof.get('schema')!='x6200.usb.observer-admission.v1' or
            proof.get('phase')!='after-initial-RX-before-PTT' or type(proof.get('ready_ns')) is not int or proof['ready_ns']<=0 or
            proof.get('first_block_required') is not (not fixed) or proof.get('first_block_confirmed') is not (not fixed) or
            proof.get('independent_fifo_reader') is not fixed or proof.get('cold_idle_unobserved') is not False or
            any(item.get('on_before_ns',0)<proof['ready_ns'] for item in run['harness'].get('rf_intervals',[]))):
        raise ValueError('Passive observer admission must follow startup grants and precede every physical PTT/RF interval')
    if verify_files:
        root=Path(run['case_record_path']).parent
        if 'FIXTURE_OUTPUT_READY' not in (root/'test.log').read_text() or (root/'result/output-observer-ready').read_text().strip()!=str(proof['ready_ns']):
            raise ValueError('Observer admission does not match its actual controller/observer readiness handshake')
        first=run['output_contract']['sessions'][0]
        rows=[json.loads(line) for line in (root/'result'/first['trace_file']).read_text().splitlines() if line.strip()]
        initial=next((row for row in rows if row.get('event')=='state' and row.get('state')==1),{})
        if type(initial.get('ms')) is not int or initial['ms']*1000000>proof['ready_ns']:
            raise ValueError('Observer was admitted before initial RX completed')
        ledger=root/('headset-output.jsonl' if case=='rx-pause150' else 'radio-output.jsonl')
        records=[json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        ready=next((row for row in records if row.get('kind')=='ready'),{})
        flags=ready.get('flags')
        if (type(flags) is not int or flags<0 or flags&0x6000 or ready.get('adjust_latency') is not False or
                ready.get('early_requests') is not False or type(ready.get('monotonic_ns')) is not int or
                ready['monotonic_ns']>proof['ready_ns'] or ready.get('scope')!='passive rendered sink monitor; not physical DAC time'):
            raise ValueError('Observer requested sink latency or failed to acknowledge passive readiness')
        if not fixed and not any(row.get('kind')=='block' and type(row.get('received_ns')) is int and
                                 row['received_ns']<=proof['ready_ns'] for row in records):
            raise ValueError('Passive monitor had no actual received block before PTT admission')
    return proof


def _bounded_number(value,minimum,maximum):
    return type(value) in (int,float) and math.isfinite(value) and minimum<=value<=maximum





def validate_rf_window(turn,interval,run,verify_files=True):
    """Bind complete modem evidence to effective RF ownership, not a late crop."""
    proof=turn.get('rf_window',{});harness=run.get('harness',{})
    if (not isinstance(proof,dict) or proof.get('schema')!='x6200.usb.rf-window.v1' or
            proof.get('receipt_turn')!=interval['turn'] or proof.get('complete_reference_covered') is not True or
            re.fullmatch('[0-9a-f]{64}',str(proof.get('receipt_sha256',''))) is None or
            type(proof.get('backend_completed_ns')) is not int or proof['backend_completed_ns']<=0 or
            type(turn.get('monitor_frame_start')) is not int or turn['monitor_frame_start']<0):
        raise ValueError('Every transmitted turn requires its complete source-bound RF-window proof')
    rf_intervals=harness.get('rf_intervals',[])
    if not isinstance(rf_intervals,list):raise ValueError('Independent RF ownership intervals are missing')
    disabled=proof.get('status')=='NOT_APPLICABLE'
    if disabled:
        if (proof.get('scope')!='RF-disabled qualification' or proof.get('rf_disabled') is not True or
                any(item.get('on_after_ns',0)<=(turn['end_ms']+1)*1000000 and
                    item.get('off_before_ns',0)>=turn['start_ms']*1000000 for item in rf_intervals)):
            raise ValueError('RF-disabled qualification cannot claim a physical keyed RF interval')
    else:
        fields=('rf_on_before_ns','rf_on_after_ns','rf_off_before_ns','rf_off_after_ns')
        if (proof.get('status')!='PASS' or type(proof.get('rf_key_sequence')) is not int or proof['rf_key_sequence']<1 or
                any(type(proof.get(key)) is not int or proof[key]<0 for key in fields) or
                not proof['rf_on_before_ns']<=proof['rf_on_after_ns']<proof['rf_off_before_ns']<=proof['rf_off_after_ns'] or
                proof['backend_completed_ns']>proof['rf_off_before_ns'] or
                proof['rf_on_after_ns']>(turn['start_ms']+1)*1000000 or proof['rf_off_before_ns']<turn['start_ms']*1000000 or
                proof.get('all_signal_consumed_while_rf') is not True or proof.get('finite_deadline_before_rf_off') is not True):
            raise ValueError('Complete finite modem playback or backend completion crossed the actual RF interval')
        actual={'key_sequence':proof['rf_key_sequence'],**{key[3:]:proof[key] for key in fields}}
        if sum(item==actual for item in rf_intervals)!=1:
            raise ValueError('RF-window proof does not identify one actual controller RF ownership interval')
        fixed=proof.get('scope')=='paced FIFO backend model; not physical hardware'
        if fixed:
            if (proof.get('basis')!='actual_FIFO_read_intervals' or proof.get('late_signal_ns')!=0 or proof.get('early_signal_ns')!=0 or
                    proof.get('aligned_frame_start')!=turn['monitor_frame_start']+turn['decoder_control']['native_alignment']['offset_frames'] or
                    proof.get('reference_frames')!=turn['decoder_control']['transport_frames'] or
                    proof.get('last_signal_read_after_ns',proof['rf_off_before_ns']+1)>proof['rf_off_before_ns'] or
                    proof.get('first_signal_read_before_ns',0)<proof['rf_on_after_ns'] or
                    proof.get('finite_last_deadline_ns',proof['rf_off_before_ns']+1)>proof['rf_off_before_ns']):
                raise ValueError('Paced FIFO signal/EOO and finite tail were not wholly consumed while RF remained keyed')
            artifacts=proof.get('fifo_artifacts',{})
            if set(artifacts)!={'raw','timing','mono'} or any(not item.get('path') or
                    re.fullmatch('[0-9a-f]{64}',str(item.get('sha256',''))) is None for item in artifacts.values()):
                raise ValueError('Independent FIFO backend evidence is missing')
        elif proof.get('scope')!='pulse-reported backend model; not physical hardware' or proof.get('basis')!='fresh_backend_tail_before_RF_off':
            raise ValueError('Unsupported RF playback model or timing scope')
        if run.get('backend_profile',{}).get('name')=='fixed-pipe' and not fixed:
            raise ValueError('Fixed backend profile cannot qualify with only the virtual backend model')
    if verify_files:
        root=Path(run['case_record_path']).parent
        rows=[json.loads(line) for line in (root/'result'/turn['trace_file']).read_text().splitlines() if line.strip()]
        receipt=next((row for row in rows if row.get('kind')=='backend-tail' and row.get('turn')==interval['turn']),{})
        hashed=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if (receipt.get('complete') is not True or receipt.get('rf_tx_confirmed') is not True or
                hashed!=proof['receipt_sha256'] or proof['backend_completed_ns']!=receipt.get('completed_us',-1)*1000 or
                receipt.get('rf_disabled') is not disabled):
            raise ValueError('RF-window evidence differs from its exact typed backend completion receipt')
        if not disabled and proof.get('scope')=='paced FIFO backend model; not physical hardware':
            artifacts=proof['fifo_artifacts']
            for item in artifacts.values():
                path=Path(item['path']).resolve()
                if not path.is_relative_to(root.resolve()) or sha(path)!=item['sha256']:
                    raise ValueError('Retained independent FIFO backend artifacts changed')
            timing=[json.loads(line) for line in Path(artifacts['timing']['path']).read_text().splitlines() if line.strip()]
            header=next((row for row in timing if row.get('kind')=='session'),{})
            final=next((row for row in reversed(timing) if row.get('kind')=='terminal'),{})
            raw=Path(artifacts['raw']['path']).read_bytes();mono=Path(artifacts['mono']['path']).read_bytes()
            if (header.get('channels')!=2 or header.get('sample_width_bytes')!=2 or header.get('frame_bytes')!=4 or
                    len(raw)!=final.get('frames',-1)*4 or final.get('raw_bytes')!=len(raw) or len(mono)*2!=len(raw) or
                    any(raw[index:index+2]!=raw[index+2:index+4] or raw[index:index+2]!=mono[index//2:index//2+2]
                        for index in range(0,len(raw),4))):
                raise ValueError('FIFO timing and mono/stereo PCM recordings are inconsistent')
            begin=turn['monitor_frame_start'];control=turn['decoder_control'];frames=control['control_frames']
            stimulus=Path(turn['artifacts']['actual_stimulus']['path']).read_bytes()
            if begin+frames>len(mono)//2 or stimulus!=mono[begin*2:(begin+frames)*2]+b'\0\0'*(turn['observed']['input_frames']-frames):
                raise ValueError('Matched sink decoder did not consume the declared independent FIFO interval')
        from test_usb_audio_integration import rf_window_proof
        transport=Path(turn['artifacts']['transport441']['path']).read_bytes()
        actual=rf_window_proof(root,rows,turn,interval['turn'],transport,turn['decoder_control'],
                               turn['monitor_frame_start'],harness,fifo=not disabled and proof.get('basis')=='actual_FIFO_read_intervals')
        if proof!=actual:raise ValueError('RF-window summary differs from its actual backend/RF/consumption evidence')
    return proof


def validate_alignment_budget(turn):
    """Alignment derives from actual queues and the existing scheduler budget."""
    budget=turn.get('alignment_budget',{})
    if (not isinstance(budget,dict) or budget.get('schema')!='x6200.usb.alignment-budget.v1' or
            budget.get('basis')!='actual_output_queues_and_existing_scheduler_crop_budgets' or budget.get('stream')!='modem' or
            not _bounded_number(budget.get('stream_target_ms'),0,1000) or
            type(budget.get('backend_usec')) is not int or budget['backend_usec']<0 or
            any(budget.get(key)!=value for key,value in (('io_service_ms',150),('codec_quantum_ms',120),
                                                       ('margin_ms',30),('crop_context_ms',200))) or
            type(budget.get('maximum_lag_ms')) is not int or budget['maximum_lag_ms']!=math.ceil(
                budget['stream_target_ms']+budget['backend_usec']/1000+150+120+30+200)):
        raise ValueError('Per-turn waveform alignment is not derived from the declared output queues and existing service budgets')
    return budget


def validate_decoder_control(turn,verify_files=True,case_root=None,expected_transport_sha256=None):
    """Bind the same-rate real decoders to a complete independent SRC model."""
    alignment_budget=validate_alignment_budget(turn);maximum_lag_ms=alignment_budget['maximum_lag_ms']
    proof=turn.get('decoder_control',{})
    if (proof.get('schema')!='x6200.usb.decoder-control.v1' or proof.get('status')!='PASS' or
            proof.get('basis')!='independent_transport_reference' or proof.get('source_rate')!=48000 or
            proof.get('rate')!=44100 or proof.get('channels')!=1 or proof.get('gain_divisor')!=4 or
            proof.get('source_context')!='fixed_full_waveform_alignment' or proof.get('alignment_budget')!=alignment_budget):
        raise ValueError('Per-turn matched transport decoder control is missing or uses a different model')
    keys=('leading_frames','control_frames','source_frames','transport_frames')
    if (any(type(proof.get(key)) is not int or proof[key]<0 for key in keys) or
            proof['source_frames']<=0 or proof['transport_frames']<=0 or
            proof['leading_frames']+proof['transport_frames']>proof['control_frames']):
        raise ValueError('Matched decoder control omitted part of the independent transport reference')
    if (type(turn.get('source_tap_start')) is not int or type(turn.get('source_tap_end')) is not int or
            turn['source_tap_start']<0 or turn['source_tap_end']-turn['source_tap_start']!=proof['source_frames']):
        raise ValueError('Matched transport reference lacks exact source tap boundaries')
    alignment=proof.get('native_alignment',{})
    if (alignment.get('offset_frames')!=proof['leading_frames'] or alignment.get('search_radius_frames')!=8 or
            alignment.get('complete_transport_covered') is not True or
            not _bounded_number(alignment.get('normalized_rms_error'),0,float('inf'))):
        raise ValueError('Matched decoder control did not retain complete native sample alignment')
    transport=proof.get('transport',{});calls=transport.get('calls')
    if (transport.get('converter')!=2 or not isinstance(transport.get('library'),str) or not transport['library'] or
            not isinstance(transport.get('version'),str) or not transport['version'] or
            not transport.get('library_path') or re.fullmatch('[0-9a-f]{64}',str(transport.get('library_sha256',''))) is None or
            (expected_transport_sha256 is not None and transport.get('library_sha256')!=expected_transport_sha256) or
            transport.get('input_frames')!=proof['source_frames'] or transport.get('output_frames')!=proof['transport_frames'] or
            transport.get('eof_complete') is not True or not isinstance(calls,list) or not calls):
        raise ValueError('Independent transport SRC did not prove complete input and EOF output')
    for call in calls:
        if (call.get('stage') not in ('regular','src-final') or
                any(type(call.get(key)) is not int or call[key]<0 for key in ('input_frames','generated_frames')) or
                call['input_frames']>4096 or call['generated_frames']>4608 or
                (call['stage']=='src-final')!=(call['input_frames']==0)):
            raise ValueError('Independent transport SRC has invalid per-call conservation evidence')
    if (sum(call['input_frames'] for call in calls)!=proof['source_frames'] or
            sum(call['generated_frames'] for call in calls)!=proof['transport_frames'] or
            calls[-1]!={'stage':'src-final','input_frames':0,'generated_frames':0}):
        raise ValueError('Independent transport SRC input or final flush is incomplete')
    waveform=proof.get('matched_waveform',{})
    if (waveform.get('status')!='PASS' or waveform.get('method')!='full_modem_waveform_and_100ms_windows' or
            waveform.get('complete_reference_covered') is not True or waveform.get('expected_gain')!=1 or
            waveform.get('minimum_correlation')!=.95 or waveform.get('maximum_lag_ms')!=maximum_lag_ms or
            waveform.get('resampler_edge_allowance_ms')!=5 or waveform.get('allowed_window_normalized_error')!=.35 or
            not _bounded_number(waveform.get('correlation'),.95,1.000001) or
            not _bounded_number(waveform.get('lag_ms'),0,maximum_lag_ms) or
            not _bounded_number(waveform.get('fitted_gain'),.96,1.04) or
            not _bounded_number(waveform.get('maximum_window_normalized_error'),0,.35) or
            type(waveform.get('reference_frames_compared_at_8k')) is not int or waveform['reference_frames_compared_at_8k']<800):
        raise ValueError('Matched transport waveform lacks complete fixed-threshold sink comparison')
    artifacts=turn.get('artifacts',{})
    required={'source48','transport441','reference_stimulus','actual_stimulus','reference_decoded',
              'actual_decoded','reference_stats','actual_stats'}
    if set(artifacts)!=required or any(not item.get('path') or re.fullmatch('[0-9a-f]{64}',str(item.get('sha256',''))) is None
                                       for item in artifacts.values()):
        raise ValueError('Matched decoder control is missing source, transport, sink or decoder artifacts')
    bindings={'source48':'source_sha256','transport441':'transport_sha256',
              'reference_stimulus':'reference_stimulus_sha256','actual_stimulus':'actual_stimulus_sha256'}
    if any(artifacts[name]['sha256']!=proof.get(key) for name,key in bindings.items()):
        raise ValueError('Matched decoder inputs differ from their independent model receipts')
    padded=(math.ceil(proof['control_frames']/44100)+1)*44100
    for side,stats_name in (('reference','reference_stats'),('actual','actual_stats')):
        stats=turn.get('reference' if side=='reference' else 'observed',{})
        if (stats.get('input_rate')!=44100 or stats.get('input_channels')!=1 or
                stats.get('input_frames')!=padded or stats.get('unpadded_frames')!=proof['control_frames'] or
                stats.get('input_sha256')!=artifacts[side+'_stimulus']['sha256'] or
                type(stats.get('decoded_frames')) is not int or stats['decoded_frames']<0 or
                stats.get('decoded_sha256')!=artifacts[side+'_decoded']['sha256'] or
                not _bounded_number(stats.get('decoded_seconds'),0,3600) or
                abs(stats['decoded_seconds']-stats['decoded_frames']/44100)>1e-12):
            raise ValueError('The two decoders did not receive the same declared rate, phase, duration and padding')
    if verify_files:
        root=Path(case_root).resolve()
        if sha(transport['library_path'])!=transport['library_sha256']:
            raise ValueError('Loaded transport library changed after the independent model ran')
        for name,item in artifacts.items():
            path=Path(item['path']).resolve()
            if not path.is_relative_to(root) or sha(path)!=item['sha256']:
                raise ValueError('Retained matched decoder artifact changed or escaped its case: '+name)
        for name,frames in (('source48',proof['source_frames']),('transport441',proof['transport_frames']),
                            ('reference_stimulus',padded),('actual_stimulus',padded),
                            ('reference_decoded',turn['reference']['decoded_frames']),
                            ('actual_decoded',turn['observed']['decoded_frames'])):
            if Path(artifacts[name]['path']).stat().st_size!=frames*2:
                raise ValueError('Retained matched decoder PCM is partial or misaligned: '+name)
        if (_load(artifacts['reference_stats']['path'])!=turn['reference'] or
                _load(artifacts['actual_stats']['path'])!=turn['observed']):
            raise ValueError('Matched decoder counters differ from their retained enriched stats')
        trace=root/'result'/turn['trace_file']
        rows=[json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
        from usb_test_support import output_alignment_budget
        if output_alignment_budget(rows,turn)!=alignment_budget:
            raise ValueError('Waveform alignment budget differs from the actual negotiated outputs and typed backend receipts')
        from usb_transport_oracle import transport_chunks_for_turn
        start,end,chunks=transport_chunks_for_turn(rows,turn)
        if (start!=turn['source_tap_start'] or end!=turn['source_tap_end'] or chunks!=calls):
            raise ValueError('Matched transport summary differs from exact controller SRC input accounting')
        with trace.with_name(trace.stem+'-modem.raw').open('rb') as source:
            source.seek(start*2);reference=source.read((end-start)*2)
        if len(reference)!=(end-start)*2 or hashlib.sha256(reference).hexdigest()!=proof['source_sha256']:
            raise ValueError('Matched decoder source differs from the complete retained controller modem interval')
    return proof


def validate_sink_turns_run(run,case,verify_files=True,expected_transport_sha256=None):
    """Require complete waveform and decoder evidence for every positive turn.

    A case-level PASS or a previous turn's successful synthesis cannot replace
    the indexed RF turn, source-delivery budget and complete actual-sink proof.
    """
    from test_usb_audio_integration import SPEECH_CASES,WAVEFORM_CASES
    if case not in WAVEFORM_CASES:return
    close=validate_capture_close_run(run)
    completed=[turn for turn in close['turns'] if turn['rf_completed']]
    turns=run.get('actual_sink_turns')
    harness=run.get('harness',{})
    expected=harness.get('total_turns',harness.get('turns'))
    if (type(expected) is not int or expected<=0 or not isinstance(turns,list) or len(turns)!=expected or
            len(completed)!=expected or harness.get('total_eoo',harness.get('eoo'))!=expected):
        raise ValueError('Required per-turn actual sink evidence does not cover every completed RF turn: '+case)
    artifact_paths=set();source_ends={};rf_seen=set()
    for number,(turn,interval) in enumerate(zip(turns,completed),1):
        if (turn.get('status')!='PASS' or turn.get('turn')!=number or turn.get('trace_file')!=interval['trace_file'] or
                turn.get('microphone_frame_budget')!=interval['microphone_frame_budget'] or
                turn.get('reference_boundary_basis') not in ('exact_modem_tap_offsets','exact_render_input_accounting') or
                type(turn.get('start_ms')) is not int or type(turn.get('end_ms')) is not int or
                not 0<=turn['start_ms']<=turn['end_ms']):
            raise ValueError('Actual sink turn is duplicated, stale or differs from its finite capture interval: '+case)
        alignment_budget=validate_alignment_budget(turn);maximum_lag_ms=alignment_budget['maximum_lag_ms']
        waveform=turn.get('content_match',{})
        if (waveform.get('status')!='PASS' or waveform.get('method')!='full_modem_waveform_and_100ms_windows' or
                waveform.get('complete_reference_covered') is not True or waveform.get('minimum_correlation')!=.95 or
                waveform.get('maximum_lag_ms')!=maximum_lag_ms or waveform.get('resampler_edge_allowance_ms')!=5 or
                waveform.get('allowed_window_normalized_error')!=.35 or waveform.get('expected_gain')!=.25 or
                not _bounded_number(waveform.get('correlation'),.95,1.000001) or
                not _bounded_number(waveform.get('lag_ms'),0,maximum_lag_ms) or
                not _bounded_number(waveform.get('fitted_gain'),.25*.96,.25*1.04) or
                not _bounded_number(waveform.get('maximum_window_normalized_error'),0,.35) or
                type(waveform.get('reference_frames_compared_at_8k')) is not int or
                waveform['reference_frames_compared_at_8k']<800):
            raise ValueError('Actual sink lacks complete fixed-threshold waveform evidence: '+case)
        validate_decoder_control(turn,verify_files=verify_files,case_root=Path(run['case_record_path']).parent if verify_files else None,
                                 expected_transport_sha256=expected_transport_sha256)
        previous=source_ends.get(turn['trace_file'],0)
        if turn['source_tap_start']<previous:
            raise ValueError('An earlier turn source interval was reused as later actual sink evidence: '+case)
        source_ends[turn['trace_file']]=turn['source_tap_end']
        for item in turn['artifacts'].values():
            path=str(Path(item['path']).resolve())
            if path in artifact_paths:
                raise ValueError('An earlier decoder or PCM artifact was reused by another actual sink turn: '+case)
            artifact_paths.add(path)
        validate_rf_window(turn,interval,run,verify_files=verify_files)
        window=turn['rf_window']
        if window['status']=='PASS':
            rf_key=(window['rf_key_sequence'],window['rf_on_after_ns'],window['rf_off_before_ns'])
            if rf_key in rf_seen:raise ValueError('An earlier physical RF interval was reused for another completed voice turn')
            rf_seen.add(rf_key)
        reference,observed=turn.get('reference',{}),turn.get('observed',{})
        for stats in (reference,observed):
            if (type(stats.get('failures')) is not int or stats['failures']!=0 or
                    any(type(stats.get(key)) is not int or stats[key]<0 for key in ('synchronized_blocks','synthesis_blocks')) or
                    not _bounded_number(stats.get('decoded_seconds'),0,3600)):
                raise ValueError('Per-turn decoder evidence is missing or failed: '+case)
        speech=case in SPEECH_CASES or turn.get('speech_status')=='DECODED_REFERENCE_MATCH'
        if speech:
            if (turn.get('speech_status')!='DECODED_REFERENCE_MATCH' or
                    turn.get('allowed_rounding_and_acquisition_seconds')!=.14 or
                    any(stats['synchronized_blocks']<=0 or stats['synthesis_blocks']<=0 or stats['decoded_seconds']<=0
                        for stats in (reference,observed)) or
                    observed['synthesis_blocks']+1<reference['synthesis_blocks'] or
                    observed['decoded_seconds']+.14<reference['decoded_seconds']):
                raise ValueError('Per-turn speech synthesis is missing or truncated at the unchanged decoder bound: '+case)
        elif case!='short-turn200' or turn.get('speech_status')!='UNASSESSED_INSUFFICIENT_DURATION':
            raise ValueError('A positive voice turn cannot omit its required speech comparison: '+case)
    return turns


def validate_capture_close_run(run,verify_files=False):
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
    stopped_after_capture=(harness.get('fault')==1 and harness.get('first_fault_origin')=='local-stop' and
                           type(run.get('usb_output_contract_version')) is int and run['usb_output_contract_version']==1)
    if len(turns)>expected_rf and not (type(harness.get('fault')) is int and (harness['fault']>1 or stopped_after_capture)):
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
        if turn['source_usec']+turn['transport_usec']>750000:
            raise ValueError('Capture-close source timing exceeded the existing finite 750ms horizon')
        if ('submitted_first_index' in turn or 'submitted_end_index' in turn) and (
                turn.get('submitted_first_index') != start or turn.get('submitted_end_index') != cutoff):
            raise ValueError('Capture submission omitted or extended its declared finite interval')
        fence=turn.get('release_fence_index')
        if (type(fence) is not int or not start<=fence<=cutoff or type(turn.get('tail_frames')) is not int or
                turn['tail_frames']!=cutoff-fence):
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
        if (budget.get('schema')!='x6200.usb.capture-budget.v2' or
                budget.get('comparison_basis')!='minimum_on_total_maximum_on_pre_fence' or
                budget.get('status') != 'PASS' or type(active) is not int or active < 0 or
                type(fragment) is not int or fragment <= 0 or budget.get('delivery_tolerance_percent') != 1 or
                budget.get('boundary_frames') != 2*fragment or budget.get('accepted_frames') != cutoff-start or
                type(budget.get('pipeline_tail_frames')) is not int or budget['pipeline_tail_frames']!=cutoff-fence or
                type(budget.get('pre_fence_frames')) is not int or budget['pre_fence_frames']!=fence-start or
                budget.get('evaluated') is not (active >= 750)):
            raise ValueError('Capture-close changed or omitted the versioned total/prefix source delivery budget')
        expected = active*48
        lower, upper = max(0, expected*99//100 - 2*fragment), (expected*101 + 99)//100 + 2*fragment
        if (budget.get('minimum_frames') != lower or budget.get('maximum_frames') != upper or
                budget.get('expected_frames')!=expected or
                (active >= 750 and (cutoff-start<lower or fence-start>upper))):
            raise ValueError('Capture-close total delivery or pre-fence delivery did not meet the unchanged 1% plus two-fragment budget')
        if not turn['rf_completed'] and harness.get('fault')==1:
            witness=turn.get('completion_interruption',{})
            output=run.get('output_contract') or {}
            session=next((item for item in output.get('sessions',[]) if item.get('trace_file')==turn['trace_file']),{})
            if (not stopped_after_capture or witness.get('schema')!='x6200.usb.capture-interruption.v1' or
                    witness.get('status')!='CONFIRMED' or witness.get('fault')!=1 or witness.get('origin')!='local-stop' or
                    witness.get('state')!=4 or type(witness.get('ms')) is not int or witness['ms']<turn['closed_ms'] or
                    witness.get('closed_ms')!=turn['closed_ms'] or witness.get('trace_file')!=turn['trace_file'] or
                    witness.get('trace_sha256')!=session.get('trace_sha256') or
                    re.fullmatch('[0-9a-f]{64}',str(witness.get('trace_sha256',''))) is None):
                raise ValueError('A complete capture interrupted by Stop lacks its retained DRAIN chronology')
    if verify_files:_validate_capture_close_raw(run,proof)
    return proof



def _validate_capture_close_raw(run,proof):
    """Bind the declared tail and continuity to the unchanged C receipts."""
    root=Path(run['case_record_path']).parent/'result'
    grouped={}
    for turn in proof['turns']:grouped.setdefault(turn['trace_file'],[]).append(turn)
    for trace_file,turns in grouped.items():
        path=root/trace_file;rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        closes=[row for row in rows if row.get('kind')=='capture-close' and row.get('complete') is True]
        descriptor=next((row for row in rows if row.get('kind')=='stream' and row.get('stream')=='mic'),{})
        if (len(closes)!=len(turns) or descriptor.get('rate')!=48000 or descriptor.get('channels')!=1 or
                type(descriptor.get('fragsize')) is not int or descriptor['fragsize']<=0 or descriptor['fragsize']%2):
            raise ValueError('Raw finite capture intervals or negotiated microphone descriptor are missing')
        retained=path.with_name(path.stem+'-mic.raw').stat().st_size
        if retained%2:raise ValueError('The raw accepted microphone tap is misaligned')
        for turn,raw in zip(turns,closes):
            keys=('turn','start_index','cutoff_index','consumed_index','mic_tap_start','mic_tap_end','capture_started_ms',
                  'release_fence_index','tail_frames','close_started_ms','timing_issued_ms','frozen_ms','reached_ms',
                  'closed_ms','write_index_bytes','source_usec','transport_usec','sink_usec','frontier_extra_frames','cutoff_split_frames')
            if (any(raw.get(key)!=turn.get(key) for key in keys) or raw.get('index_gap') is not False or
                    raw.get('close_timeout_ms')!=750 or raw.get('scope')!='pulse-capture-pipeline-frontier' or
                    raw.get('submitted_first_index')!=turn['start_index'] or raw.get('submitted_end_index')!=turn['cutoff_index'] or
                    not 0<=turn['mic_tap_start']<=turn['mic_tap_end']<=retained//2 or
                    turn['frozen_ms']-turn['timing_issued_ms']>=750 or
                    turn['microphone_frame_budget']['fragment_frames']!=descriptor['fragsize']//2):
                raise ValueError('Capture tail, prefix or continuity differs from the exact raw finite source receipt')
            if not turn['rf_completed'] and run['harness'].get('fault')==1:
                witness=turn['completion_interruption']
                faults=[row for row in rows if row.get('kind')=='event' and row.get('event')=='first-fault']
                if (not faults or witness['trace_sha256']!=sha(path) or faults[0].get('status')!=1 or
                        faults[0].get('operation')!='local-stop' or faults[0].get('state')!=4 or
                        faults[0].get('ms')!=witness['ms'] or faults[0]['ms']<turn['closed_ms']):
                    raise ValueError('Capture Stop interruption summary differs from its first raw DRAIN fault')


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
          'callback_ended_ms','blocked_peeks','cutoff_index','sentinel_frames','release_posted_ms','pa_lock_attempt_ms',
          'pa_lock_waiting','coordinator_wait_required')
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
            exercise['pa_lock_waiting'] not in (0,1) or exercise['coordinator_wait_required']!=0 or
            not exercise['release_observed_ms']<=interval['close_started_ms']<=interval['timing_issued_ms']):
        raise ValueError('Capture boundary was not exercised at the declared retained-release epoch')
    if mode=='held150':
        if not (exercise['hold_started_ms']+50<=exercise['release_observed_ms']<exercise['hold_ended_ms'] and
                interval['timing_issued_ms']<exercise['hold_ended_ms'] and interval['frozen_ms']<exercise['hold_ended_ms'] and
                exercise['blocked_peeks']>0):
            raise ValueError('Held capture delivery did not overlap release and asynchronous frontier freeze')
    elif not (exercise['hold_ended_ms']<=exercise['callback_started_ms']<=exercise['barrier_started_ms']<=
              exercise['release_posted_ms']<=exercise['callback_ended_ms'] and
              exercise['barrier_started_ms']<=exercise['release_observed_ms'] and interval.get('cutoff_split_frames',0)>0):
        raise ValueError('Capture callback did not post retained release without waiting and split its finite cutoff')
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



def validate_capture_pipeline_tail_run(run,verify_files=True):
    """Exercise finite captured tail without enlarging either delivery bound."""
    close=validate_capture_close_run(run,verify_files=verify_files)
    proof=run.get('capture_pipeline_tail',{});harness=run.get('harness',{})
    if (proof.get('schema')!='x6200.usb.pipeline-tail.v1' or proof.get('status')!='PASS' or
            proof.get('case')!='capture-close-tail120' or proof.get('mode')!='tail120' or proof.get('phase')!='drain' or
            proof.get('model_source_usec')!=120000 or proof.get('physical_latency')!='UNMEASURED' or
            proof.get('original_upper_rejected') is not True or close['completed_rf_turns']!=1 or
            close['completed_intervals']!=1 or close['abandoned_intervals']!=0 or
            harness.get('total_eoo',harness.get('eoo'))!=1 or harness.get('rf_clear_confirmed') is not True or
            harness.get('rf_valid') is not True or harness.get('rf_tx') is not False or harness.get('physical_tx') is not False):
        raise ValueError('Controlled capture tail did not complete its acknowledged source-bound RF turn and fresh RX')
    interval=close['turns'][0];budget=interval['microphone_frame_budget']
    fields=('requested_ms','real_ack_ms','real_source_usec','write_index_bytes','read_index_bytes','transport_usec')
    if (any(type(proof.get(key)) is not int or proof[key]<0 for key in fields) or
            not interval['timing_issued_ms']<=proof['requested_ms']<=proof['real_ack_ms']<=interval['frozen_ms'] or
            proof['write_index_bytes']!=interval['write_index_bytes'] or proof['read_index_bytes']%2 or
            proof['read_index_bytes']//2<interval['release_fence_index'] or
            interval['source_usec']!=120000 or proof['transport_usec']!=interval['transport_usec'] or
            proof.get('total_accepted_frames')!=budget['accepted_frames'] or
            any(proof.get(key)!=budget[key] for key in ('pre_fence_frames','pipeline_tail_frames','minimum_frames','maximum_frames')) or
            budget['evaluated'] is not True or not budget['accepted_frames']>budget['maximum_frames'] or
            budget['pre_fence_frames']>budget['maximum_frames'] or budget['accepted_frames']<budget['minimum_frames']):
        raise ValueError('Controlled finite tail did not expose the old total-upper error with unchanged prefix and minimum bounds')
    boundary=proof.get('source_boundary',{})
    if (boundary.get('status')!='PASS' or boundary.get('turn')!=interval['turn'] or
            boundary.get('accepted_frames')!=budget['accepted_frames'] or boundary.get('compared_frames')!=budget['accepted_frames'] or
            re.fullmatch('[0-9a-f]{64}',str(boundary.get('expected_source_sha256',''))) is None or
            boundary.get('expected_source_sha256')!=boundary.get('accepted_sha256') or
            boundary.get('post_cutoff_excluded') is not True or boundary.get('post_cutoff_sentinel_excluded') is not True or
            boundary.get('sentinel_value')!=30001 or type(boundary.get('post_cutoff_frames_observed')) is not int or
            type(boundary.get('post_cutoff_sentinel_frames')) is not int or
            not 0<boundary['post_cutoff_sentinel_frames']<=boundary['post_cutoff_frames_observed'] or
            any(re.fullmatch('[0-9a-f]{64}',str(proof.get(key,''))) is None
                for key in ('observer_pcm_sha256','observer_index_sha256'))):
        raise ValueError('Controlled capture tail omitted exact indexed PCM or post-cutoff sentinel exclusion')
    if verify_files:
        from usb_test_support import validate_capture_pipeline_tail
        root=Path(run['case_record_path']).parent/'result'
        pcm,index=root/'mic-observer.pcm',root/'mic-observer.jsonl'
        if sha(pcm)!=proof['observer_pcm_sha256'] or sha(index)!=proof['observer_index_sha256']:
            raise ValueError('Controlled tail observer evidence changed')
        rows=[json.loads(line) for line in (root/interval['trace_file']).read_text().splitlines() if line.strip()]
        raw=next(row for row in rows if row.get('kind')=='capture-close' and row.get('complete') is True)
        microphone=(root/(Path(interval['trace_file']).stem+'-mic.raw')).read_bytes()
        source_rows=[json.loads(line) for line in index.read_text().splitlines() if line.strip()]
        actual=validate_capture_pipeline_tail(raw,interval,microphone,source_rows,pcm.read_bytes(),
                                            (root.parent/'test.log').read_text())
        if any(proof.get(key)!=value for key,value in actual.items()):
            raise ValueError('Controlled tail summary differs from actual timing, source and sentinel witnesses')
    validate_sink_turns_run(run,'capture-close-tail120',verify_files=verify_files)
    return proof



def validate_backend_failure_run(run,case,verify_files=True):
    specs={'backend-timing-missing':('missing',14,'modem-backend-timing'),
           'backend-timing-invalid':('invalid',9,'modem-backend-timing-invalid'),
           'backend-stop-before-timing':('stop-before-timing',1,'local-stop'),
           'backend-stop-during-wait':('stop-during-wait',1,'local-stop')}
    mode,fault,origin=specs[case];proof=run.get('backend_failure',{});harness=run.get('harness',{})
    validate_output_contract_run(run,verify_files=verify_files)
    if (not isinstance(proof,dict) or proof.get('schema')!='x6200.usb.backend-failure.v1' or proof.get('status')!='PASS' or
            proof.get('mode')!=mode or proof.get('fault')!=fault or proof.get('origin')!=origin or proof.get('state')!=4 or
            proof.get('requested_phase')!='drain' or proof.get('eoo')!=1 or proof.get('completed_rf_turns')!=0 or
            proof.get('keys')!=1 or proof.get('physical_tx') is not False or proof.get('rf_clear_confirmed') is not True or
            proof.get('interrupted_intervals')!=1 or harness.get('fault')!=fault or harness.get('first_fault_origin')!=origin or
            harness.get('total_turns',harness.get('turns'))!=0 or harness.get('total_eoo',harness.get('eoo'))!=1 or
            harness.get('keys')!=1 or harness.get('physical_tx') is not False or harness.get('rf_clear_confirmed') is not True or
            any(type(proof.get(key)) is not int or proof[key]<0 for key in ('protocol_ack_ms','requested_ms','real_ack_ms','first_fault_ms')) or
            not 0<proof['protocol_ack_ms']<=proof['requested_ms']<=proof['real_ack_ms']<=proof['first_fault_ms'] or
            re.fullmatch('[0-9a-f]{64}',str(proof.get('raw_log_sha256',''))) is None or
            sum(item['interrupted_intervals'] for item in run['output_contract']['sessions'])!=1):
        raise ValueError('Backend interruption did not retain its actual post-EOO DRAIN request, original fault and fresh RF clear')
    if mode=='missing' and harness.get('first_fault_operation_ms',0)<1000:
        raise ValueError('Missing backend timing did not exercise the unchanged one-second operation timeout')
    if mode=='stop-during-wait':
        fields=('request_before_ns','request_after_ns','wait_started_ms','wait_until_us')
        if (any(type(proof.get(key)) is not int or proof[key]<0 for key in fields) or
                not proof['wait_started_ms']*1000000<=proof['request_before_ns']<=proof['request_after_ns']<proof['wait_until_us']*1000 or
                proof['request_after_ns']-proof['request_before_ns']>=100000000 or
                not any(0<item.get('off_before_ns',0)<proof['wait_until_us']*1000 for item in harness.get('rf_intervals',[]))):
            raise ValueError('Local Stop did not clear RF before the unfinished backend wait expired')
    if verify_files:
        from usb_test_support import validate_backend_failure
        root=Path(run['case_record_path']).parent
        rows=[]
        for session in run['output_contract']['sessions']:
            rows.extend(json.loads(line) for line in (root/'result'/session['trace_file']).read_text().splitlines() if line.strip())
        text=(root/'test.log').read_text()
        if hashlib.sha256(text.encode()).hexdigest()!=proof['raw_log_sha256'] or validate_backend_failure(rows,harness,text,mode)!=proof:
            raise ValueError('Backend interruption summary differs from its exact timing/Stop/fault witnesses')
        if mode=='missing':
            receipt=next(row for row in rows if row.get('kind')=='backend-tail')
            if proof['first_fault_ms']-receipt.get('timing_issued_ms',proof['first_fault_ms'])<1000:
                raise ValueError('Backend timing timeout is not bound to its actual issue epoch')
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


def _known_host_snapshot(snapshot,minimum):
    memory,cpu=snapshot.get('memory',{}),snapshot.get('cpu',{})
    current,maximum,high=(memory.get(key) for key in ('current_bytes','max_bytes','high_bytes'))
    if (snapshot.get('status')!='KNOWN_BOUNDED' or not snapshot.get('cgroup_path') or snapshot.get('errors') or
            type(current) is not int or current<0 or type(maximum) is not int or maximum<=0 or
            (high is not None and (type(high) is not int or high<=0))):
        raise ValueError('Qualification requires a known bounded cgroup resource snapshot')
    effective=min(maximum,high) if high is not None else maximum
    headroom=max(0,effective-current)
    if (memory.get('effective_limit_bytes')!=effective or memory.get('headroom_bytes')!=headroom or
            snapshot.get('headroom_bytes')!=headroom or headroom<minimum):
        raise ValueError('Observed host headroom does not meet the declared resource contract')
    for component,names in ((memory.get('events',{}),('high','max','oom','oom_kill')),
                            (memory.get('stat',{}),('anon','file','shmem','pgscan_direct','pgsteal_direct')),
                            (cpu.get('stat',{}),('usage_usec','nr_throttled','throttled_usec'))):
        if any(type(component.get(key)) is not int or component[key]<0 for key in names):
            raise ValueError('Qualification resource counters are missing or invalid')
    if (type(cpu.get('period_us')) is not int or cpu['period_us']<=0 or
            (cpu.get('quota_us') is not None and (type(cpu['quota_us']) is not int or cpu['quota_us']<=0))):
        raise ValueError('Qualification CPU quota observation is invalid')
    return snapshot


def validate_host_resources(matrix, verify_files=True, require_end=True):
    """Resource admission never changes an actual controller failure verdict."""
    host=matrix.get('host_resources',{});config=matrix.get('plan',{}).get('host_resource_configuration',{})
    identity=matrix.get('identity',{})
    if (host.get('schema')!='x6200.usb.host-resource-contract.v1' or host.get('configuration')!=config or
            identity.get('host_resource_configuration')!=config or config.get('profile')!='qualification' or
            type(config.get('min_headroom_bytes')) is not int or config['min_headroom_bytes']<MIN_START_HEADROOM_BYTES or
            type(config.get('min_end_headroom_bytes')) is not int or config['min_end_headroom_bytes']<MIN_END_HEADROOM_BYTES or
            config.get('method')!='explicit_readonly_regular_file_DONTNEED' or
            config.get('pressure_counter_policy')!=list(PRESSURE_COUNTERS) or
            any(host.get(key)!=identity.get(key) for key in ('source_commit','source_tree_sha256','build_id','test_binary_sha256'))):
        raise ValueError('Source-bound host resource configuration or fixed qualification thresholds are missing')
    pre=host.get('preflight',{})
    if (pre.get('schema')!='x6200.usb.host-resources.v1' or pre.get('phase')!='preflight' or
            pre.get('profile')!='qualification' or pre.get('status')!='PASS' or pre.get('assessment')!='PASS' or
            pre.get('promotion_eligible') is not True or pre.get('configuration')!=config or pre.get('reasons') or
            pre.get('min_headroom_bytes')!=config['min_headroom_bytes'] or pre.get('min_end_headroom_bytes')!=config['min_end_headroom_bytes']):
        raise ValueError('Host resource preflight did not admit the qualification campaign')
    _known_host_snapshot(pre.get('before',{}),0)
    baseline=_known_host_snapshot(pre.get('after',{}),config['min_headroom_bytes'])
    reclaim=pre.get('reclaim',{})
    if (reclaim.get('status') in ('BLOCKED','ERROR') or reclaim.get('timed_out') is not False or
            reclaim.get('contents_modified') is not False or reclaim.get('worker_still_running',False) is not False or
            reclaim.get('rejected_roots') or not set(reclaim.get('roots',[]))<=set(config.get('reclaim_roots',[])) or
            pre.get('headroom_bytes')!=baseline['headroom_bytes']):
        raise ValueError('Host cache preparation did not remain within its explicit read-only bounds')
    if verify_files:
        if sha(host['preflight_report_path'])!=host.get('preflight_report_sha256') or _load(host['preflight_report_path'])!=pre:
            raise ValueError('Host resource preflight receipt changed')
    if not require_end:return host
    end=host.get('end',{})
    if (host.get('status')!='PASS' or end.get('schema')!='x6200.usb.host-resources.v1' or end.get('phase')!='end' or
            end.get('profile')!='qualification' or end.get('status')!='PASS' or end.get('assessment')!='PASS' or
            end.get('promotion_eligible') is not True or end.get('configuration')!=config or end.get('reasons') or
            end.get('before')!=baseline or end.get('min_headroom_bytes')!=config['min_end_headroom_bytes'] or
            end.get('new_memory_pressure') or end.get('cpu_throttling_is_diagnostic_only') is not True):
        raise ValueError('Host resource end observation is missing or records pressure during qualification')
    after=_known_host_snapshot(end.get('after',{}),config['min_end_headroom_bytes'])
    if (after.get('cgroup_path')!=baseline.get('cgroup_path') or end.get('headroom_bytes')!=after['headroom_bytes'] or
            any(baseline[component].get(key)!=after[component].get(key) for component,keys in
                (('memory',('max_bytes','high_bytes','effective_limit_bytes')),('cpu',('quota_us','period_us'))) for key in keys)):
        raise ValueError('Host resource limits changed during qualification')
    for key in PRESSURE_COUNTERS:
        component,bank,counter=key.split('.')
        change=after[component][bank][counter]-baseline[component][bank][counter]
        if change!=0 or end.get('counter_deltas',{}).get(key)!=change:
            raise ValueError('New or reset host memory pressure counters invalidate qualification')
    if verify_files:
        if sha(host['end_report_path'])!=host.get('end_report_sha256') or _load(host['end_report_path'])!=end:
            raise ValueError('Host resource end receipt changed')
    return host



def validate_release_lock_protocol(proof,expected,verify_files=True):
    """The test fixture must release without waiting on its own Pulse lock."""
    keys=('coordinator_blocked','release_posted_without_wait','observed_after_unlock')
    execution=proof.get('execution',{});witness=proof.get('witness',{})
    if (proof.get('schema')!='x6200.usb.release-lock-protocol.v1' or proof.get('status')!='PASS' or
            execution.get('exit_code')!=0 or execution.get('timed_out') is not False or
            any(proof.get(key)!=expected[key] for key in
                ('source_commit','build_id','controller_binary_sha256','fault_injector_sha256','radio_accessed')) or
            proof.get('checks')!={key:True for key in keys} or witness.get('status')!='PASS' or
            any(witness.get(key)!=expected[key] for key in ('source_commit','build_id','radio_accessed')) or
            any(witness.get(key) is not True for key in keys)):
        raise ValueError('Release lock protocol did not prove the blocked coordinator interleaving without waiting')
    log=proof.get('evidence_files',{}).get('log',{})
    if not log.get('path') or re.fullmatch('[0-9a-f]{64}',str(log.get('sha256',''))) is None:
        raise ValueError('Release lock protocol is missing its retained witness log')
    if verify_files:
        if sha(log['path'])!=log['sha256']:
            raise ValueError('Release lock protocol witness log changed')
        rows=[json.loads(line[len('RELEASE_LOCK_PROTOCOL '):]) for line in Path(log['path']).read_text().splitlines()
              if line.startswith('RELEASE_LOCK_PROTOCOL ')]
        if rows!=[witness]:
            raise ValueError('Release lock protocol summary differs from its actual child witness')
    return proof


def validate_infrastructure_prerequisites(matrix, verify_files=True):
    """Both startup and active-qualification runtime failures must stay failed."""
    if matrix.get('plan',{}).get('infrastructure_prerequisites')!=list(INFRASTRUCTURE_PREREQUISITES):
        raise ValueError('USB campaign omits its mandatory infrastructure failure-preservation prerequisite')
    prerequisites=matrix.get('infrastructure_prerequisites',{})
    if set(prerequisites)!=set(INFRASTRUCTURE_PREREQUISITES):
        raise ValueError('USB infrastructure prerequisite evidence is missing')
    identity=matrix.get('identity',{})
    expected={'source_commit':identity.get('source_commit'),'build_id':identity.get('build_id'),
              'controller_binary_sha256':identity.get('test_binary_sha256'),'runner_sha256':identity.get('runner_sha256'),
              'fault_injector_sha256':matrix.get('fault_injector_sha256'),
              'fixture_sha256':{'speech':identity.get('speech_sha256'),'modem':identity.get('modem_sha256')},'radio_accessed':False}
    specs={'initial_buffer':('normal',13,'modem-buffer-budget',False),
           'qualification_telemetry':('rf-disabled',4,'radio-packet-stale',True)}
    for entry in prerequisites.values():
        proof=entry.get('record',{})
        if (entry.get('status')!='PASS' or entry.get('supervision',{}).get('exit_code')!=0 or
                entry.get('supervision',{}).get('timed_out') is not False or proof.get('status')!='PASS' or
                proof.get('schema')!='x6200.usb.infrastructure.v2' or proof.get('kind')!='unexpected-runtime-failures' or
                entry.get('record_sha256')!=digest(proof)):
            raise ValueError('USB infrastructure prerequisite is not a valid expected-negative proof')
        if any(proof.get(key)!=value for key,value in expected.items()):
            raise ValueError('USB infrastructure proof belongs to a different source, build, binary or fixture')
        if set(proof.get('variants',{}))!=set(specs):
            raise ValueError('USB infrastructure did not exercise both real failure paths')
        _failure_checks(proof)
        validate_release_lock_protocol(proof.get('release_lock_protocol',{}),expected,verify_files=verify_files)
        if verify_files and (sha(entry['report_path'])!=entry.get('report_sha256') or
                _load(entry['report_path'])!=proof or sha(entry['log_path'])!=entry.get('log_sha256')):
            raise ValueError('USB infrastructure report was removed or changed')
        for name,(case,code,origin,disabled) in specs.items():
            variant=proof['variants'][name]
            if (variant.get('status')!='PASS' or variant.get('schema')!='x6200.usb.infrastructure.v1' or
                    variant.get('kind')!='unexpected-runtime-failure' or variant.get('variant')!=name or
                    variant.get('driver_case')!=case or variant.get('rf_disabled') is not disabled or variant.get('keys')!=0 or
                    any(variant.get(key)!=value for key,value in expected.items())):
                raise ValueError('USB infrastructure failure variant identity or RF policy is invalid')
            _failure_checks(variant)
            if (type(variant.get('driver_exit_code')) is not int or variant['driver_exit_code']==0 or
                    variant.get('driver_status')!='FAIL' or type(variant.get('harness_exit_code')) is not int or
                    variant['harness_exit_code']<=0 or variant.get('harness_status')!='FAIL' or
                    variant.get('terminal_fault_code')!=code or variant.get('terminal_fault_origin')!=origin or
                    variant.get('collector_status')!='BLOCKED'):
                raise ValueError('Unexpected runtime failure was converted into a successful functional test')
            files=variant.get('evidence_files',{})
            required=set(INFRASTRUCTURE_FILES)|({'injection_witness'} if disabled else set())
            if not required<=set(files):raise ValueError('USB infrastructure proof did not retain its failed child evidence')
            if disabled:
                q=variant.get('qualification_failure',{})
                keys=('qualification_started_ms','freeze_started_ms','packet_ms','sequence','freeze_duration_ms','fault_ms','recovered_ms')
                if (any(type(q.get(key)) is not int or q[key]<0 for key in keys) or q['sequence']<1 or
                        q['freeze_duration_ms']!=350 or q['packet_ms']>q['freeze_started_ms'] or
                        q['qualification_started_ms']>q['fault_ms'] or q['fault_ms']<q['packet_ms']+250 or
                        q['recovered_ms']<q['freeze_started_ms']+350 or q.get('qualification_complete') is not False or q.get('keys')!=0):
                    raise ValueError('Active RF-disabled qualification failure was not exercised and recovered')
            if verify_files:
                for filename in required:
                    item=files[filename]
                    if sha(item['path'])!=item.get('sha256'):raise ValueError('USB infrastructure child evidence changed: '+filename)
                child=_load(files['case_result']['path']);failure=_load(files['harness_failure']['path'])
                completion=_load(files['completion']['path']);collector=_load(files['collector_report']['path'])
                faultfile=_load(files['first_fault']['path']);fault=faultfile.get('first_fault',{})
                if (child.get('status')!='FAIL' or child.get('controller_exit_code')!=variant['harness_exit_code'] or
                        failure.get('status')!='FAIL' or failure.get('exit_code')!=variant['harness_exit_code'] or
                        failure.get('primary_fault')!=code or failure.get('primary_origin')!=origin or failure.get('keys')!=0 or
                        failure.get('rf_disabled') is not disabled or
                        any(failure.get(key) is not True for key in ('cleanup_requested','stopped','closed')) or
                        completion.get('terminal') is not True or completion.get('fault')!=code or
                        completion.get('trace_complete') is not True or completion.get('rf_clear_confirmed') is not True or
                        completion.get('rf_disabled') is not disabled or collector.get('status')!='BLOCKED' or
                        collector.get('ready_for_rf_test') is not False or fault.get('code')!=code or fault.get('origin')!=origin):
                    raise ValueError('Retained infrastructure child artifacts contradict the failure-preservation proof')
                if disabled:
                    marker=_load(files['injection_witness']['path'])
                    rows=[json.loads(line) for line in Path(files['trace']['path']).read_text().splitlines() if line.strip()]
                    if (marker.get('kind')!='test-telemetry-freeze' or marker.get('qualification_running') is not True or
                            marker.get('keys')!=0 or marker.get('duration_ms')!=350 or marker.get('ms')!=q['freeze_started_ms'] or
                            marker.get('packet_ms')!=q['packet_ms'] or marker.get('sequence')!=q['sequence'] or
                            completion.get('qualification_complete') is not False or
                            not any(row.get('event')=='qualification-start' and row.get('ms')==q['qualification_started_ms'] for row in rows) or
                            not any(row.get('event')=='first-fault' and row.get('status')==4 and row.get('operation')==origin and
                                    row.get('ms')==q['fault_ms'] for row in rows) or
                            not any(row.get('kind')=='sample' and row.get('qualification_running') is True for row in rows)):
                        raise ValueError('Qualification failure witness does not match the actual active-stage trace')
    return prerequisites


def _failure_checks(proof):
    checks=proof.get('checks',{})
    if set(checks)!=set(INFRASTRUCTURE_CHECKS) or any(checks[key] is not True for key in INFRASTRUCTURE_CHECKS):
        raise ValueError('USB infrastructure failure was not preserved at every required stage')


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
