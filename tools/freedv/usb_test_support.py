"""Bounded host-test processes and independent USB audio evidence checks.

These helpers are test infrastructure; they never contact a radio.
"""
from pathlib import Path
from array import array
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time


def require(condition, message):
    """A qualification check must remain active under Python optimization."""
    if not condition:
        raise ValueError(message)


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_unix_socket_path(path):
    """Linux sockaddr_un has 108 bytes, including the terminating NUL."""
    require(len(os.fsencode(path)) <= 107, 'Private Pulse Unix socket path exceeds the 107-byte Linux limit')


class PulseTestRuntime:
    """Keep Unix sockets independent of arbitrarily deep evidence directories."""
    def __init__(self, evidence_directory):
        self.evidence_directory = Path(evidence_directory)
        self.temporary = tempfile.TemporaryDirectory(prefix='x6pa-', dir='/tmp')
        self.directory = Path(self.temporary.name)
        self.socket = self.directory/'native'
        self.cleanup_attempted = False
        try:
            require_unix_socket_path(self.socket)
            (self.directory/'config').mkdir(mode=0o700)
            (self.directory/'state').mkdir(mode=0o700)
        except BaseException:
            self.temporary.cleanup()
            raise

    def environment(self):
        return {'PULSE_SERVER':'unix:'+str(self.socket), 'PULSE_RUNTIME_PATH':str(self.directory),
                'PULSE_STATE_PATH':str(self.directory/'state'), 'XDG_CONFIG_HOME':str(self.directory/'config')}

    def identity(self):
        return {'scope':'Private host Pulse runtime; immutable evidence remains in the attempt directory',
                'directory':str(self.directory), 'socket':str(self.socket),
                'socket_path_bytes':len(os.fsencode(self.socket)), 'maximum_socket_path_bytes':107,
                'evidence_directory':str(self.evidence_directory),
                'cleanup_attempted':self.cleanup_attempted, 'directory_removed':not self.directory.exists()}

    def close(self):
        self.cleanup_attempted = True
        self.temporary.cleanup()


def pulse_startup_failure(log_text):
    """Mandatory module failures cannot recover through readiness polling."""
    markers = ('Failed to load module ', 'Failed to open module ', 'Failed to bind socket',
               'AF_UNIX path too long', 'Daemon startup failed')
    return next((line for line in log_text.splitlines() if any(marker in line for marker in markers)), None)


def _stop_group(process, grace):
    process.poll()  # Reap an already exited leader; descendants may remain.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.01)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=max(1.0, grace))


def run_bounded(command, log_path, timeout, env=None, cwd=None, terminate_grace=3.0):
    """Run one command in its own process group, retaining output on failure.

    A timeout is a result, never an implicit retry. On interruption, the whole
    command group is still terminated and reaped before the exception escapes.
    """
    started = time.monotonic()
    process = None
    timed_out = False
    with Path(log_path).open('w') as log:
        try:
            process = subprocess.Popen([str(x) for x in command], env=env, cwd=cwd,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            if process is not None:
                _stop_group(process, terminate_grace)
    return {'command': [str(x) for x in command], 'log_path': str(log_path),
            'exit_code': process.returncode, 'timed_out': timed_out,
            'duration_ms': round((time.monotonic() - started) * 1000)}


class ProcessSupervisor:
    """Own direct children within the enclosing test case's process group."""
    def __init__(self, root, env):
        self.root, self.env = Path(root), env
        self.children = []

    def start(self, name, command, stdout=None, env=None, cwd=None):
        log = (self.root / (name + '.log')).open('w')
        output = stdout if stdout is not None else log
        try:
            process = subprocess.Popen([str(x) for x in command], env=self.env if env is None else env,
                                       stdout=output, stderr=log, cwd=cwd)
        except BaseException:
            log.close()
            raise
        self.children.append({'name': name, 'process': process, 'log': log,
                              'command': [str(x) for x in command], 'expected_exit': False,
                              'cwd':str(cwd) if cwd is not None else None})
        return process

    def require_running(self, except_process=None):
        for child in self.children:
            process = child['process']
            if process is except_process:
                continue
            code = process.poll()
            if child['expected_exit']:
                if code is not None and child.get('expected_exit_code') is not None and code!=child['expected_exit_code']:
                    raise RuntimeError('Planned fixture '+child['name']+' exited with '+str(code)+' instead of '+str(child['expected_exit_code']))
                continue
            if code is not None:
                raise RuntimeError('Fixture process ' + child['name'] + ' exited unexpectedly with ' + str(code) +
                                   '; inspect ' + str(self.root / (child['name'] + '.log')))

    def expect_exit(self, process, exit_code=0):
        for child in self.children:
            if child['process'] is process:
                child['expected_exit']=True
                child['expected_exit_code']=exit_code
                return
        raise ValueError('Only a supervised fixture can have a planned exit')

    def stop(self, process, grace=2.0):
        for child in self.children:
            if child['process'] is process:
                child['expected_exit'] = True
                break
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        return process.returncode

    def close(self, grace=3.0):
        """Best-effort cleanup never hides the primary case failure."""
        errors = []
        deadline = time.monotonic() + grace
        for child in self.children:
            process = child['process']
            child['expected_exit'] = True
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        while time.monotonic() < deadline and any(c['process'].poll() is None for c in self.children):
            time.sleep(.01)
        for child in self.children:
            if child['process'].poll() is None:
                try:
                    child['process'].kill()
                except ProcessLookupError:
                    pass
        kill_deadline = time.monotonic() + 1
        for child in self.children:
            try:
                child['process'].wait(timeout=max(.01, kill_deadline-time.monotonic()))
            except Exception as error:
                errors.append(child['name'] + ': ' + str(error))
            try:
                child['log'].close()
            except Exception as error:
                errors.append(child['name'] + ' log: ' + str(error))
        return {'errors': errors, 'children': [
            {'name': c['name'], 'exit_code': c['process'].returncode, 'command': c['command'], 'cwd':c.get('cwd')}
            for c in self.children]}


def completed_turns(rows):
    """Locate each externally observable complete TX/drain interval."""
    turns, current = [], None
    for row in rows:
        if row.get('kind') != 'event' or row.get('event') != 'state':
            continue
        state = row.get('state')
        if state == 3:
            require(current is None, 'A new TX began before the previous turn completed')
            current = {'start_ms': row['ms']}
        elif state == 4 and current is not None:
            current['drain_ms'] = row['ms']
        elif state == 8 and current is not None:
            require('drain_ms' in current, 'Completed TX omitted drain')
            current['end_ms'] = row['ms']
            turns.append(current)
            current = None
    return turns


def validate_delayed_ack(rows, log_text, phase, action, state, requested_ms=650):
    """Prove the injected hold used the controller's measured wait interval.

    Issue time precedes the real Pulse API call. It cannot serve as the start
    of a delay that is intended to exercise the later controller wait.
    """
    pattern=(r'INJECT PA delayed-ack action=(\S+) phase=(\S+) issued_ms=(\d+) '
             r'wait_started_ms=(\d+) ack_ms=(\d+) requested_ms=(\d+) success=(-?\d+)')
    witnesses=[]
    for match in re.finditer(pattern,log_text):
        observed_action,observed_phase,issued,wait_started,acknowledged,requested,success=match.groups()
        if observed_action==action and observed_phase==phase:
            witnesses.append(dict(issued_ms=int(issued),wait_started_ms=int(wait_started),
                ack_ms=int(acknowledged),requested_ms=int(requested),success=int(success)))
    operation='modem-'+action
    starts=[row for row in rows if row.get('kind')=='event' and row.get('event')=='operation-start'
            and row.get('operation')==operation and row.get('state')==state]
    endings=[row for row in rows if row.get('kind')=='event' and row.get('event')=='operation-end'
             and row.get('operation')==operation and row.get('state')==state and row.get('status')==1]
    header=next((row for row in rows if row.get('kind')=='session'),{})
    timeout=header.get('operation_timeout_ms',1000)
    for witness in witnesses:
        issued,started,ack=witness['issued_ms'],witness['wait_started_ms'],witness['ack_ms']
        if (witness['requested_ms']!=requested_ms or witness['success']!=1 or
                ack-started<requested_ms):
            continue
        for end in endings:
            if not requested_ms<=end.get('duration_ms',0)<timeout:
                continue
            # The most recent start is the only valid partner for this end.
            prior=[begin for begin in starts if begin['ms']<=end['ms']]
            if not prior:
                continue
            begin=max(prior,key=lambda row:row['ms'])
            if issued<=begin['ms']<=started<=ack<=end['ms']:
                return {'status':'PASS','phase':phase,'operation':operation,'trace_state':state,
                        **witness,'hold_ms':ack-started,'trace_start_ms':begin['ms'],
                        'trace_end_ms':end['ms'],'duration_ms':end['duration_ms']}
    require(False,'No correlated '+str(requested_ms)+'ms delayed ACK witness inside the requested '+phase+' '+operation+' wait')


def validate_operation_phase(rows, phase, mode, log_text=''):
    states = {'initial-rx':0, 'tx':2, 'rx-return':9}
    events = [row for row in rows if row.get('kind') == 'event' and row.get('event') == 'operation-end'
              and row.get('operation') == 'modem-flush' and row.get('state') == states[phase]]
    header = next((row for row in rows if row.get('kind') == 'session'), {})
    timeout = header.get('operation_timeout_ms',1000)
    def matches(row):
        if mode == 'delay':
            return row.get('status') == 1 and 650 <= row.get('duration_ms',0) < timeout
        if mode == 'reject':
            return row.get('status') == -1
        return row.get('status') == -2 and row.get('duration_ms',0) >= timeout
    observed = next((row for row in events if matches(row)),None)
    require(observed is not None, 'Trace did not exercise '+mode+' in the requested '+phase+' preparation phase')
    if mode != 'delay':
        require(any(row.get('event') == 'first-fault' and row.get('state') == states[phase] and
                    row.get('status') == (15 if mode == 'reject' else 14) for row in rows),
                'The requested PA phase did not cause its expected first fault')
    result={'phase':phase,'mode':mode,'operation':'modem-flush','trace_state':states[phase],
            'observed_status':observed['status'],'duration_ms':observed['duration_ms']}
    if mode=='delay':
        result['delay_witness']=validate_delayed_ack(rows,log_text,phase,'flush',states[phase])
    return result


def validate_drain_loss_wait(rows, harness, log_text):
    witness=re.search(r'RF_DROP_DURING_TRIGGER_WAIT started_ms=(\d+) drop_ms=(\d+) ack_ms=(\d+)',log_text)
    require(witness is not None,'RF loss during the delayed trigger wait was not retained')
    started,dropped,acknowledged=map(int,witness.groups())
    require(started<dropped<acknowledged and acknowledged-started>=650,
            'RF loss did not occur inside the requested successful 650ms trigger ACK wait')
    delay=validate_delayed_ack(rows,log_text,'tx','trigger',4)
    require(started==delay['wait_started_ms'] and acknowledged==delay['ack_ms'] and
            delay['trace_start_ms']<=started<dropped<acknowledged<=delay['trace_end_ms'],
            'The trace does not place RF loss inside the successful DRAIN trigger operation wait')
    faults=[row for row in rows if row.get('event')=='first-fault' and row.get('status')==4
            and row.get('operation')=='tx-status-disappeared-during-drain' and row.get('state')==4]
    require(faults and min(row['ms'] for row in faults)>=acknowledged,
            'Fresh RF state was not rejected after the delayed trigger returned')
    require(harness['fault']==4 and harness['turns']==0 and not harness['physical_tx'],
            'A turn with RF loss during the trigger wait was counted as complete')
    return {'status':'PASS','operation':'modem-trigger','phase':'tx','state':4,
            'started_ms':started,'rf_drop_ms':dropped,'acknowledged_ms':acknowledged,
            'successful_ack_wait_ms':acknowledged-started,'completed_turns':0,'delay_witness':delay}


def render_capacity_audit(rows):
    audits=[row for row in rows if row.get('kind')=='render-queue']
    require(audits,'Render-capacity evidence is missing')
    total=final_frames=final_calls=maximum=0
    for row in audits:
        limit,bound=row['queue_limit_frames'],row['output_capacity_frames']
        before,generated,after=row['queue_before_frames'],row['generated_frames'],row['queue_after_frames']
        require(limit==22050 and bound==4608,'The declared render queue bounds changed')
        require(all(type(value) is int and value>=0 for value in (before,generated,after,row['reserved_from_frames'])),
                'Render capacity counters are invalid')
        require(after==before+generated and after<=limit and generated<=bound,'Rendered output exceeded its bounded queue')
        if row['stage'] in ('regular','src-final'):
            require(row['reserved_from_frames']+bound<=limit and before<=row['reserved_from_frames'],
                    'Conversion consumed input before reserving its worst-case output capacity')
            total+=generated
        else:
            require(row['stage'] in ('regular-wait','src-final-wait') and generated==0,
                    'Deferred render step unexpectedly generated output')
        if row['stage']=='src-final':
            final_calls+=1;final_frames+=generated
        maximum=max(maximum,after)
    return {'status':'PASS','count':len(audits),'generated_frames':total,'final_flush_calls':final_calls,
            'final_flush_frames':final_frames,'high_water_frames':maximum}


def validate_tail_backpressure(rows,harness,log_text):
    queue,hold=harness['render_queue'],harness['render_hold']
    require('INJECT render hold started' in log_text and 'INJECT render hold ended' in log_text and 'no-discard=1' in log_text,
            'Lossless output backpressure was not injected')
    require(hold['ended_ms']-hold['started_ms']>=150 and hold['hits']>0,'The output gate did not hold for 150ms')
    require(queue['limit_frames']==22050 and queue['output_bound_frames']==4608 and
            17442<queue['high_water_frames']<=22050 and queue['deferred_calls']>0,
            'EOO did not exercise bounded render-queue pressure')
    require(queue['final_flush_calls']>=2 and queue['final_flush_frames']>0 and
            queue['generated_frames']==queue['written_frames'], 'The final SRC tail was not fully emitted and written')
    audit=render_capacity_audit(rows)
    require(audit['generated_frames']==queue['generated_frames'] and
            audit['final_flush_frames']==queue['final_flush_frames'] and
            audit['final_flush_calls']==queue['final_flush_calls'], 'Render audit and final counters disagree')
    require(any(row.get('kind')=='render-queue' and row.get('stage') in ('regular-wait','src-final-wait') and
                hold['started_ms']<=row['ms']<=hold['ended_ms'] for row in rows),
            'The EOO producer did not reach backpressure while output submission was gated')
    return {'status':'PASS','render_queue':queue,'render_hold':hold,'render_audit':audit}


def validate_session_identity(rows, completion, expected_source=None, expected_build=None, expected_storage_error=False):
    headers = [row for row in rows if row.get('kind') == 'session']
    samples = [row for row in rows if row.get('kind') == 'sample']
    require(len(headers) == 1 and samples, 'Session metadata or samples are missing')
    header, final = headers[0], samples[-1]
    require(completion.get('kind') == 'completion' and completion.get('terminal') is True,
            'A closed session completion record is required')
    for name in ('session_id', 'source_commit', 'build_id'):
        require(header.get(name) and header[name] == completion.get(name), 'Session identity mismatch: '+name)
    if expected_source is not None:
        require(header['source_commit'] == expected_source, 'Controller source commit differs from the campaign')
    if expected_build is not None:
        require(header['build_id'] == expected_build, 'Controller build differs from the campaign')
    require(completion.get('trace_complete') is True and completion.get('trace_history_truncated') is False,
            'The controller trace is incomplete or truncated')
    require(completion.get('events_dropped') == 0, 'Controller trace dropped events')
    require((completion.get('storage_errno', 0) > 0) if expected_storage_error else completion.get('storage_errno') == 0,
            'Controller incident storage result differs from the case expectation')
    require(completion.get('last_sample_ms') == final.get('ms') and
            completion.get('state') == final.get('state') and completion.get('fault') == final.get('fault'),
            'Completion does not match the final controller sample')
    return {name:header[name] for name in ('session_id','source_commit','build_id')}


def microphone_frame_budget(active_ms, accepted_frames, fragment_frames, rate=48000, stream='microphone'):
    expected = active_ms * rate / 1000
    boundary = 2 * fragment_frames
    lower = max(0, math.floor(expected * .99) - boundary)
    upper = math.ceil(expected * 1.01) + boundary
    evaluated = active_ms >= 750
    if evaluated:
        require(lower <= accepted_frames <= upper,
                'Accepted '+stream+' frames differ from the observed wall time: '+str(accepted_frames)+
                ' frames; required '+str(lower)+'..'+str(upper))
    return {'status':'PASS','stream':stream,'active_ms':active_ms,'accepted_frames':accepted_frames,
            'expected_frames':expected,'minimum_frames':lower,'maximum_frames':upper,
            'fragment_frames':fragment_frames,'boundary_frames':boundary,
            'delivery_tolerance_percent':1,'evaluated':evaluated}


def capture_close_oracle(rows, microphone_pcm, expected_turns):
    """Check finite Pulse capture intervals, separately from delivery time.

    This frontier describes the Pulse capture pipeline. It does not establish
    the physical microphone or PTT latency. Monitor playback latency is
    deliberately absent from the capture-production frontier arithmetic.
    """
    require(len(microphone_pcm)%2==0,'The accepted microphone tap is misaligned')
    completed=completed_turns(rows)
    intervals=[];current=None
    for row in rows:
        if row.get('kind')!='event' or row.get('event')!='state':
            continue
        if row.get('state')==3:
            current={'start_ms':row['ms'],'rf_completed':False};intervals.append(current)
        elif current is not None and row.get('state')==4:
            current['drain_ms']=row['ms']
        elif current is not None and row.get('state') in (5,6,8):
            current.update(end_ms=row['ms'],rf_completed=row['state']==8);current=None
    closes=[row for row in rows if row.get('kind')=='capture-close' and row.get('complete') is True]
    require(len(closes)>=expected_turns==len(completed),'Capture closure did not cover every completed RF turn')
    descriptor=next((row for row in rows if row.get('kind')=='stream' and row.get('stream')=='mic'),{})
    require(descriptor.get('rate')==48000 and descriptor.get('channels')==1,
            'Capture closure requires the actual 48k mono microphone descriptor')
    results=[];covered=[]
    for number,close in enumerate(closes,1):
        require(close.get('turn')==number,'Capture closure turn order is incomplete')
        matches=[turn for turn in intervals if turn not in covered and 'drain_ms' in turn and
                 turn['start_ms']<=close['close_started_ms']<=turn['drain_ms']<=close['timing_issued_ms']]
        require(len(matches)==1,'Capture closure has no matching finite TX release interval')
        turn=matches[0];covered.append(turn)
        write=close['write_index_bytes']
        require(type(write) is int and write>=0 and write%2==0,'The raw capture write index is invalid')
        require(all(type(close[key]) is int and close[key]>=0 for key in ('source_usec','transport_usec','sink_usec')),
                'Capture timing inputs are invalid')
        extra=((close['source_usec']+close['transport_usec'])*48000+999999)//1000000
        cutoff=write//2+extra
        require(close['frontier_extra_frames']==extra and close['cutoff_index']==cutoff,
                'The capture cutoff does not match the raw production frontier')
        start=close['start_index']
        require(type(start) is int and 0<=start<=cutoff and close['consumed_index']==cutoff and
                close['submitted_first_index']==start and close['submitted_end_index']==cutoff,
                'The completed capture interval is missing, overlapping or extends beyond its frozen cutoff')
        tap_start,tap_end=close['mic_tap_start'],close['mic_tap_end']
        require(type(tap_start) is int and type(tap_end) is int and 0<=tap_start<=tap_end<=len(microphone_pcm)//2,
                'The accepted capture interval is not retained in the microphone tap')
        accepted=tap_end-tap_start
        require(accepted==cutoff-start,'The accepted PCM does not conserve the exact indexed capture interval')
        require(close['tail_frames']==max(0,cutoff-close['release_fence_index']) and
                close['index_gap'] is False and close['close_timeout_ms']==750,
                'Capture closure changed its indexed tail, continuity or deadline contract')
        times=[close[key] for key in ('close_started_ms','timing_issued_ms','frozen_ms','reached_ms','closed_ms')]
        require(all(type(value) is int for value in times) and times==sorted(times) and
                turn['start_ms']<=times[0]<=turn['drain_ms']<=times[1] and
                times[-1]<=turn['end_ms'] and times[-1]-times[0]<=750,
                'Capture closure did not finish within its bounded release phase')
        releases=[row for row in rows if row.get('kind')=='event' and row.get('event')=='capture-release-observed'
                  and row.get('state')==3 and times[0]<=row.get('ms',-1)<=turn['drain_ms']]
        require(releases and close['capture_started_ms']<=turn['start_ms'],
                'Capture closure lacks its coordinator release epoch before the I/O fence')
        accounting=next((row for row in rows if row.get('kind')=='source-accounting' and row.get('turn')==number),None)
        require(accounting and rows.index(close)<rows.index(accounting) and accounting['ms']>=times[-1] and
                accounting['accepted_mic_frames']==accepted and accounting['passed'] is True,
                'EOO source accounting ran before complete capture closure or disagrees with accepted PCM')
        active_ms=times[0]-close['capture_started_ms']
        require(accounting['active_ms']==active_ms,'The source frame budget changed its capture epoch')
        budget=microphone_frame_budget(active_ms,accepted,descriptor['fragsize']//2)
        results.append({'status':'PASS','turn':number,'start_index':start,'cutoff_index':cutoff,
            'consumed_index':close['consumed_index'],'accepted_frames':accepted,'mic_tap_start':tap_start,
            'mic_tap_end':tap_end,'close_elapsed_ms':times[-1]-times[0],'write_index_bytes':write,
            'source_usec':close['source_usec'],'transport_usec':close['transport_usec'],'sink_usec':close['sink_usec'],
            'release_fence_index':close['release_fence_index'],'tail_frames':close['tail_frames'],
            'cutoff_split_frames':close['cutoff_split_frames'],
            'capture_started_ms':close['capture_started_ms'],'close_started_ms':times[0],
            'timing_issued_ms':times[1],'frozen_ms':times[2],'reached_ms':times[3],'closed_ms':times[4],
            'frontier_extra_frames':extra,'frontier_basis':'Pulse capture production; no monitor playback correction',
            'physical_latency':'UNMEASURED','rf_completed':turn['rf_completed'],'microphone_frame_budget':budget})
    require(sum(turn['rf_completed'] for turn in covered)==expected_turns,
            'Completed RF turns are missing finite capture closure')
    return {'status':'PASS','turns':results,'deadline_ms':750,'physical_latency':'UNMEASURED',
            'completed_rf_turns':expected_turns,'completed_intervals':len(results),
            'abandoned_intervals':sum(row.get('kind')=='capture-close' and row.get('complete') is False for row in rows)}


def indexed_capture_source_match(close_result, microphone_pcm, source_rows, source_pcm):
    """Compare accepted PCM with an independently observed native-index span."""
    require(len(source_pcm)%2==0,'The independent capture source tap is misaligned')
    start,cutoff=close_result['start_index'],close_result['cutoff_index']
    expected=bytearray();cursor=start;split=None;post=0
    previous_end=None
    for block in source_rows:
        if block.get('valid') is not True:
            continue
        begin,end,tap,frames=(block[key] for key in ('index_start','index_end','tap_start','tap_frames'))
        require(all(type(value) is int and value>=0 for value in (begin,end,tap,frames)) and
                end-begin==frames and (tap+frames)*2<=len(source_pcm),'The independent source index ledger is invalid')
        if end<=start or begin>=cutoff:
            if begin>=cutoff:post+=frames
            continue
        require(previous_end is None or begin==previous_end,'The independent source interval has a gap or duplicate')
        previous_end=end
        left,right=max(begin,start),min(end,cutoff)
        require(left==cursor,'The independent source is missing part of the finite capture interval')
        expected.extend(source_pcm[(tap+left-begin)*2:(tap+right-begin)*2]);cursor=right
        if begin<cutoff<end:
            split={'start_index':begin,'end_index':end,'cutoff_index':cutoff};post+=end-cutoff
    require(cursor==cutoff and len(expected)//2==cutoff-start,'The independent source did not reach the frozen capture cutoff')
    accepted=microphone_pcm[close_result['mic_tap_start']*2:close_result['mic_tap_end']*2]
    require(accepted==expected,'Accepted microphone PCM differs from the exact indexed pre-cutoff source')
    return {'status':'PASS','turn':close_result['turn'],'compared_frames':len(expected)//2,
            'accepted_frames':len(accepted)//2,'expected_source_sha256':hashlib.sha256(expected).hexdigest(),
            'accepted_sha256':hashlib.sha256(accepted).hexdigest(),'post_cutoff_frames_observed':post,
            'split_block':split,'post_cutoff_excluded':True}


def validate_capture_release_boundary(close, closed, microphone_pcm, source_rows, source_pcm, log_text, mode):
    pattern=(r'INJECT MIC release mode=(\S+) hold_started_ms=(\d+) hold_ended_ms=(\d+) '
             r'callback_started_ms=(\d+) barrier_started_ms=(\d+) release_observed_ms=(\d+) '
             r'callback_ended_ms=(\d+) blocked_peeks=(\d+) cutoff_index=(\d+) sentinel_frames=(\d+) no-discard=1')
    match=re.search(pattern,log_text)
    require(match and match[1]==mode,'The indexed capture release exercise was not retained')
    keys=('hold_started_ms','hold_ended_ms','callback_started_ms','barrier_started_ms','release_observed_ms',
          'callback_ended_ms','blocked_peeks','cutoff_index','sentinel_frames')
    witness=dict(zip(keys,map(int,match.groups()[1:])))
    require(witness['hold_ended_ms']-witness['hold_started_ms']>=150 and witness['cutoff_index']==closed['cutoff_index'],
            'The capture delivery hold or frozen cutoff was not exercised')
    if mode=='held150':
        require(witness['hold_started_ms']+50<=witness['release_observed_ms']<witness['hold_ended_ms'] and
                close['timing_issued_ms']<witness['hold_ended_ms'] and close['frozen_ms']<witness['hold_ended_ms'] and
                witness['blocked_peeks']>0,'The release fence bypassed the held microphone delivery')
    else:
        require(mode=='split' and witness['hold_ended_ms']<=witness['callback_started_ms']<=witness['barrier_started_ms']<=
                witness['release_observed_ms']<witness['callback_ended_ms'] and close['cutoff_split_frames']>0,
                'The coordinator did not observe release inside the selected callback and split its finite cutoff')
    require(witness['release_observed_ms']<=close['close_started_ms']<=close['timing_issued_ms'],
            'Capture closure did not follow the observed retained release')
    result=indexed_capture_source_match(closed,microphone_pcm,source_rows,source_pcm)
    require(result['split_block'] is not None and result['post_cutoff_frames_observed']>0,
            'The indexed source did not expose both sides of the frozen cutoff')
    sentinel=0
    for block in source_rows:
        if block.get('valid') is not True or block['index_end']<=closed['cutoff_index']:
            continue
        offset=max(0,closed['cutoff_index']-block['index_start'])
        payload=array('h');payload.frombytes(source_pcm[(block['tap_start']+offset)*2:(block['tap_start']+block['tap_frames'])*2])
        if sys.byteorder!='little':payload.byteswap()
        sentinel+=sum(sample==30001 for sample in payload)
    accepted=array('h');accepted.frombytes(microphone_pcm[closed['mic_tap_start']*2:closed['mic_tap_end']*2])
    if sys.byteorder!='little':accepted.byteswap()
    require(witness['sentinel_frames']>0 and sentinel==witness['sentinel_frames'] and 30001 not in accepted,
            'Post-cutoff sentinel audio entered the accepted microphone interval or was not observed')
    return {**result,'mode':mode,'exercise':witness,'sentinel_value':30001,
            'post_cutoff_sentinel_frames':sentinel,'post_cutoff_sentinel_excluded':True}


def validate_capture_close_failure(rows,harness,log_text,mode):
    requested=re.search(r'INJECT MIC close-timing requested_ms=(\d+) mode='+mode+r' phase=drain',log_text)
    acknowledged=re.search(r'INJECT MIC close-timing mode='+mode+
        r' real_ack_ms=(\d+) success=1 withheld=([01]) phase=drain',log_text)
    require(requested and acknowledged,'The failing timing request was not independently acknowledged in DRAIN')
    requested_ms,ack_ms=int(requested[1]),int(acknowledged[1])
    require(requested_ms<=ack_ms and acknowledged[2]==('1' if mode=='missing' else '0'),
            'The intended timing ACK suppression or invalid boundary was not exercised')
    starts=[row for row in rows if row.get('event')=='capture-close-start' and row.get('state')==4]
    faults=[row for row in rows if row.get('event')=='first-fault' and row.get('state')==4 and row['ms']>=ack_ms]
    require(starts and any(row['ms']<=requested_ms for row in starts) and faults,
            'The injected timing failure did not target the active DRAIN capture closure')
    fault=faults[0]
    stop_ms=None
    if mode=='stop':
        stopped=re.search(r'INJECT MIC close-timing stop-before-forward stop_ms=(\d+) callback_invoked=1 phase=drain',log_text)
        require(stopped and ack_ms<=int(stopped[1])<=fault['ms'] and fault['status']==1 and fault['operation']=='local-stop',
                'Local Stop was not observed before forwarding the late successful timing callback')
        stop_ms=int(stopped[1])
    elif mode=='invalid':
        require('INJECT MIC close-timing retrograde ' in log_text and
                'write_index_bytes=0 source_usec=0 transport_usec=0 phase=drain' in log_text and
                fault['status']==23 and fault['operation']=='microphone-close-frontier-invalid',
                'The retrograde capture frontier was not rejected')
    else:
        require(mode=='missing' and fault['status'] in (6,9,17),
                'Withheld capture timing did not fail through a bounded existing queue, output or closure guard')
        if fault['status']==9:
            require(fault['operation']=='modem-payload-underflow' and fault.get('stream')=='modem' and
                    any(row.get('kind')=='sample' and row.get('modem_underflows',0)>0 for row in rows),
                    'An unrelated output fault cannot prove capture timing was withheld')
        elif fault['status']==6:
            require(fault['operation'] in ('pcm-queue-full','aggregate-queue-limit') and
                    any(row.get('kind')=='sample' and row.get('state')==4 and requested_ms<=row['ms']<=fault['ms'] and
                        row.get('capture',0)>0 for row in rows),
                    'An unrelated queue defect cannot prove pending capture backpressure')
        else:
            require(fault['operation']=='microphone-close-timeout' and fault.get('stream')=='mic' and
                    fault.get('duration_ms',0)>=750,'The close timeout did not retain its original 750ms guard')
    require(harness['fault']==fault['status'] and harness['turns']==0 and harness['eoo']==0 and
            harness['rf_clear_confirmed'] and not harness['physical_tx'],
            'A failed capture closure emitted EOO, completed a turn or left RF asserted')
    abandoned=[row for row in rows if row.get('kind')=='capture-close' and row.get('complete') is False]
    require(abandoned and not any(row.get('kind')=='capture-close' and row.get('complete') is True for row in rows) and
            abandoned[-1]['close_timeout_ms']==750,'The failed finite capture interval was not retained as incomplete')
    if mode=='stop':
        require(abandoned[-1]['frozen_ms']==0 and abandoned[-1]['cutoff_index']==0,
                'The late timing callback committed a new frontier after local Stop')
    return {'status':'PASS','mode':mode,'phase':'drain','requested_ms':requested_ms,'real_ack_ms':ack_ms,
            'withheld':mode=='missing','fault':fault['status'],'origin':fault['operation'],
            'fault_ms':fault['ms'],'eoo':0,'completed_turns':0,'close_timeout_ms':750,
            'abandoned_interval':abandoned[-1],'stop_requested_ms':stop_ms}


def modem_waveform_match(reference_pcm, reference_rate, observed_pcm, observed_rate, expected_gain=.25):
    """Verify the complete transmitted waveform, independently of vocoder phase.

    A fixed alignment is selected once, then checked across the full reference
    and every overlapping 100 ms window. Five milliseconds at each reference
    edge cover the transport resampler and legacy short-case crop context.
    Missing speech/modem regions cannot be removed by independently aligning
    windows. Separate real-codec decoding still establishes decodability.
    """
    def samples(pcm):
        require(len(pcm) % 2 == 0, 'PCM frame is misaligned')
        result = array('h');result.frombytes(pcm)
        if sys.byteorder != 'little':
            result.byteswap()
        require(len(result) >= 2, 'Modem PCM is empty')
        return result

    def at_rate(pcm, source_rate, target_rate):
        ratio = source_rate / target_rate
        output = []
        for at in range(math.floor((len(pcm)-1)/ratio)):
            position = at * ratio
            index = int(position)
            fraction = position-index
            output.append(pcm[index]*(1-fraction)+pcm[index+1]*fraction)
        return output

    def correlation(left, right):
        n = len(left);sx,sy = sum(left),sum(right)
        xx = sum(value*value for value in left)-sx*sx/n
        yy = sum(value*value for value in right)-sy*sy/n
        return ((sum(a*b for a,b in zip(left,right))-sx*sy/n)/math.sqrt(xx*yy)
                if xx > 1e-9 and yy > 1e-9 else 0.0)

    left = at_rate(samples(reference_pcm),reference_rate,8000)[40:-40]
    observed = samples(observed_pcm)
    right = at_rate(observed,observed_rate,32000)
    require(len(left) >= 800, 'Modem reference is too short for a complete waveform check')
    n = len(left)
    indexes = [round(index*(n-1)/95) for index in range(96)]
    probe = [left[index] for index in indexes]
    maximum = min(32000,len(right)-(n-1)*4-1)
    require(maximum >= 0, 'Monitor recording omitted part of the modem reference')
    best_score,best_lag = -1.0,0
    for lag in range(0,maximum+1,4):
        score = correlation(probe,[right[lag+index*4] for index in indexes])
        if score > best_score:
            best_score,best_lag = score,lag
    for lag in range(max(0,best_lag-4),min(maximum,best_lag+4)+1):
        score = correlation(probe,[right[lag+index*4] for index in indexes])
        if score > best_score:
            best_score,best_lag = score,lag
    # Refine against the native monitor samples. A 32k grid alone cannot
    # represent every 48k headphone sample phase, especially for wideband audio.
    def native_at(position):
        index=int(position);fraction=position-index
        return observed[index]*(1-fraction)+observed[index+1]*fraction
    fine_rate=observed_rate*4
    center=best_lag*fine_rate/32000
    fine_best=(-1.0,0)
    for lag in range(max(0,math.floor(center-fine_rate/32000)),math.ceil(center+fine_rate/32000)+1):
        if lag/fine_rate+(n-1)/8000 >= (len(observed)-1)/observed_rate:
            continue
        score=correlation(probe,[native_at((lag/fine_rate+index/8000)*observed_rate) for index in indexes])
        if score>fine_best[0]:
            fine_best=score,lag
    require(fine_best[0]>=0,'Monitor recording omitted the aligned reference')
    delay=fine_best[1]/fine_rate
    require(delay<=1,'Waveform alignment exceeded its one-second bound')
    aligned=[native_at((delay+index/8000)*observed_rate) for index in range(n)]
    score = correlation(left,aligned)
    require(score >= .95, 'Sink modem waveform differs from its reference; correlation '+str(round(score,5)))
    energy = sum(value*value for value in left)
    require(energy > 0, 'Modem reference contains no signal')
    gain = sum(a*b for a,b in zip(left,aligned))/energy
    require(expected_gain*.96 <= gain <= expected_gain*1.04, 'Sink waveform gain differs from its declared path gain')
    rms = math.sqrt(energy/n)*abs(gain)
    windows = list(range(0,n-799,400))
    if windows[-1] != n-800:
        windows.append(n-800)
    errors = [math.sqrt(sum((aligned[index]-gain*left[index])**2 for index in range(start,start+800))/800)/rms
              for start in windows]
    maximum_error = max(errors)
    require(maximum_error <= .35, 'Sink modem waveform has a missing, duplicated or corrupted region')
    return {'status':'PASS','method':'full_modem_waveform_and_100ms_windows','correlation':score,
            'lag_ms':delay*1000,'fitted_gain':gain,'expected_gain':expected_gain,'minimum_correlation':.95,
            'maximum_lag_ms':1000,'resampler_edge_allowance_ms':5,
            'maximum_window_normalized_error':maximum_error,'allowed_window_normalized_error':.35,
            'reference_frames_compared_at_8k':n,'complete_reference_covered':True,
            'intelligibility':'UNASSESSED'}


def audio_buffer_contract(rows):
    """Derive four-stream reserve from negotiated attributes, not requests."""
    streams={row['stream']:row for row in rows if row.get('kind')=='stream'}
    require(set(streams)=={'mic','radio-rx','headset','modem'},'All four negotiated audio descriptors are required')
    report={'status':'PASS','service_pause_budget_ms':150,'codec_quantum_ms':120,'margin_ms':30,
            'minimum_output_reserve_ms':300,'streams':{},'violations':[]}
    for name,stream in streams.items():
        require(stream['channels']==1 and stream['rate'] in (44100,48000),'Unexpected stream framing')
        per_second=stream['rate']*2
        for field in ('maxlength','tlength','prebuf','minreq','fragsize'):
            require(type(stream[field]) is int and stream[field]>=0,'Invalid negotiated buffer attribute')
        maximum=stream['maxlength']*1000/per_second
        value={'maximum_ms':maximum}
        if name in ('modem','headset'):
            usable=(stream['tlength']-stream['minreq'])*1000/per_second
            prebuffer=stream['prebuf']*1000/per_second
            value.update(usable_ms=usable,prebuffer_ms=prebuffer)
            if usable<300 or prebuffer<300 or maximum>1000 or stream['tlength']>stream['maxlength']:
                report['violations'].append(name+' output reserve or prebuffer is outside the declared profile')
        else:
            fragment=stream['fragsize']*1000/per_second
            minimum=150+2*fragment
            value.update(fragment_ms=fragment,minimum_capture_ms=minimum)
            if not 0<fragment<1000 or maximum<minimum or maximum>1000:
                report['violations'].append(name+' capture cannot retain the service pause and both fragment boundaries')
        report['streams'][name]=value
    if report['violations']:
        report['status']='FAIL'
    return report


def planned_fixture_eof(exit_code, expected_seconds, elapsed_seconds):
    require(0<expected_seconds<=30,'Finite RX fixture must fit the bounded 30-second input contract')
    require(exit_code==0,'Finite RX fixture did not finish and drain normally')
    require(elapsed_seconds>=expected_seconds-.1,'Finite RX fixture ended before its complete payload duration')
    return {'status':'PLANNED_EOF','exit_code':exit_code,'input_seconds':expected_seconds,
            'elapsed_seconds':elapsed_seconds,'repeated':False,
            'method':'Finite paplay input; normal exit follows its playback drain'}


def validate_turn_decode(reference, observed, turn_number, tolerance_seconds=.14):
    """Compare every sink turn with its exact controller modem reference.

    Whole-recording sync is insufficient: another turn could have supplied it.
    Allow one 120 ms modem block plus sample-boundary rounding, while rejecting
    a missing turn, a truncated payload, decoder errors, or no synthesis.
    """
    label = 'Turn ' + str(turn_number)
    for name, record in [('reference', reference), ('sink', observed)]:
        require(record['failures'] == 0, label + ' ' + name + ' decoder failed')
        require(record['synchronized_blocks'] > 0 and record['synthesis_blocks'] > 0,
                label + ' ' + name + ' did not decode speech')
    require(reference['decoded_seconds'] > 0, label + ' reference is empty')
    require(observed['decoded_seconds'] + tolerance_seconds >= reference['decoded_seconds'],
            label + ' sink speech is truncated: ' + str(observed['decoded_seconds']) +
            's vs ' + str(reference['decoded_seconds']) + 's reference')
    require(observed['synthesis_blocks'] + 1 >= reference['synthesis_blocks'],
            label + ' sink lost more than one modem synthesis block')
    return {'status': 'PASS', 'turn': turn_number, 'reference': reference, 'observed': observed,
            'allowed_rounding_and_acquisition_seconds': tolerance_seconds}
