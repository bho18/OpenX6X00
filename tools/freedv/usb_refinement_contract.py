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
BUDGETS_V2 = dict(BUDGETS, cue_duration_ms=500, cue_buffer_ms=500,
                 radio_recovery_budget_ms=2000, volume_retry_limit=2,
                 volume_retry_first_delay_ms=250,volume_retry_second_delay_ms=500,cue_retry_limit=2)
HARDENING_BOOLEAN_FIELDS = ('armed','mic_near_maximum','mic_overload','radio_recovering',
    'headset_volume_recovering','headset_volume_unavailable','cue_recovering','cue_unavailable')
HARDENING_UNSIGNED_FIELDS = ('radio_recovery_started_ms','radio_recovery_attempts',
    'headset_volume_retries','headset_volume_operation','headset_volume_operation_age_ms','cue_retries')
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
    require(type(required_version) is int and required_version in (0, 1, 2),
            'Unsupported USB refinement contract version')
    result = dict(schema=SCHEMA if required_version != 2 else 'x6200.usb.refinement-contract.v2', usb_refinement_contract_version=required_version,
                  status='DIAGNOSTIC_ONLY', scope='Refinement evidence; no physical RF qualification')
    if required_version == 0:
        return result
    budgets=BUDGETS_V2 if required_version==2 else BUDGETS
    sessions = [row for row in rows if isinstance(row, dict) and row.get('kind') == 'session']
    require(len(sessions) == 1 and isinstance(completion, dict),
            'Refinement contract requires one session and immutable completion')
    header = sessions[0]
    identity = {name: header.get(name) for name in IDENTITY}
    for record in (header, completion):
        require(type(record.get('usb_refinement_contract_version')) is int and
                record['usb_refinement_contract_version'] == required_version,
                'Session/completion is not bound to the required USB refinement contract')
        require(all(isinstance(identity[name], str) and identity[name] and record.get(name) == identity[name]
                    for name in IDENTITY), 'Refinement session identity is missing or conflicts')
        if expected_identity is not None:
            require(all(record.get(name) == expected_identity.get(name) for name in IDENTITY),
                    'Refinement receipt belongs to another session/build/source')
    require(all(type(header.get(key)) is int and header[key]==value for key,value in budgets.items()),
            'Refinement session changed or omitted its fixed operational budgets')
    require(header.get('operational') is True and header.get('usb_startup_contract_version')==4,
            'Refinement features require operational startup4')
    samples=[row for row in rows if isinstance(row,dict) and row.get('kind')=='sample']
    require(samples,'Refinement session has no runtime observations')
    previous=None
    for record in [*samples,completion]:
        require(type(record.get('usb_refinement_contract_version')) is int and record['usb_refinement_contract_version']==required_version,
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
        if required_version==2:
            require(all(type(record.get(key)) is bool for key in HARDENING_BOOLEAN_FIELDS) and
                    all(type(record.get(key)) is int and record[key]>=0 for key in HARDENING_UNSIGNED_FIELDS),
                    'Hardening health/control observations are missing or untyped')
            require(record['radio_recovery_started_ms']<=record['sampled_ms'] and
                    record['headset_volume_retries']<=2 and record['cue_retries']<=2 and
                    record['headset_volume_operation']<=3,
                    'Hardening recovery control bounds changed')
            channels=record.get('headset_volume_channel_values')
            require(type(channels) is list and len(channels)<=32 and
                    all(type(value) is int and 0<=value<(1<<32) for value in channels) and
                    (not record['headset_volume_ready'] or len(channels)>0),
                    'Hardening volume readback omitted its complete typed Pulse channel vector')
            require(not record['radio_recovering'] or
                    (record.get('armed') is False and record['cue_active'] is False),
                    'Radio recovery admitted TX or a local preview')
            require(not record['headset_volume_unavailable'] or record['headset_volume_ready'] is False,
                    'Unavailable headphone control claimed an applied volume')
            require(not record['cue_unavailable'] or
                    (record['cue_available'] is False and record['cue_active'] is False),
                    'Unavailable preview claimed active or available playback')
        if previous is not None:
            require(all(record[key]>=previous[key] for key in (*COUNTERS,*TIMESTAMPS)),
                    'Refinement runtime counter or monotonic timestamp moved backwards')
            if required_version==2:
                require(record['radio_recovery_attempts']>=previous['radio_recovery_attempts'],
                        'Radio recovery attempt counter moved backwards')
        previous=record
    for receipt in (row for row in rows if isinstance(row,dict) and row.get('kind')=='startup-readiness'):
        require(type(receipt.get('usb_refinement_contract_version')) is int and receipt['usb_refinement_contract_version']==required_version and
                all(type(receipt.get(key)) is int and receipt[key]==value for key,value in budgets.items()),
                'Startup behavior receipt is not bound to refinement budgets')
    result.update(status='PASS', sample_count=len(samples),mic_recovery_attempts=completion['mic_recovery_attempts'],
                  final_mic_health=completion['mic_health'],
                  volume_confirmed_revision=completion['headset_volume_confirmed_revision'],
                  volume_failed_revision=completion['headset_volume_failed_revision'],
                  cue_completed=completion['cue_completed'],cue_canceled=completion['cue_canceled'],cue_failures=completion['cue_failures'], **identity)
    return result


def refinement_exercise(rows,completion,harness,phases,case):
    """Grade actual C phase observations; callers also retain their raw hashes."""
    if case.startswith('operational-hardening-'):
        return hardening_exercise(rows,completion,harness,phases,case)
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


def hardening_exercise(rows,completion,harness,phases,case):
    """Recheck bounded control/recovery exercises from retained phase records."""
    contract=validate_refinement_contract(rows,completion,2)
    require(type(phases) is list and phases and all(type(row) is dict for row in phases),
            'Hardening exercise phases are missing')
    names=[row.get('phase') for row in phases]
    require(all(isinstance(name,str) and name for name in names) and len(names)==len(set(names)) and
            all(type(row.get('ms')) is int and row['ms']>=0 for row in phases) and
            all(right['ms']>=left['ms'] for left,right in zip(phases,phases[1:])),
            'Hardening phases are duplicated or out of chronological order')
    by_name={row['phase']:row for row in phases}
    def phase(name):
        require(name in by_name,'Hardening exercise omitted its '+name+' phase')
        return by_name[name]
    initial=phase('initial')
    require(initial.get('headset_volume_initialized') is True and initial.get('headset_volume_ready') is True and
            initial.get('headset_volume_percent')==37 and initial.get('headset_muted') is False and
            initial.get('keys')==0 and initial.get('submitted_mic')==0,
            'Hardening exercise did not start with confirmed idle headset volume')
    suffix=case.removeprefix('operational-hardening-')
    faults={'radio-recover-timeout':(4,'radio-status-recovery-timeout'),
            'radio-recover-flapping':(4,'radio-status-recovery-repeated-loss'),
            'radio-recover-clear-blocked':(20,'radio-recovery-clear-timeout'),
            'radio-recover-operation-deadline':(4,'radio-status-recovery-timeout'),
            'radio-recover-unexpected-tx':(4,'unexpected-tx-during-radio-recovery'),
            'radio-recover-protection':(5,'radio-protection'),
            'radio-loss-tx':(4,'radio-packet-stale')}
    expected_fault,origin=faults.get(suffix,(0,None))
    require(harness.get('fault')==expected_fault,'Hardening exercise changed its required fault outcome')
    if origin is not None:
        require(harness.get('first_fault_origin')==origin,'Hardening exercise lost its specific first failure')
    expected_turns=0 if expected_fault or suffix=='mic-overload' else 1
    require(harness.get('turns')==expected_turns and harness.get('eoo')==expected_turns,
            'Hardening exercise did not retain its exact complete warm turn count')
    require(harness.get('keys')==(1 if suffix=='radio-loss-tx' else expected_turns),
            'Hardening recovery or optional controls admitted unexpected key requests')
    for key in ('rade_creations','encoder_creations','decoder_creations'):
        require(type(initial.get(key)) is int and initial[key]==1 and harness.get(key)==initial[key] and
                all(row.get(key)==initial[key] for row in phases),
                'Hardening recovery recreated a warm codec component: '+key)
    if suffix.startswith('volume-'):
        if suffix=='volume-paced-rounded':
            for name,target in (('low0',0),('up1',1),('up5',5),('up9',9),('up10',10),
                                ('up15',15),('up100',100),('down9',9),('final15',15)):
                observed=phase(name)
                require(observed.get('headset_volume_requested_percent')==target and
                        observed.get('headset_volume_pending') is False and observed.get('headset_volume_ready') is True and
                        observed.get('headset_volume_confirmed_revision')==observed.get('headset_volume_requested_revision') and
                        abs(observed.get('headset_volume_percent',-1000)-target)<=3,
                        'Paced detents stalled or lost accumulated intent at '+name)
        elif suffix in ('volume-retry','volume-unavailable-retry'):
            if suffix=='volume-retry':
                recovering=phase('recovering')
                require(recovering.get('headset_volume_recovering') is True and recovering.get('keys')==0 and
                        recovering.get('volume_failures')==1 and phase('rejected-recovered').get('volume_calls',0)>=2,
                        'Transient headphone error was not isolated for bounded recovery')
                require(any(row.get('kind')=='event' and row.get('event')=='headset-volume-error' and
                            type(row.get('duration_ms')) is int and 1000<=row['duration_ms']<=1150 for row in rows),
                        'Headphone recovery never exercised its actual bounded callback timeout')
            else:
                unavailable=phase('volume-unavailable')
                require(unavailable.get('headset_volume_unavailable') is True and
                        unavailable.get('headset_volume_ready') is False and unavailable.get('headset_volume_retries')==2 and
                        unavailable.get('keys')==0,'Persistent volume error did not exhaust its bounded retries')
            final=phase('final55')
            require(final.get('headset_volume_percent')==55 and final.get('headset_volume_requested_percent')==55 and
                    final.get('headset_volume_pending') is False and final.get('headset_volume_unavailable') is False and
                    final.get('headset_volume_confirmed_revision')==final.get('headset_volume_requested_revision'),
                    'Latest volume intent did not survive automatic/contextual recovery')
            if suffix=='volume-unavailable-retry':
                require(final['headset_volume_confirmed_revision']>unavailable.get('headset_volume_failed_revision',0),
                        'Same-target retry reused a failed revision and cannot persist its confirmed setting')
        elif suffix=='volume-external-zero':
            require(phase('external51').get('headset_volume_percent')==51 and
                    phase('external51').get('headset_volume_requested_percent')==51 and
                    phase('zero').get('headset_volume_percent')==0 and
                    phase('restored30').get('headset_volume_percent')==30,
                    'External volume or zero-to-positive restoration lost its authoritative level')
        else:raise ValueError('Unknown hardening volume exercise')
    elif suffix.startswith('cue-'):
        final=phase('final-preview');final_gesture=phase('final-gesture-start')
        require(final.get('cue_available') is True and final.get('cue_unavailable') is False and
                final.get('cue_completed',0)>final_gesture.get('cue_completed',0) and
                final.get('cue_active') is False and final.get('keys')==0 and final.get('submitted_mic')==0,
                'Final local preview did not complete independently from the RF/microphone path')
        require(final_gesture.get('cue_active') is False and final['ms']-final_gesture['ms']>=650 and
                final.get('headset_volume_percent')==100 and final.get('headset_volume_pending') is False and
                final.get('headset_volume_requested_revision')==final.get('headset_volume_confirmed_revision'),
                'Final preview was not independently completed after the latest settled volume gesture')
        if suffix=='cue-final-gesture':
            earlier=phase('earlier-preview');gesture=phase('gesture-start')
            low_start=phase('earlier-gesture-start')
            require(earlier.get('headset_volume_percent')==50 and earlier.get('headset_volume_requested_percent')==50 and
                    earlier.get('headset_volume_ready') is True and earlier.get('headset_volume_pending') is False and
                    earlier.get('headset_volume_requested_revision')==earlier.get('headset_volume_confirmed_revision') and
                    earlier['ms']-low_start['ms']>=650,
                    'Low-level preview was not completed after its confirmed native volume setting')
            require(gesture.get('cue_completed')==earlier.get('cue_completed') and
                    final['cue_completed']>gesture['cue_completed'] and final['ms']-gesture['ms']>=650,
                    'An earlier preview was accepted as the final paused gesture')
            edited=phase('active-preview-edit')
            require(edited.get('cue_active') is True and edited.get('keys')==0 and
                    edited.get('cue_canceled')==gesture.get('cue_canceled'),
                    'New detents truncated the active preview into snippets')
            require(type(final.get('cue_io_pauses')) is int and final['cue_io_pauses']==1 and type(final.get('cue_io_pause_ms')) is int and
                    50<=final['cue_io_pause_ms']<=150,
                    'Preview buffering was not exercised across its independently measured scheduling pause')
            decoded=[phase(name) for name in ('decoded-before-edit','decoded-after-edit','decoded-settled')]
            require(harness.get('rx_synthesis_blocks',0)>=4 and
                    all(row.get('cue_active') is False and row.get('keys')==0 and row.get('submitted_mic')==0 for row in decoded) and
                    all(row.get('cue_completed')==decoded[0].get('cue_completed') for row in decoded[1:]),
                    'Decoded speech was interrupted by a preview or a suppressed cue replayed afterward')
        elif suffix=='cue-recovery':
            recovering=phase('cue-recovering')
            require(recovering.get('cue_recovering') is True and recovering.get('cue_failures',0)>0,
                    'Preview failure never entered isolated recovery')
            require(any(row.get('kind')=='event' and row.get('event')=='cue-error' and
                        row.get('operation')=='preview-underflow' for row in rows),
                    'Preview recovery did not exercise its real underflow callback path')
            canceled=phase('backoff-canceled')
            require(canceled.get('cue_active') is False and canceled.get('keys')==0 and
                    canceled.get('cue_completed')==recovering.get('cue_completed') and
                    canceled['ms']-recovering['ms']>=750,
                    'Preview recovery replayed an invalidated cue after retuning')
        elif suffix=='cue-unavailable-retry':
            unavailable=phase('cue-unavailable')
            require(unavailable.get('cue_unavailable') is True and unavailable.get('cue_retries')==2 and
                    unavailable.get('cue_available') is False and unavailable.get('cue_active') is False,
                    'Persistent optional preview failure did not stop bounded retries')
        else:raise ValueError('Unknown hardening preview exercise')
    elif suffix=='mic-overload':
        near=phase('near-maximum');overload=phase('overload');clean=phase('clean')
        require(near.get('mic_near_maximum') is True and near.get('mic_overload') is False and
                overload.get('mic_overload') is True and clean.get('mic_near_maximum') is False and
                clean.get('mic_overload') is False and all(row.get('keys')==0 for row in (near,overload,clean)),
                'Live microphone maximum/overload warnings disagree with the RX capture signal')
    elif suffix.startswith('radio-recover'):
        recovering=phase('radio-recovering')
        require(recovering.get('radio_recovering') is True and recovering.get('armed') is False and
                recovering.get('cue_active') is False and recovering.get('keys')==0 and
                recovering.get('submitted_mic')==0 and recovering.get('radio_recovery_attempts')==1,
                'Stale RX radio status was not fenced before bounded recovery')
        if not expected_fault:
            restored=phase('radio-restored')
            require(restored.get('radio_recovering') is False and restored.get('fault')==0 and
                    restored.get('keys')==0 and restored.get('submitted_mic')==0 and
                    restored.get('radio_recovery_attempts')==1,
                    'Radio recovery did not return fresh RX before any later press')
            if suffix=='radio-recover':
                preview=phase('preview-before-status');controls=phase('controls-during-recovery')
                settled=phase('canceled-preview-settled')
                require(preview.get('cue_active') is True and preview.get('keys')==0 and
                        controls.get('radio_recovering') is True and controls.get('armed') is False and
                        controls.get('cue_active') is False and controls.get('keys')==0 and controls.get('submitted_mic')==0 and
                        controls.get('headset_volume_percent')==45 and controls.get('headset_volume_requested_percent')==45 and
                        settled.get('cue_active') is False and settled.get('keys')==0 and
                        settled.get('cue_completed')==preview.get('cue_completed') and
                        settled['ms']-restored['ms']>=750,
                        'Concurrent volume/status recovery replayed a canceled preview or lost local controls')
            if suffix.endswith('held'):
                held=phase('held-restored')
                require(held.get('pressed') is True and held.get('armed') is False and held.get('keys')==0 and
                        restored.get('pressed') is False and restored.get('armed') is True,
                        'PTT held through recovery was automatically admitted')
        elif suffix=='radio-recover-flapping':
            require(phase('first-restored').get('radio_recovering') is False and
                    harness.get('radio_recovery_attempts')==1,
                    'Repeated radio loss silently restarted automatic recovery')
        elif suffix in ('radio-recover-timeout','radio-recover-clear-blocked','radio-recover-operation-deadline'):
            failed=phase('fault')
            elapsed=failed['ms']-failed.get('radio_recovery_started_ms',failed['ms'])
            require(2000<=elapsed<=2250,'Radio recovery failed to honor its shared two-second deadline')
            if suffix=='radio-recover-operation-deadline':
                started=failed['radio_recovery_started_ms']
                nested=[row for row in rows if row.get('kind')=='event' and row.get('event')=='operation-start' and
                        row.get('operation')=='modem-flush' and row.get('state')==11 and
                        type(row.get('ms')) is int and started+1400<=row['ms']<failed['ms']]
                require(nested and 0<failed['ms']-nested[-1]['ms']<650,
                        'Shared recovery deadline was not exercised inside the delayed RX operation after startup')
    elif suffix!='radio-loss-tx':raise ValueError('Unknown hardening exercise')
    return dict(schema='x6200.usb.refinement-exercise.v2',status='PASS',case=case,
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


def cue_reference_pcm(version=1):
    """Fixed 600Hz/-20dBFS preview, with the specified 5ms cosine ramps."""
    from array import array
    import math
    import sys
    require(type(version) is int and version in (1,2),'Unsupported local preview waveform version')
    frames=24000 if version==2 else 2880
    samples=array('h')
    for index in range(frames):
        envelope=1.0
        if index<240:envelope=.5-.5*math.cos(math.pi*index/240)
        if index>=frames-240:envelope=.5-.5*math.cos(math.pi*(frames-1-index)/240)
        samples.append(round(3276.7*envelope*math.sin(2*math.pi*600*index/48000)))
    if sys.byteorder!='little':samples.byteswap()
    return samples.tobytes()


def local_cue_waveform(observed_pcm,version=1,*,frame_start=None,frame_end=None,expected_gain=1.0):
    """Find a complete preview within an independently delimited gesture window.

    Version2 requires explicit monitor boundaries for the final gesture, so an
    earlier successful cue cannot satisfy a later gesture's waveform check.
    Boundary provenance is retained and checked by the integration driver.
    Its expected gain comes from the exercised Pulse sink level, so a complete
    but constant-loudness preview cannot pass multiple volume settings.
    """
    import numpy as np
    require(len(observed_pcm)%2==0,'Headset cue monitor is not PCM16 aligned')
    require(type(version) is int and version in (1,2),'Unsupported local preview waveform version')
    total=len(observed_pcm)//2
    if version==2:
        require(type(frame_start) is int and type(frame_end) is int and
                0<=frame_start<frame_end<=total,'Final preview requires a valid explicit monitor gesture window')
        require(type(expected_gain) in (int,float) and 0<expected_gain<=1,
                'Preview expected gain must be finite and within unity')
    else:
        frame_start=0 if frame_start is None else frame_start
        frame_end=total if frame_end is None else frame_end
        require(type(frame_start) is int and type(frame_end) is int and 0<=frame_start<frame_end<=total,
                'Preview monitor bounds are invalid')
    observed=np.frombuffer(observed_pcm,dtype='<i2')[frame_start:frame_end].astype(np.float64)
    reference=np.frombuffer(cue_reference_pcm(version),dtype='<i2').astype(np.float64)
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
    require(correlation>=.985 and (abs(gain-expected_gain)<=expected_gain*.03 if version==2 else 0<gain<=1.05) and peak<=3441,
            'Headset did not render the complete quiet fixed-frequency preview')
    result=dict(schema='x6200.usb.local-cue-waveform.v'+str(version),status='PASS',frequency_hz=600,duration_ms=500 if version==2 else 60,
        ramp_ms=5,level_dbfs=-20,frames=len(reference),minimum_correlation=.985,correlation=correlation,
        measured_gain=gain,monitor_frame_start=frame_start+start,peak_pcm16=peak,
        scope='Actual private-Pulse headset monitor only; physical sound level is unmeasured')
    if version==2:result.update(monitor_window_start=frame_start,monitor_window_end=frame_end,
        minimum_gain=.97*expected_gain,expected_gain=expected_gain,allowed_relative_error=.03)
    return result


def validate_recent_diagnostics(record,expected_identity=None,frozen=None,required_version=None):
    """Validate a bounded copied context window, never lifecycle completeness.

    Ring order is arrival order. Concurrent producers can capture a timestamp
    before acquiring the recorder mutex, and first-fault captured_ms precedes
    freeze serialization, so these are not coherent simultaneous snapshots.
    """
    def unsigned(value,bits=64):return type(value) is int and 0<=value<(1<<bits)
    def signed(value,bits=32):return type(value) is int and -(1<<(bits-1))<=value<(1<<(bits-1))
    require(isinstance(record,dict) and record.get('kind')=='recent-diagnostics' and
            record.get('schema')=='x6200.usb.recent.v1' and type(record.get('usb_refinement_contract_version')) is int and
            record['usb_refinement_contract_version'] in (1,2) and type(record.get('frozen_at_first_fault')) is bool and
            unsigned(record.get('captured_ms')),'Recent diagnostic envelope is malformed or unbound')
    if required_version is not None:
        require(type(required_version) is int and required_version in (1,2) and
                record['usb_refinement_contract_version']==required_version,
                'Recent diagnostics belong to another refinement version')
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
