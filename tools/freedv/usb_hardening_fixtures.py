"""Synthetic hardening inputs for unit gate tests; never runtime qualification.

Only the unit-test fixture builder imports this module. Actual C controller,
Pulse, waveform and collector runs produce separate source-bound evidence.
"""
import json
from pathlib import Path
from usb_refinement_contract import (BUDGETS_V2,validate_refinement_contract,hardening_exercise,
    HARDENING_BOOLEAN_FIELDS,HARDENING_UNSIGNED_FIELDS,cue_reference_pcm,local_cue_waveform)


def hardening_case_fixture(root,result,case):
    from usb_test_campaign import operational_proof,sha
    from test_usb_campaign import atomic_json,refresh_capture_contract_fixture
    from usb_output_contract import validate_output_contract
    root=Path(root);directory=root/'result'
    path=directory/'freedv-usb-session-unit.jsonl';cp=path.with_name(path.stem+'-completion.json')
    rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    completion=json.loads(cp.read_text());h=result['harness'];result['synthetic']=True
    suffix=case.removeprefix('operational-hardening-')
    origins={'radio-recover-timeout':'radio-status-recovery-timeout',
        'radio-recover-flapping':'radio-status-recovery-repeated-loss',
        'radio-recover-clear-blocked':'radio-recovery-clear-timeout',
        'radio-recover-operation-deadline':'radio-status-recovery-timeout',
        'radio-recover-unexpected-tx':'unexpected-tx-during-radio-recovery',
        'radio-recover-protection':'radio-protection','radio-loss-tx':'radio-packet-stale'}
    if suffix in origins:h['first_fault_origin']=origins[suffix]
    if suffix=='radio-loss-tx':h.update(keys=1,key_attempts=1)
    common=dict(usb_refinement_contract_version=2,mic_health=0,mic_health_reason='fresh-capture',
        mic_health_changed_ms=100,mic_recovery_started_ms=0,mic_recovery_epoch=0,mic_recovery_attempts=0,
        mic_retry_available=False,mic_meter_ready=True,mic_age_ms=0,pressed=False,
        rx_sync_changed_ms=100,rx_audio_last_ms=100,headset_volume_initialized=True,
        headset_volume_ready=True,headset_volume_pending=False,headset_volume_percent=37,
        headset_volume_requested_percent=37,headset_volume_requested_muted=False,headset_muted=False,
        headset_volume_requested_revision=1,headset_volume_confirmed_revision=1,headset_volume_failed_revision=0,
        cue_available=True,cue_active=False,cue_requests=0,cue_completed=0,cue_canceled=0,cue_failures=0,
        keys=0,turns=0,submitted_mic=0,physical_release_sequence=0,state=1,fault=0,synthetic=True,
        rade_creations=1,encoder_creations=1,decoder_creations=1,
        **{key:False for key in HARDENING_BOOLEAN_FIELDS},
        **{key:0 for key in HARDENING_UNSIGNED_FIELDS},
        headset_volume_channel_values=[24248,18186 if suffix.startswith('volume-') else 24248],
        cue_io_pauses=0,cue_io_pause_ms=0,volume_calls=0,volume_failures=0)
    for row in rows:
        if row.get('kind') in ('session','startup-readiness'):
            row.update(usb_refinement_contract_version=2,synthetic=True,**BUDGETS_V2)
        if row.get('kind')=='capture-close' and row.get('complete') is True:
            row.update(close_trigger='ptt-release',physical_release_sequence=h['physical_release_sequence'])
        if row.get('event')=='capture-navigation-observed':row['event']='capture-release-observed'
    phases=[]
    def phase(name,at,**changes):
        common.update(changes,sampled_ms=at,rx_audio_last_ms=at)
        record=dict(common,phase=name,ms=at);phases.append(record);rows.append(dict(record,kind='sample'))
        return record
    def volume(name,at,target,actual=None,**changes):
        actual=target if actual is None else actual
        revision=common['headset_volume_requested_revision']+1
        return phase(name,at,headset_volume_percent=actual,headset_volume_requested_percent=target,
            headset_volume_requested_revision=revision,headset_volume_confirmed_revision=revision,
            headset_volume_channel_values=[round(65536*actual/100),round(65536*actual*(.5 if suffix=='volume-external-zero' else .75 if suffix.startswith('volume-') else 1.)/100)],**changes)
    phase('initial',150)
    if suffix=='volume-paced-rounded':
        for index,(name,target) in enumerate((('low0',0),('up1',1),('up5',5),('up9',9),('up10',10),
                ('up15',15),('up100',100),('down9',9),('final15',15)),1):
            volume(name,150+index*150,target,((target+2)//5)*5)
    elif suffix in ('volume-retry','volume-unavailable-retry'):
        if suffix=='volume-retry':
            phase('recovering',300,headset_volume_recovering=True,headset_volume_retries=1,
                  headset_volume_requested_percent=51,headset_volume_requested_revision=2,
                  headset_volume_pending=True,volume_calls=1,volume_failures=1)
            volume('rejected-recovered',650,55,headset_volume_recovering=False,headset_volume_pending=False,volume_calls=2)
            rows.append(dict(kind='event',event='headset-volume-error',operation='headset-volume-set',
                stream='headset',ms=1700,state=1,status=-1,error=0,duration_ms=1000,synthetic=True))
        else:
            phase('volume-unavailable',1100,headset_volume_unavailable=True,headset_volume_ready=False,
                  headset_volume_retries=2,headset_volume_failed_revision=2,
                  headset_volume_requested_percent=55,headset_volume_requested_revision=2)
        volume('final55',1800,55,headset_volume_ready=True,headset_volume_recovering=False,
               headset_volume_unavailable=False,headset_volume_pending=False,headset_volume_retries=0)
    elif suffix=='volume-external-zero':
        volume('external51',300,51);volume('zero',500,0);volume('restored30',700,30)
    elif suffix.startswith('cue-'):
        if suffix=='cue-final-gesture':phase('earlier-gesture-start',300)
        volume('earlier-preview',1000,50 if suffix=='cue-final-gesture' else 41,cue_requests=1,cue_completed=1)
        phase('gesture-start',1400)
        if suffix=='cue-recovery':
            volume('cue-recovering',1800,70,cue_failures=1,cue_retries=1,cue_recovering=True,cue_available=False)
            rows.append(dict(kind='event',event='cue-error',operation='preview-underflow',stream='headset',
                ms=1750,state=1,status=-1,error=0,duration_ms=0,synthetic=True))
            phase('backoff-canceled',3100,cue_recovering=False,cue_available=True,cue_active=False)
        elif suffix=='cue-unavailable-retry':
            volume('cue-unavailable',2400,70,cue_failures=3,cue_retries=2,cue_unavailable=True,cue_available=False)
        else:
            phase('active-edit',1600,cue_active=True,cue_requests=2,cue_io_pauses=1,cue_io_pause_ms=50)
            volume('active-preview-edit',1700,70,cue_active=True)
            phase('intermediate-completed',2100,cue_active=False,cue_completed=2)
        phase('final-gesture-start',4200,cue_recovering=False,cue_unavailable=False,cue_available=True,cue_retries=0)
        volume('final-preview',5050,100,cue_requests=common['cue_requests']+1,cue_completed=common['cue_completed']+1)
        if suffix=='cue-final-gesture':
            phase('decoded-before-edit',6000);phase('decoded-after-edit',6200);phase('decoded-settled',8900)
            h['rx_synthesis_blocks']=4
    elif suffix=='mic-overload':
        phase('near-maximum',400,mic_near_maximum=True,mic_overload=False)
        phase('overload',600,mic_near_maximum=True,mic_overload=True)
        phase('clean',900,mic_near_maximum=False,mic_overload=False)
    elif suffix.startswith('radio-recover'):
        if suffix=='radio-recover':volume('preview-before-status',400,39,cue_active=True,cue_requests=1)
        phase('radio-recovering',1000,state=11,radio_recovering=True,radio_recovery_started_ms=1000,radio_recovery_attempts=1,cue_active=False)
        if suffix=='radio-recover':
            volume('controls-during-recovery',1150,45,cue_active=False)
        if suffix in ('radio-recover','radio-recover-held','radio-recover-flapping'):
            if suffix=='radio-recover-held':phase('held-restored',1700,state=1,radio_recovering=False,pressed=True)
            phase('radio-restored',1800,state=1,radio_recovering=False,pressed=False,armed=True)
            if suffix=='radio-recover':phase('canceled-preview-settled',2600,cue_active=False)
            if suffix=='radio-recover-flapping':
                phase('first-restored',1850);phase('repeated-loss',2300,armed=False,fault=4)
        if suffix=='radio-recover-operation-deadline':rows.append(dict(kind='event',ms=2650,event='operation-start',operation='modem-flush',state=11,status=0,error=0,duration_ms=0))
        if h['fault']:
            phase('fault',3050 if suffix in ('radio-recover-timeout','radio-recover-clear-blocked','radio-recover-operation-deadline') else 2400,
                  fault=h['fault'],state=5,armed=False,radio_recovering=False)
            phase('stopped',3200,fault=h['fault'],state=5,armed=False)
    elif suffix=='radio-loss-tx':
        phase('tx-status-lost',1300,state=5,fault=4,keys=1)
        phase('fault',1350);phase('stopped',1500)
    else:raise ValueError('Unknown synthetic hardening case: '+case)
    end=max([row.get('ms',0) for row in rows]+[5500])+100
    if not h['fault'] and h['turns']:
        phase('after-turn',end,keys=h['keys'],turns=h['turns'],submitted_mic=h['submitted_mic'],armed=False)
        end+=100
    final=next(row for row in reversed(rows) if row.get('kind')=='sample' and 'phase' not in row)
    final.update(common,ms=end,sampled_ms=end,rx_audio_last_ms=end,keys=h['keys'],turns=h['turns'],
                 submitted_mic=h['submitted_mic'],state=5 if h['fault']>1 else 6,pressed=False,armed=False,fault=h['fault'])
    if h['fault']:final.update(first_fault_code=h['fault'],first_fault_origin=h['first_fault_origin'])
    # Every original runtime sample needs the version2 fields as well. The
    # existing base fixture has only its final sample; phase samples precede it.
    for row in rows:
        if row.get('kind')=='sample' and 'phase' not in row:
            for key,value in common.items():row.setdefault(key,value)
    completion.update({key:value for key,value in final.items() if key not in ('kind','ms','phase')},
        last_sample_ms=end,finalized_ms=end+1,usb_refinement_contract_version=2)
    rows=sorted(rows,key=lambda row:(-1 if row.get('kind')=='session' else row.get('ms',
        row.get('closed_ms',row.get('completed_us',0)//1000)),row.get('kind','')))
    completion['trace_rows']=sum(row.get('kind') in ('event','sample') for row in rows)
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows));atomic_json(cp,completion)
    ready=next((row for row in rows if row.get('kind')=='startup-readiness'),None)
    if ready:atomic_json(path.with_name(path.stem+'-readiness.json'),ready)
    refresh_capture_contract_fixture(root,result)
    rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()];completion=json.loads(cp.read_text())
    files=dict(trace_file=path.name,completion_file=cp.name,trace_sha256=sha(path),completion_sha256=sha(cp))
    contract=validate_refinement_contract(rows,completion,2)
    result.update(usb_refinement_contract_version=2,refinement_contract=dict(
        schema='x6200.usb.refinement-contract-set.v1',status='PASS',usb_refinement_contract_version=2,
        sessions=[dict(contract,**files)]))
    for key in ('rade_creations','encoder_creations','decoder_creations','radio_recovery_attempts'):
        h[key]=common[key]
    h.update({key:common[key] for key in ('cue_requests','cue_completed','cue_canceled','cue_failures')})
    exercise=hardening_exercise(rows,completion,h,phases,case)
    pp=directory/'synthetic-hardening-phases.jsonl';pp.write_text(''.join(json.dumps(row)+'\n' for row in phases))
    exercise['phases_artifact']=dict(path=str(pp),sha256=sha(pp));result['refinement_exercise']=exercise
    result['operational']=operational_proof(rows,completion,h,case)
    output=validate_output_contract(rows,completion)
    result['output_contract']['sessions']=[dict(output,**files)]
    if suffix.startswith('volume-'):
        target='alsa_output.usb-fixture'
        def sinks(level,balance=.75):
            maximum=int(65536*level/100)
            # Model the independently observed integer volume anchor, rather
            # than rounding its nominal percentage ratio a second time.
            anchor=(33423,16711) if balance==.5 else (26214,19660)
            channels=[maximum,maximum*anchor[1]//anchor[0]]
            return {target:dict(mute=False,volume=channels),
                'radio_sink_fixture':dict(mute=False,volume=[65536,65536]),
                'unrelated_sink':dict(mute=True,volume=[15073,12452])}
        names=(('up1','up5','up9','up10','up15','up100') if suffix=='volume-paced-rounded' else
               ('external51','restored30') if suffix=='volume-external-zero' else ('final55',))
        observed=[]
        for name in names:
            snapshot=dict(next(record for record in phases if record['phase']==name))
            pulse=sinks(snapshot['headset_volume_percent'],.5 if suffix=='volume-external-zero' else .75);entry=dict(snapshot=snapshot,pulse=pulse)
            for kind,body in (('snapshot',snapshot),('pulse',pulse)):
                artifact=directory/('synthetic-hardening-'+name+'-'+kind+'.json');atomic_json(artifact,body)
                entry.update({kind+'_path':str(artifact),kind+'_sha256':sha(artifact)})
            observed.append(entry)
        result['hardening_volume_readbacks']=dict(schema='x6200.usb.hardening-volume-readbacks.v1',
            status='PASS',synthetic=True,initial_sinks=sinks(40),phases=observed)
    if suffix.startswith('cue-'):
        from array import array
        reference=array('h');reference.frombytes(cue_reference_pcm(2))
        low_gain=.125 if suffix=='cue-final-gesture' else .2
        earlier=array('h',(round(value*low_gain) for value in reference)).tobytes()
        pcm=earlier+b'\0'*(48000*2)+reference.tobytes()+b'\0'*(9600*2)
        monitor=directory/'synthetic-hardening-headset.raw';monitor.write_bytes(pcm)
        ledger=directory/'synthetic-hardening-headset.jsonl'
        start=next(record['ms'] for record in phases if record['phase']=='final-gesture-start')
        end=next(record['ms'] for record in phases if record['phase']=='final-preview')+200
        blocks=[dict(kind='block',received_ns=1000*1000000,frame_start=0,frames=24000),
                dict(kind='block',received_ns=2000*1000000,frame_start=24000,frames=48000),
                dict(kind='block',received_ns=(start+100)*1000000,frame_start=72000,frames=24000),
                dict(kind='block',received_ns=(end-50)*1000000,frame_start=96000,frames=9600)]
        ledger.write_text(''.join(json.dumps(record)+'\n' for record in blocks))
        window=dict(start_ms=start,end_ms=end,frame_start=72000,frame_end=105600)
        result['local_cue_output']=dict(schema='x6200.usb.local-cue-output.v2',status='PASS',synthetic=True,
            waveform=local_cue_waveform(pcm,2,frame_start=window['frame_start'],frame_end=window['frame_end']),
            gesture_window=window,monitor=dict(path=str(monitor),sha256=sha(monitor)),
            ledger=dict(path=str(ledger),sha256=sha(ledger)))
        if suffix=='cue-final-gesture':
            low_start=next(record['ms'] for record in phases if record['phase']=='earlier-gesture-start')
            low_end=next(record['ms'] for record in phases if record['phase']=='earlier-preview')+200
            low_window=dict(start_ms=low_start,end_ms=low_end,frame_start=0,frame_end=24000)
            low=local_cue_waveform(pcm,2,frame_start=0,frame_end=24000,expected_gain=.125)
            result['local_cue_output'].update(low_level_window=low_window,low_level_waveform=low,
                level_ratio=result['local_cue_output']['waveform']['measured_gain']/low['measured_gain'],
                gain_mapping=dict(method='pa_sw_volume_to_linear',raw_volume=32768,linear_gain=.125))
            snapshot=dict(next(record for record in phases if record['phase']=='earlier-preview'))
            initial={'alsa_output.usb-fixture':dict(mute=False,volume=[65536,65536]),
                'radio_sink_fixture':dict(mute=False,volume=[65536,65536]),
                'unrelated_sink':dict(mute=False,volume=[65536,65536])}
            pulse={name:dict(mute=entry['mute'],volume=list(entry['volume'])) for name,entry in initial.items()}
            pulse['alsa_output.usb-fixture']=dict(mute=False,volume=[32768,32768])
            entry=dict(snapshot=snapshot,pulse=pulse)
            for kind,body in (('snapshot',snapshot),('pulse',pulse)):
                artifact=directory/('synthetic-hardening-low-preview-'+kind+'.json');atomic_json(artifact,body)
                entry.update({kind+'_path':str(artifact),kind+'_sha256':sha(artifact)})
            result['local_cue_output']['low_level_readbacks']=dict(
                schema='x6200.usb.hardening-volume-readbacks.v1',status='PASS',synthetic=True,
                initial_sinks=initial,phases=[entry])
    return result
