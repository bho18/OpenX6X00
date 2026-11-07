#!/usr/bin/env python3
"""Typed evidence for optional preview recovery, local cues and volume state.

This augments startup4/output1/open1. A refinement receipt never substitutes
for a completed source-bound startup or the microphone/output conservation
contracts, and historical sessions remain diagnostic-only for these features.
"""

SCHEMA = 'x6200.usb.refinement-contract.v1'
IDENTITY = ('session_id', 'build_id', 'source_commit')
BUDGETS = dict(mic_stale_ms=750, mic_recovery_budget_ms=1500, headset_volume_initial_timeout_ms=2500,
               cue_frequency_hz=600, cue_duration_ms=60, cue_ramp_ms=5, cue_level_dbfs=-20, cue_buffer_ms=20)
COUNTERS = ('mic_recovery_epoch','mic_recovery_attempts','headset_volume_requested_revision',
            'headset_volume_confirmed_revision','headset_volume_failed_revision',
            'cue_requests','cue_completed','cue_canceled','cue_failures')
TIMESTAMPS = ('sampled_ms','rx_sync_changed_ms','rx_audio_last_ms','mic_health_changed_ms','mic_recovery_started_ms')
BOOLEAN_FIELDS = ('headset_volume_initialized','headset_volume_pending','headset_volume_ready',
                  'headset_volume_requested_muted','headset_muted','mic_retry_available','cue_available','cue_active')
HEALTH_REASONS = frozenset(('startup','fresh-capture','capture-stale','flush-rejected','uncork-rejected',
    'timing-rejected','timing-invalid','recovery-timeout','tuning-changed','microphone-not-ready'))
RECENT_EVENT_COLUMNS = ['ms','state','name','operation','stream','status','error','duration_ms']
RECENT_SNAPSHOT_COLUMNS = ['ms','state','fault','mic_health','mic_age_ms','rx_age_ms','io_age_ms',
    'generation','turns','volume_percent','volume_pending','ptt','rf_tx','display_generation','display_dark','drawing_enabled']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_refinement_contract(rows, completion, required_version=1, expected_identity=None):
    require(type(required_version) is int and required_version in (0, 1),
            'Unsupported USB refinement contract version')
    result = dict(schema=SCHEMA, usb_refinement_contract_version=required_version,
                  status='DIAGNOSTIC_ONLY', scope='Refinement evidence; no physical RF qualification')
    if required_version == 0:
        return result
    sessions = [row for row in rows if isinstance(row, dict) and row.get('kind') == 'session']
    require(len(sessions) == 1 and isinstance(completion, dict),
            'Refinement contract requires one session and immutable completion')
    header = sessions[0]
    identity = {name: header.get(name) for name in IDENTITY}
    for record in (header, completion):
        require(type(record.get('usb_refinement_contract_version')) is int and
                record['usb_refinement_contract_version'] == 1,
                'Session/completion is not bound to USB refinement contract1')
        require(all(isinstance(identity[name], str) and identity[name] and record.get(name) == identity[name]
                    for name in IDENTITY), 'Refinement session identity is missing or conflicts')
        if expected_identity is not None:
            require(all(record.get(name) == expected_identity.get(name) for name in IDENTITY),
                    'Refinement receipt belongs to another session/build/source')
    require(all(type(header.get(key)) is int and header[key]==value for key,value in BUDGETS.items()),
            'Refinement session changed or omitted its fixed operational budgets')
    require(header.get('operational') is True and header.get('usb_startup_contract_version')==4,
            'Refinement features require operational startup4')
    samples=[row for row in rows if isinstance(row,dict) and row.get('kind')=='sample']
    require(samples,'Refinement session has no runtime observations')
    previous=None
    for record in [*samples,completion]:
        require(type(record.get('usb_refinement_contract_version')) is int and record['usb_refinement_contract_version']==1,
                'A refinement runtime observation lost its version binding')
        require(all(type(record.get(key)) is int and record[key]>=0 for key in (*COUNTERS,*TIMESTAMPS)),
                'Refinement counters/timestamps must be nonnegative monotonic integers')
        require(all(type(record.get(key)) is bool for key in BOOLEAN_FIELDS),
                'Refinement runtime flags are missing or untyped')
        require(type(record.get('mic_health')) is int and record['mic_health'] in (0,1,2) and
                record.get('mic_health_reason') in HEALTH_REASONS,
                'Refinement microphone health is missing or invalid')
        require(all(record[key]<=record['sampled_ms'] for key in TIMESTAMPS[1:]),
                'Refinement observation refers to a future state transition')
        require(all(type(record.get(key)) is int and 0<=record[key]<=100 for key in
                    ('headset_volume_percent','headset_volume_requested_percent')),
                'Confirmed/requested headset volume is outside its percentage range')
        require(record['headset_volume_confirmed_revision']<=record['headset_volume_requested_revision'] and
                record['headset_volume_failed_revision']<=record['headset_volume_requested_revision'],
                'Volume acknowledgement refers to a future request')
        require(not record['headset_volume_initialized'] or record['headset_volume_confirmed_revision']>0,
                'Initialized volume has no actual confirmed readback revision')
        require(not record['mic_retry_available'] or record['mic_health']==2,
                'Microphone retry is offered outside the unavailable state')
        require(record['mic_health']==0 or record.get('armed') is not True,
                'TX admission remained armed with an unavailable/recovering microphone')
        require(not record['cue_active'] or record.get('state') not in (2,3,4,7),
                'Local preview was active in a transmit transition')
        if previous is not None:
            require(all(record[key]>=previous[key] for key in (*COUNTERS,*TIMESTAMPS)),
                    'Refinement runtime counter or monotonic timestamp moved backwards')
        previous=record
    for receipt in (row for row in rows if isinstance(row,dict) and row.get('kind')=='startup-readiness'):
        require(receipt.get('usb_refinement_contract_version')==1 and
                all(type(receipt.get(key)) is int and receipt[key]==value for key,value in BUDGETS.items()),
                'Startup behavior receipt is not bound to refinement budgets')
    result.update(status='PASS', sample_count=len(samples),mic_recovery_attempts=completion['mic_recovery_attempts'],
                  final_mic_health=completion['mic_health'],
                  volume_confirmed_revision=completion['headset_volume_confirmed_revision'],
                  volume_failed_revision=completion['headset_volume_failed_revision'],
                  cue_completed=completion['cue_completed'],cue_canceled=completion['cue_canceled'],cue_failures=completion['cue_failures'], **identity)
    return result


def refinement_exercise(rows,completion,harness,phases,case):
    """Grade actual C phase observations; callers also retain their raw hashes."""
    contract=validate_refinement_contract(rows,completion)
    require(isinstance(phases,list) and all(isinstance(row,dict) for row in phases),
            'Refinement phase observations are missing')
    names=[row.get('phase') for row in phases]
    require(len(names)==len(set(names)) and all(type(row.get('ms')) is int for row in phases) and
            all(right['ms']>=left['ms'] for left,right in zip(phases,phases[1:])),
            'Refinement phase observations are duplicated or out of chronological order')
    by_name={row['phase']:row for row in phases}
    if case=='operational-volume-startup-fails':
        require(not phases and harness.get('fault')==13 and harness.get('startup_ready') is False and
                harness.get('first_fault_origin')=='headset-initial-volume-not-confirmed' and
                all(harness.get(key)==0 for key in ('keys','turns','submitted_mic','headset_uncorks','headset_payload_writes')),
                'Unconfirmed startup volume admitted audio or TX')
    else:
        initial=by_name.get('initial',{})
        require(initial.get('headset_volume_percent')==37 and initial.get('headset_muted') is False and
                initial.get('headset_volume_ready') is True and initial.get('headset_volume_initialized') is True and
                initial.get('headset_volume_pending') is False and initial.get('keys')==0,
                'Initial headset volume was not actually confirmed before normal mode operation')
    if case.startswith('operational-mic-recover'):
        recovering=by_name.get('recovering',{})
        require(recovering.get('mic_health')==1 and recovering.get('armed') is False and
                recovering.get('mic_meter_ready') is False and recovering.get('keys')==0 and
                recovering.get('mic_recovery_attempts')==1 and recovering.get('mic_age_ms',0)>=750,
                'Inactive microphone staleness was not isolated from RX and TX admission')
        if case=='operational-mic-recover-timeout':
            for label,attempt in (('unavailable',1),('retry-timeout',2)):
                phase=by_name.get(label,{})
                require(phase.get('mic_health')==2 and phase.get('mic_recovery_attempts')==attempt and
                        phase.get('mic_retry_available') is True and phase.get('keys')==0 and phase.get('armed') is False,
                        'Microphone recovery timeout was hidden or silently retried')
                require(1500<=phase.get('mic_health_changed_ms',0)-phase.get('mic_recovery_started_ms',0)<=1650,
                        'Microphone recovery did not honor its shared 1500ms deadline within the unchanged150ms service budget')
        if case=='operational-mic-recover-retune':
            canceled=by_name.get('retune-canceled',{})
            require(canceled.get('mic_health')==2 and canceled.get('mic_health_reason')=='tuning-changed' and
                    canceled.get('keys')==0 and canceled.get('armed') is False,
                    'Tuning did not invalidate the pending microphone recovery epoch')
        fault={'operational-mic-recover-stop':1,'operational-mic-recover-device-loss':2,'operational-mic-recover-radio-loss':4}.get(case,0)
        require(harness.get('fault')==fault,'Recovery changed a Stop/device/radio failure into another outcome')
        if fault:
            require(harness.get('keys')==0 and harness.get('turns')==0 and harness.get('submitted_mic')==0,
                    'Failed recovery admitted microphone audio or RF')
        else:
            recovered=by_name.get('recovered',{})
            attempts=3 if case.endswith('timeout') else 2 if case.endswith('retune') else 1
            require(recovered.get('mic_health')==0 and recovered.get('mic_meter_ready') is True and
                    recovered.get('mic_recovery_attempts')==attempts and recovered.get('keys')==0 and
                    harness.get('turns')==1 and harness.get('eoo')==1 and harness.get('keys')==1,
                    'Fresh recovery did not admit exactly one later warm complete turn')
            if case.endswith('held'):
                require(recovered.get('pressed') is True and recovered.get('armed') is False and
                        recovered.get('physical_release_sequence')==0 and harness.get('physical_release_sequence')==2,
                        'PTT held through recovery was automatically admitted or its release was fabricated')
    elif case.startswith('operational-cue-'):
        preview=by_name.get('cue-preview',{})
        require(preview.get('keys')==0 and preview.get('submitted_mic')==0,
                'A local level preview entered the RF/microphone path')
        if case.endswith('failure'):
            require(preview.get('cue_failures',0)>0 and preview.get('cue_available') is False and
                    preview.get('cue_active') is False and harness.get('cue_connect_failures',0)>0,
                    'Optional cue failure was not disabled independently')
        else:
            require(preview.get('cue_completed',0)>0 and preview.get('cue_failures')==0,
                    'Confirmed idle RX volume edit never completed its local preview')
        if case.endswith('burst'):
            burst=by_name.get('cue-burst',{});before=by_name.get('cue-decoded-before-edit',{});during=by_name.get('cue-during-decoded-speech',{})
            require(0<burst.get('cue_requests',0)<=35 and before and during and during.get('cue_active') is False and
                    during.get('cue_completed')==before.get('cue_completed') and harness.get('cue_press_hits')==1 and
                    harness.get('rx_synthesis_blocks',0)>=4,
                    'Cue burst queued unbounded work, interrupted decoded speech or ignored PTT priority')
        require(harness.get('fault')==0 and harness.get('turns')==1 and harness.get('eoo')==1,
                'Optional cue behavior interrupted the voice modem turn')
    elif case=='operational-volume-readback':
        pending=by_name.get('volume-pending',{});confirmed=by_name.get('volume-confirmed',{})
        failed=by_name.get('volume-failed',{});recovered=by_name.get('volume-recovered',{})
        require(pending.get('headset_volume_pending') is True and pending.get('headset_volume_percent')==37 and
                pending.get('headset_volume_requested_percent')==48 and
                pending.get('headset_volume_requested_revision',0)>pending.get('headset_volume_confirmed_revision',0),
                'Pending volume edit replaced the confirmed value optimistically')
        require(confirmed.get('headset_volume_percent')==48 and confirmed.get('headset_volume_pending') is False and
                confirmed.get('headset_volume_confirmed_revision')==confirmed.get('headset_volume_requested_revision'),
                'Volume edit completed without a matching actual readback')
        require(failed.get('headset_volume_percent')==48 and failed.get('headset_volume_failed_revision',0)>0 and
                failed.get('headset_volume_failed_revision')==failed.get('headset_volume_requested_revision'),
                'Failed volume request lost the confirmed level or failure revision')
        require(recovered.get('headset_volume_percent')==55 and recovered.get('headset_volume_pending') is False and
                recovered.get('headset_volume_confirmed_revision')==recovered.get('headset_volume_requested_revision') and
                recovered.get('headset_volume_confirmed_revision',0)>failed.get('headset_volume_failed_revision',0) and
                harness.get('cue_requests')==0 and harness.get('fault')==0 and harness.get('turns')==1,
                'A rejected volume edit did not recover by a later confirmed request')
    return dict(schema='x6200.usb.refinement-exercise.v1',status='PASS',case=case,
                contract=contract,phases=phases,physical_release_sequence=harness.get('physical_release_sequence'))


def validate_volume_readbacks(proof):
    require(proof.get('schema')=='x6200.usb.refinement-volume-readbacks.v1' and proof.get('status')=='PASS',
            'Independent refinement volume readbacks are missing')
    initial=proof.get('initial_sinks',{});phases=proof.get('phases',[])
    names=('volume-initial','volume-pending','volume-confirmed','volume-failed','volume-recovered')
    target='alsa_output.usb-fixture';others=('radio_sink_fixture','unrelated_sink')
    require(set(initial)=={target,*others} and len(phases)==len(names),
            'Refinement volume readbacks omitted the initial or unrelated sink state')
    for entry,name,level in zip(phases,names,(37,None,48,48,55)):
        snapshot=entry.get('snapshot',{});pulse=entry.get('pulse',{})
        require(snapshot.get('phase')==name and set(pulse)==set(initial) and
                all(pulse[sink]==initial[sink] for sink in others),
                'A headset volume edit changed another sink or skipped a readback phase')
        actual=pulse[target]
        require(actual.get('mute') is False and len(actual.get('volume',[]))==2,
                'Confirmed headset readback is muted or lacks both hardware channels')
        if level is not None:
            baseline=initial[target].get('volume',[])
            require(len(baseline)==2 and max(baseline)>0 and all(type(value) is int for value in actual['volume']) and
                    abs(max(actual['volume'])-65536*level/100)<=1 and
                    abs(actual['volume'][1]*baseline[0]-actual['volume'][0]*baseline[1])<=65536 and
                    snapshot.get('headset_volume_percent')==level,
                    'Controller volume does not agree with independently queried Pulse master state')
    return proof


def cue_reference_pcm():
    """Fixed 600Hz/60ms/-20dBFS preview, with the specified 5ms cosine ramps."""
    from array import array
    import math
    import sys
    samples=array('h')
    for index in range(2880):
        envelope=1.0
        if index<240:envelope=.5-.5*math.cos(math.pi*index/240)
        if index>=2640:envelope=.5-.5*math.cos(math.pi*(2879-index)/240)
        samples.append(round(3276.7*envelope*math.sin(2*math.pi*600*index/48000)))
    if sys.byteorder!='little':samples.byteswap()
    return samples.tobytes()


def local_cue_waveform(observed_pcm):
    """Find a complete local preview in the independently captured headset PCM."""
    import numpy as np
    require(len(observed_pcm)%2==0,'Headset cue monitor is not PCM16 aligned')
    observed=np.frombuffer(observed_pcm,dtype='<i2').astype(np.float64)
    reference=np.frombuffer(cue_reference_pcm(),dtype='<i2').astype(np.float64)
    require(len(observed)>=len(reference),'Headset cue monitor is shorter than one complete preview')
    length=len(observed)+len(reference)-1;fft_length=1<<(length-1).bit_length()
    cross=np.fft.irfft(np.fft.rfft(observed,fft_length)*np.fft.rfft(reference[::-1],fft_length),fft_length)
    cross=cross[len(reference)-1:len(observed)]
    energy=np.r_[0.,np.cumsum(observed*observed)]
    energy=energy[len(reference):]-energy[:-len(reference)]
    reference_energy=float(reference@reference)
    normalized=cross/np.sqrt(np.maximum(energy*reference_energy,1.))
    start=int(np.argmax(normalized));correlation=float(normalized[start]);gain=float(cross[start]/reference_energy)
    peak=int(np.max(np.abs(observed[start:start+len(reference)])))
    require(correlation>=.985 and 0<gain<=1.05 and peak<=3441,
            'Headset did not render the complete quiet fixed-frequency preview')
    return dict(schema='x6200.usb.local-cue-waveform.v1',status='PASS',frequency_hz=600,duration_ms=60,
        ramp_ms=5,level_dbfs=-20,frames=2880,minimum_correlation=.985,correlation=correlation,
        measured_gain=gain,monitor_frame_start=start,peak_pcm16=peak,
        scope='Actual private-Pulse headset monitor only; physical sound level is unmeasured')


def validate_recent_diagnostics(record,expected_identity=None,frozen=None):
    """Validate a bounded copied context window, never lifecycle completeness.

    Ring order is arrival order. Concurrent producers can capture a timestamp
    before acquiring the recorder mutex, and first-fault captured_ms precedes
    freeze serialization, so these are not coherent simultaneous snapshots.
    """
    def unsigned(value,bits=64):return type(value) is int and 0<=value<(1<<bits)
    def signed(value,bits=32):return type(value) is int and -(1<<(bits-1))<=value<(1<<(bits-1))
    require(isinstance(record,dict) and record.get('kind')=='recent-diagnostics' and
            record.get('schema')=='x6200.usb.recent.v1' and type(record.get('usb_refinement_contract_version')) is int and
            record['usb_refinement_contract_version']==1 and type(record.get('frozen_at_first_fault')) is bool and
            unsigned(record.get('captured_ms')),'Recent diagnostic envelope is malformed or unbound')
    require(all(isinstance(record.get(name),str) and 0<len(record[name])<=200 for name in IDENTITY),
            'Recent diagnostic identity is missing')
    if expected_identity is not None:
        require(all(record[name]==expected_identity.get(name) for name in IDENTITY),
                'Recent diagnostics belong to another session/build/source')
    if frozen is not None:require(record['frozen_at_first_fault'] is frozen,'Recent diagnostic kind conflicts with its freeze flag')
    display=record.get('last_display',{});mic=record.get('microphone',{});radio=record.get('radio_age',{})
    require(all(isinstance(value,dict) for value in (display,mic,radio)),'Recent diagnostic context is not an object')
    require(all(type(display.get(name)) is bool for name in ('known','dark','viewers','drawing_enabled')) and
            unsigned(display.get('monotonic_ms')) and unsigned(display.get('generation')) and
            type(display.get('reason')) is int and 0<=display['reason']<=8 and
            signed(display.get('invalidation_count')) and signed(display.get('brightness'),16),
            'Recent display context is missing or untyped')
    require(all(unsigned(mic.get(name)) for name in ('last_valid_ms','captured_frames','recovery_epoch')) and
            type(radio.get('known')) is bool and unsigned(radio.get('age_ms')),
            'Recent microphone or radio-age context is missing or untyped')
    window=record.get('window',{})
    require(isinstance(window,dict),'Recent diagnostic window is not an object')
    require(window.get('schema')=='x6200.usb.recent-window.v1' and window.get('frozen') is record['frozen_at_first_fault'] and
            type(window.get('event_capacity')) is int and window['event_capacity']==64 and
            type(window.get('snapshot_capacity')) is int and window['snapshot_capacity']==16 and
            window.get('labels')=='printable-ascii-prefixes-23B' and
            window.get('event_columns')==RECENT_EVENT_COLUMNS and window.get('snapshot_columns')==RECENT_SNAPSHOT_COLUMNS and
            all(unsigned(window.get(name)) for name in ('events_evicted','snapshots_evicted','events_rejected','snapshots_rejected')),
            'Recent window capacities, columns or retention counters changed')
    events,snapshots=window.get('events'),window.get('snapshots')
    require(type(events) is list and len(events)<=64 and type(snapshots) is list and len(snapshots)<=16,
            'Recent diagnostic window exceeds its fixed capacities')
    for row in events:
        require(type(row) is list and len(row)==8 and unsigned(row[0]) and unsigned(row[1],8) and
                all(isinstance(text,str) and len(text)<=23 and all(0x20<=ord(char)<=0x7e for char in text) for text in row[2:4]) and
                type(row[4]) is int and 0<=row[4]<=4 and signed(row[5]) and signed(row[6]) and unsigned(row[7]),
                'Recent event row is malformed or exceeds a bounded field')
    for row in snapshots:
        require(type(row) is list and len(row)==16 and unsigned(row[0]) and unsigned(row[1],8) and
                unsigned(row[2],16) and unsigned(row[3],8) and all(unsigned(row[i],32) for i in (4,5,6)) and
                all(unsigned(row[i]) for i in (7,8,13)) and type(row[9]) is int and 0<=row[9]<=100 and
                all(type(row[i]) is bool for i in (10,11,12,14,15)),
                'Recent snapshot row is malformed or exceeds a bounded field')
    return dict(schema='x6200.usb.recent-diagnostic-contract.v1',status='DIAGNOSTIC_ONLY',
        scope='Bounded copied recent context; does not replace the primary trace, first fault or completion contracts',
        frozen_at_first_fault=record['frozen_at_first_fault'],captured_ms=record['captured_ms'],
        events=len(events),snapshots=len(snapshots),events_evicted=window['events_evicted'],snapshots_evicted=window['snapshots_evicted'],
        **{name:record[name] for name in IDENTITY})
