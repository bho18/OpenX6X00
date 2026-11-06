"""Bounded host-test processes and independent USB audio evidence checks.

These helpers are test infrastructure; they never contact a radio.
"""
from pathlib import Path
from array import array
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


def validate_operation_phase(rows, phase, mode):
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
    return {'phase':phase,'mode':mode,'operation':'modem-flush','trace_state':states[phase],
            'observed_status':observed['status'],'duration_ms':observed['duration_ms']}


def validate_drain_loss_wait(rows, harness, log_text):
    witness=re.search(r'RF_DROP_DURING_TRIGGER_WAIT started_ms=(\d+) drop_ms=(\d+) ack_ms=(\d+)',log_text)
    require(witness is not None,'RF loss during the delayed trigger wait was not retained')
    started,dropped,acknowledged=map(int,witness.groups())
    require(started<dropped<acknowledged and acknowledged-started>=650,
            'RF loss did not occur inside the requested successful 650ms trigger ACK wait')
    starts=[row for row in rows if row.get('kind')=='event' and row.get('event')=='operation-start'
            and row.get('operation')=='modem-trigger' and row.get('state')==4]
    endings=[row for row in rows if row.get('kind')=='event' and row.get('event')=='operation-end'
             and row.get('operation')=='modem-trigger' and row.get('state')==4 and row.get('status')==1]
    require(any(started<=begin['ms']<dropped<acknowledged<=end['ms'] for begin in starts for end in endings),
            'The trace does not place RF loss inside the successful DRAIN trigger operation')
    faults=[row for row in rows if row.get('event')=='first-fault' and row.get('status')==4
            and row.get('operation')=='tx-status-disappeared-during-drain' and row.get('state')==4]
    require(faults and min(row['ms'] for row in faults)>=acknowledged,
            'Fresh RF state was not rejected after the delayed trigger returned')
    require(harness['fault']==4 and harness['turns']==0 and not harness['physical_tx'],
            'A turn with RF loss during the trigger wait was counted as complete')
    return {'status':'PASS','operation':'modem-trigger','phase':'tx','state':4,
            'started_ms':started,'rf_drop_ms':dropped,'acknowledged_ms':acknowledged,
            'successful_ack_wait_ms':acknowledged-started,'completed_turns':0}


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
