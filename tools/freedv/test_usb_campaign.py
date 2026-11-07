#!/usr/bin/env python3
"""Qualification-history and matrix-failure regressions; no audio/radio needed."""
import copy
import hashlib
from array import array
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
                               immutable_json, sha, validate_campaign, INFRASTRUCTURE_PREREQUISITES, INFRASTRUCTURE_CHECKS, validate_tail_backpressure_run, validate_capture_close_run,
                               validate_capture_source_boundary_run, validate_capture_close_failure_run,
                               host_resource_configuration, validate_host_resources, PRESSURE_COUNTERS, validate_sink_turns_run, validate_release_lock_protocol, validate_capture_pipeline_tail_run, validate_output_contract_run, validate_backend_profile_run, validate_observer_admission_run, validate_backend_failure_run, validate_audio_controls_run, validate_audio_gain_turn_run)
from usb_test_campaign import validate_headset_volume_flags, operational_proof, validate_operational_run


SYNTHETIC_TRANSPORT_LIBRARY=b'synthetic-libsamplerate-gate-fixture'
SYNTHETIC_TRANSPORT_SHA=hashlib.sha256(SYNTHETIC_TRANSPORT_LIBRARY).hexdigest()


def resource_fixture(root,identity,configuration=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    config=configuration or identity.get('host_resource_configuration') or host_resource_configuration()
    def snapshot(current):
        limit=16*1024**3;head=limit-current
        return {'status':'KNOWN_BOUNDED','errors':[],'cgroup_path':'/sys/fs/cgroup/unit-fixture',
            'headroom_bytes':head,'memory':{'current_bytes':current,'max_bytes':limit,'high_bytes':None,
                'effective_limit_bytes':limit,'headroom_bytes':head,
                'events':{'high':3,'max':7,'oom':0,'oom_kill':0},
                'stat':{'anon':50*1024**2,'file':5*1024**3,'shmem':0,'pgscan_direct':11,'pgsteal_direct':9}},
            'cpu':{'quota_us':400000,'period_us':100000,'quota_cores':4.,'affinity_cores':4,
                   'stat':{'usage_usec':100,'nr_throttled':2,'throttled_usec':10}}}
    qualified=config['profile']=='qualification'
    pre={'schema':'x6200.usb.host-resources.v1','phase':'preflight','profile':config['profile'],
         'configuration':config,'status':'PASS' if qualified else 'RECORDED_NONPROMOTABLE','assessment':'PASS',
         'promotion_eligible':qualified,'min_headroom_bytes':config['min_headroom_bytes'],
         'min_end_headroom_bytes':config['min_end_headroom_bytes'],'pressure_counter_policy':list(PRESSURE_COUNTERS),
         'before':snapshot(10*1024**3),'after':snapshot(6*1024**3),'headroom_bytes':10*1024**3,
         'reclaim':{'status':'COMPLETE' if config['reclaim_roots'] else 'NOT_REQUESTED',
                    'roots':config['reclaim_roots'],'rejected_roots':[],'timed_out':False,'contents_modified':False,
                    'worker_still_running':False},'reasons':[]}
    end={'schema':'x6200.usb.host-resources.v1','phase':'end','profile':config['profile'],'configuration':config,
         'status':'PASS' if qualified else 'RECORDED_NONPROMOTABLE','assessment':'PASS','promotion_eligible':qualified,
         'min_headroom_bytes':config['min_end_headroom_bytes'],'before':pre['after'],'after':snapshot(7*1024**3),
         'headroom_bytes':9*1024**3,'counter_deltas':{key:0 for key in PRESSURE_COUNTERS},
         'new_memory_pressure':{},'reasons':[],'cpu_throttling_is_diagnostic_only':True}
    pp=root/'host-resource-preflight.json';ep=root/'host-resource-end.json';atomic_json(pp,pre);atomic_json(ep,end)
    return {'schema':'x6200.usb.host-resource-contract.v1','status':'PASS' if qualified else 'RECORDED_NONPROMOTABLE',
            'configuration':config,'preflight':pre,'end':end,'preflight_report_path':str(pp),'preflight_report_sha256':sha(pp),
            'end_report_path':str(ep),'end_report_sha256':sha(ep),
            **{key:identity[key] for key in ('source_commit','source_tree_sha256','build_id','test_binary_sha256')}}


def infrastructure_fixture(root, identity, injector_sha):
    """Synthetic metadata for gate unit negatives; never actual C evidence."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    shared={'radio_accessed':False,'source_commit':identity['source_commit'],'build_id':identity['build_id'],
            'controller_binary_sha256':identity['test_binary_sha256'],'runner_sha256':identity['runner_sha256'],
            'fixture_sha256':{'speech':identity['speech_sha256'],'modem':identity['modem_sha256']},
            'fault_injector_sha256':injector_sha}
    variants={}
    for name,case,code,origin,disabled in (('initial_buffer','normal',13,'modem-buffer-budget',False),
                                          ('qualification_telemetry','rf-disabled',4,'radio-packet-stale',True)):
        directory=root/name;directory.mkdir()
        bodies={
            'case_result':{'status':'FAIL','case':case,'controller_exit_code':2},
            'harness_failure':{'status':'FAIL','exit_code':2,'primary_fault':code,'primary_origin':origin,
                               'cleanup_requested':True,'stopped':True,'closed':True,'rf_disabled':disabled,'keys':0},
            'completion':{'terminal':True,'fault':code,'trace_complete':True,'rf_clear_confirmed':True,
                          'rf_disabled':disabled,'qualification_complete':False},
            'collector_report':{'status':'BLOCKED','ready_for_rf_test':False},
            'first_fault':{'first_fault':{'code':code,'origin':origin}},
        }
        rows=[{'kind':'sample','state':5,'fault':code}]
        q=None
        if disabled:
            q={'qualification_started_ms':1000,'freeze_started_ms':1100,'packet_ms':1090,'sequence':7,
               'freeze_duration_ms':350,'fault_ms':1341,'recovered_ms':1450,'qualification_complete':False,'keys':0}
            bodies['injection_witness']={'kind':'test-telemetry-freeze','qualification_running':True,'keys':0,
                'duration_ms':350,'ms':1100,'packet_ms':1090,'sequence':7}
            rows=[{'kind':'event','event':'qualification-start','ms':1000},
                  {'kind':'sample','qualification_running':True,'rf_disabled':True,'ms':1100},
                  {'kind':'event','event':'first-fault','status':4,'operation':origin,'ms':1341}]
        files={}
        for filename,body in bodies.items():
            path=directory/(filename+'.json');atomic_json(path,body)
            files[filename]={'path':str(path),'sha256':sha(path)}
        trace=directory/'trace.jsonl';trace.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        files['trace']={'path':str(trace),'sha256':sha(trace)}
        variant={'schema':'x6200.usb.infrastructure.v1','kind':'unexpected-runtime-failure','status':'PASS',
                 **shared,'variant':name,'driver_case':case,'rf_disabled':disabled,'keys':0,
                 'checks':{key:True for key in INFRASTRUCTURE_CHECKS},'driver_exit_code':1,'driver_status':'FAIL',
                 'harness_exit_code':2,'harness_status':'FAIL','terminal_fault_code':code,
                 'terminal_fault_origin':origin,'collector_status':'BLOCKED','evidence_files':files}
        if q:variant['qualification_failure']=q
        variants[name]=variant;atomic_json(directory/'variant-result.json',variant)
    protocol_root=root/'release-lock-protocol';protocol_root.mkdir()
    protocol_keys=('coordinator_blocked','release_posted_without_wait','observed_after_unlock')
    witness={'status':'PASS','source_commit':identity['source_commit'],'build_id':identity['build_id'],
             'radio_accessed':False,**{key:True for key in protocol_keys}}
    protocol_log=protocol_root/'protocol.log';protocol_log.write_text('RELEASE_LOCK_PROTOCOL '+json.dumps(witness)+'\n')
    protocol={'schema':'x6200.usb.release-lock-protocol.v1','status':'PASS',
        **{key:shared[key] for key in ('source_commit','build_id','controller_binary_sha256','fault_injector_sha256','radio_accessed')},
        'execution':{'exit_code':0,'timed_out':False,'duration_ms':1},'witness':witness,
        'checks':{key:True for key in protocol_keys},'evidence_files':{'log':{'path':str(protocol_log),'sha256':sha(protocol_log)}}}
    proof={'schema':'x6200.usb.infrastructure.v2','kind':'unexpected-runtime-failures','status':'PASS',
           **shared,'checks':{key:True for key in INFRASTRUCTURE_CHECKS},'variants':variants,
           'release_lock_protocol':protocol}
    report=root/'infrastructure-result.json';atomic_json(report,proof)
    log=root/'infrastructure.log';log.write_text('Expected failed children preserved; unit fixture only\n')
    return {'status':'PASS','record':proof,'record_sha256':digest(proof),'report_path':str(report),
            'report_sha256':sha(report),'log_path':str(log),'log_sha256':sha(log),
            'supervision':{'exit_code':0,'timed_out':False}}


def capture_close_fixture(completed_rf_turns=0, extra_failed=False):
    """Synthetic finite-capture metadata for the release-gate unit tests."""
    rows=[]
    count=completed_rf_turns+int(extra_failed)
    for number in range(1,count+1):
        start=1000+(number-1)*144000;cutoff=start+96000;epoch=(number-1)*5000
        rows.append({'status':'PASS','turn':number,'trace_file':'freedv-usb-session-unit.jsonl',
            'rf_completed':number<=completed_rf_turns,'start_index':start,'cutoff_index':cutoff,
            'consumed_index':cutoff,'accepted_frames':96000,'mic_tap_start':(number-1)*96000,'mic_tap_end':number*96000,
            'close_elapsed_ms':150,'write_index_bytes':(cutoff-96)*2,'source_usec':1000,'transport_usec':1000,
            'sink_usec':10000,'frontier_extra_frames':96,'release_fence_index':cutoff-100,'tail_frames':100,
            'cutoff_split_frames':200,'capture_started_ms':1000+epoch,'close_started_ms':3000+epoch,'timing_issued_ms':3000+epoch,
            'frozen_ms':3001+epoch,'reached_ms':3100+epoch,'closed_ms':3150+epoch,'frontier_basis':'Pulse capture production; no monitor playback correction',
            'physical_latency':'UNMEASURED','microphone_frame_budget':{'schema':'x6200.usb.capture-budget.v2',
                'comparison_basis':'minimum_on_total_maximum_on_pre_fence','pre_fence_frames':95900,'pipeline_tail_frames':100,
                'status':'PASS','stream':'microphone',
                'active_ms':2000,'accepted_frames':96000,'expected_frames':96000.,'minimum_frames':93120,
                'maximum_frames':98880,'fragment_frames':960,'boundary_frames':1920,
                'delivery_tolerance_percent':1,'evaluated':True}})
    return {'status':'PASS','deadline_ms':750,'frontier_basis':'Pulse capture production; no monitor playback correction',
            'physical_latency':'UNMEASURED','turns':rows,'completed_rf_turns':completed_rf_turns,
            'completed_intervals':len(rows),'abandoned_intervals':0}


def capture_source_boundary_fixture(case,root):
    root=Path(root);result=root/'result';result.mkdir(parents=True,exist_ok=True)
    close=capture_close_fixture(1);interval=close['turns'][0]
    accepted=array('h',((index%30000)-15000 for index in range(96000)))
    if sys.byteorder!='little':accepted.byteswap()
    accepted_pcm=accepted.tobytes();source_pcm=accepted_pcm+(30001).to_bytes(2,'little',signed=True)*200
    pcm=result/'mic-observer.pcm';pcm.write_bytes(source_pcm)
    rows=[];cursor=interval['start_index'];tap=0
    while tap<95040:
        rows.append({'valid':True,'index_start':cursor,'index_end':cursor+960,'tap_start':tap,'tap_frames':960})
        cursor+=960;tap+=960
    rows.append({'valid':True,'index_start':cursor,'index_end':interval['cutoff_index']+200,
                 'tap_start':tap,'tap_frames':1160})
    index=result/'mic-observer.jsonl';index.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (result/'freedv-usb-session-unit-mic.raw').write_bytes(accepted_pcm)
    mode='held150' if case.endswith('held150') else 'split'
    exercise={'hold_started_ms':2950 if mode=='held150' else 2800,
              'hold_ended_ms':3110 if mode=='held150' else 2950,
              'callback_started_ms':3110 if mode=='held150' else 2950,'barrier_started_ms':0 if mode=='held150' else 2999,
              'release_observed_ms':3000,'callback_ended_ms':3110 if mode=='held150' else 3001,
              'blocked_peeks':1 if mode=='held150' else 0,'cutoff_index':97000,'sentinel_frames':200,
              'release_posted_ms':0 if mode=='held150' else 2999,'pa_lock_attempt_ms':0 if mode=='held150' else 2999,
              'pa_lock_waiting':0,'coordinator_wait_required':0}
    boundary={'status':'PASS','turn':1,'compared_frames':96000,'accepted_frames':96000,
              'expected_source_sha256':hashlib.sha256(accepted_pcm).hexdigest(),
              'accepted_sha256':hashlib.sha256(accepted_pcm).hexdigest(),'post_cutoff_frames_observed':200,
              'split_block':{'start_index':96040,'end_index':97200,'cutoff_index':97000},
              'post_cutoff_excluded':True,'mode':mode,'exercise':exercise,'sentinel_value':30001,
              'post_cutoff_sentinel_frames':200,'post_cutoff_sentinel_excluded':True}
    return {'harness':{'turns':1,'total_turns':1,'eoo':1,'fault':1,'keys':1,'physical_tx':False,'rf_clear_confirmed':True},
            'capture_close':close,'capture_source_boundary':{'status':'PASS','case':case,'turns':[boundary],
                'observer_pcm_sha256':sha(pcm),'observer_index_sha256':sha(index),'physical_latency':'UNMEASURED'}}


def capture_close_failure_fixture(case,root):
    from usb_test_support import validate_capture_close_failure
    root=Path(root);result=root/'result';result.mkdir(parents=True,exist_ok=True)
    mode=case.removeprefix('capture-close-')
    fault,origin=(9,'modem-payload-underflow') if mode=='missing' else ((23,'microphone-close-frontier-invalid') if mode=='invalid' else (1,'local-stop'))
    fault_ms=1424 if mode=='missing' else 1002
    abandoned={'kind':'capture-close','turn':1,'complete':False,'close_timeout_ms':750,
               'close_started_ms':1000,'timing_issued_ms':1001,'frozen_ms':0,'cutoff_index':0,
               'scope':'pulse-capture-pipeline-frontier'}
    rows=[{'kind':'event','event':'capture-close-start','state':4,'ms':1000},
          {'kind':'event','event':'first-fault','state':4,'ms':fault_ms,'status':fault,'operation':origin,'stream':'modem'},
          {'kind':'sample','state':4,'ms':fault_ms,'modem_underflows':1},abandoned]
    text='INJECT MIC close-timing requested_ms=1001 mode='+mode+' phase=drain\n'
    text+='INJECT MIC close-timing mode='+mode+' real_ack_ms=1002 success=1 withheld='+str(int(mode=='missing'))+' phase=drain\n'
    if mode=='invalid':text+='INJECT MIC close-timing retrograde write_index_bytes=0 source_usec=0 transport_usec=0 phase=drain\n'
    if mode=='stop':text+='INJECT MIC close-timing stop-before-forward stop_ms=1002 callback_invoked=1 phase=drain\n'
    (root/'test.log').write_text(text)
    (result/'freedv-usb-session-unit.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    harness={'fault':fault,'first_fault_origin':origin,'turns':0,'total_turns':0,'eoo':0,'keys':1,
             'rf_clear_confirmed':True,'physical_tx':False}
    close=capture_close_fixture();close['abandoned_intervals']=1
    return {'harness':harness,'capture_close':close,
            'capture_close_failure':validate_capture_close_failure(rows,harness,text,mode)}


def sink_turn_fixture(close,require_speech=True,root=None):
    """Synthetic numeric/file proof for gate negatives; no controller runs."""
    turns=[];trace_rows={};trace_pcm={};tap_cursors={}
    root=Path(root) if root is not None else None
    for number,interval in enumerate((row for row in close['turns'] if row['rf_completed']),1):
        stats={'failures':0,'synchronized_blocks':8 if require_speech else 0,
               'synthesis_blocks':7 if require_speech else 0,'decoded_seconds':.84 if require_speech else 0.}
        turn={'status':'PASS','turn':number,'trace_file':interval['trace_file'],
              'start_ms':interval['capture_started_ms'],'end_ms':interval['closed_ms']+500,
              'reference_boundary_basis':'exact_render_input_accounting',
              'microphone_frame_budget':copy.deepcopy(interval['microphone_frame_budget']),
              'reference':copy.deepcopy(stats),'observed':copy.deepcopy(stats),
              'speech_status':'DECODED_REFERENCE_MATCH' if require_speech else 'UNASSESSED_INSUFFICIENT_DURATION',
              'allowed_rounding_and_acquisition_seconds':.14,
              'content_match':{'status':'PASS','method':'full_modem_waveform_and_100ms_windows',
                   'complete_reference_covered':True,'correlation':.999,'lag_ms':600.,'fitted_gain':.25,
                   'expected_gain':.25,'minimum_correlation':.95,'maximum_lag_ms':1000,
                   'resampler_edge_allowance_ms':5,'maximum_window_normalized_error':.01,
                   'allowed_window_normalized_error':.35,'reference_frames_compared_at_8k':3900}}
        turn['alignment_budget']={'schema':'x6200.usb.alignment-budget.v1',
            'basis':'actual_output_queues_and_existing_scheduler_crop_budgets','stream':'modem','stream_target_ms':450.,
            'backend_usec':50000,'io_service_ms':150,'codec_quantum_ms':120,'margin_ms':30,'crop_context_ms':200,
            'maximum_lag_ms':1000}
        calls=[{'stage':'regular','input_frames':4000,'generated_frames':3675} for _ in range(6)]
        calls.append({'stage':'src-final','input_frames':0,'generated_frames':0})
        artifacts={};bodies={};directory=(root or Path('/synthetic'))/('turn-%03d-control'%number)
        for name,frames in (('source48',24000),('transport441',22050),('reference_stimulus',88200),
                            ('actual_stimulus',88200),('reference_decoded',37044 if require_speech else 0),
                            ('actual_decoded',37044 if require_speech else 0)):
            if name=='source48':raw=(1000).to_bytes(2,'little',signed=True)*frames
            elif name=='transport441':raw=(250).to_bytes(2,'little',signed=True)*frames
            elif name in ('reference_stimulus','actual_stimulus'):
                raw=b'\0\0'*2205+(250).to_bytes(2,'little',signed=True)*22050+b'\0\0'*(frames-24255)
            else:raw=b'\0\0'*frames
            bodies[name]=raw
            artifacts[name]={'path':str(directory/(name+'.raw')),'sha256':hashlib.sha256(raw).hexdigest()}
        for side,key in (('reference','reference'),('actual','observed')):
            stats=turn[key]
            stats.update(input_rate=44100,input_channels=1,input_frames=88200,unpadded_frames=26460,
                         input_sha256=artifacts[side+'_stimulus']['sha256'],
                         decoded_frames=37044 if require_speech else 0,decoded_sha256=artifacts[side+'_decoded']['sha256'])
            raw=(json.dumps(stats,sort_keys=True,indent=2)+'\n').encode();bodies[side+'_stats']=raw
            artifacts[side+'_stats']={'path':str(directory/(side+'-stats.json')),'sha256':hashlib.sha256(raw).hexdigest()}
        source_start=tap_cursors.get(interval['trace_file'],0);tap_cursors[interval['trace_file']]=source_start+24000
        turn.update(source_tap_start=source_start,source_tap_end=source_start+24000,artifacts=artifacts)
        matched=copy.deepcopy(turn['content_match']);matched.update(expected_gain=1,fitted_gain=1.)
        library_path=(root or Path('/synthetic'))/'transport-library-fixture'
        if root is not None:library_path.parent.mkdir(parents=True,exist_ok=True);library_path.write_bytes(SYNTHETIC_TRANSPORT_LIBRARY)
        turn['decoder_control']={'schema':'x6200.usb.decoder-control.v1','status':'PASS',
            'basis':'independent_transport_reference','source_rate':48000,'rate':44100,'channels':1,
            'gain_divisor':4,'leading_frames':2205,'control_frames':26460,'source_frames':24000,'transport_frames':22050,
            'source_sha256':artifacts['source48']['sha256'],'transport_sha256':artifacts['transport441']['sha256'],
            'reference_stimulus_sha256':artifacts['reference_stimulus']['sha256'],
            'actual_stimulus_sha256':artifacts['actual_stimulus']['sha256'],
            'source_context':'fixed_full_waveform_alignment','alignment_budget':copy.deepcopy(turn['alignment_budget']),'native_alignment':{'offset_frames':2205,
                'search_radius_frames':8,'normalized_rms_error':.01,'complete_transport_covered':True},
            'transport':{'converter':2,'library':'synthetic-libsamplerate','version':'synthetic',
                'input_frames':24000,'output_frames':22050,'calls':calls,'eof_complete':True,
                'library_path':str(library_path),'library_sha256':SYNTHETIC_TRANSPORT_SHA},'matched_waveform':matched}
        if root is not None:
            directory.mkdir(parents=True,exist_ok=True)
            for name,raw in bodies.items():Path(artifacts[name]['path']).write_bytes(raw)
            trace_rows.setdefault(interval['trace_file'],[{'kind':'stream','stream':'mic','rate':48000,'channels':1,'fragsize':1920},
                {'kind':'stream','stream':'modem','ms':0,'rate':44100,'channels':1,'tlength':39690,'reported_sink_usec':50000,'configured_sink_usec':50000}]).extend(
                dict(row,kind='render-queue',ms=turn['start_ms']+100+index) for index,row in enumerate(calls))
            trace_rows[interval['trace_file']].append(dict(interval,kind='capture-close',complete=True,index_gap=False,
                close_timeout_ms=750,scope='pulse-capture-pipeline-frontier',submitted_first_index=interval['start_index'],
                submitted_end_index=interval['cutoff_index']))
            trace_pcm[interval['trace_file']]=trace_pcm.get(interval['trace_file'],b'')+bodies['source48']
        turns.append(turn)
    if root is not None:
        result=root/'result';result.mkdir(exist_ok=True)
        for name,rows in trace_rows.items():
            (result/name).write_text(''.join(json.dumps(row)+'\n' for row in rows))
            (result/(Path(name).stem+'-modem.raw')).write_bytes(trace_pcm[name])
            microphone=result/(Path(name).stem+'-mic.raw')
            count=max(row['mic_tap_end'] for row in rows if row.get('kind')=='capture-close')
            if not microphone.exists():microphone.write_bytes(b'\0\0'*count)
    helper={'harness':{'turns':len(turns),'total_turns':len(turns),'fault':1},'capture_close':close,'actual_sink_turns':turns}
    if root is not None:output_contract_fixture(root,helper['harness'])
    attach_rf_windows_fixture(helper,root)
    return turns


def capture_pipeline_tail_fixture(root):
    """Synthetic actual-index/timing receipts, never live audio evidence."""
    from usb_test_support import validate_capture_pipeline_tail
    root=Path(root);result=root/'result';result.mkdir(parents=True,exist_ok=True)
    close=capture_close_fixture(1);row=close['turns'][0]
    prefix,tail=95900,5760;accepted_frames=prefix+tail;fence=row['start_index']+prefix;cutoff=fence+tail
    row.update(release_fence_index=fence,tail_frames=tail,cutoff_index=cutoff,consumed_index=cutoff,
               accepted_frames=accepted_frames,mic_tap_end=accepted_frames,source_usec=120000,transport_usec=0,
               frontier_extra_frames=tail,write_index_bytes=fence*2)
    row['microphone_frame_budget'].update(accepted_frames=accepted_frames,pre_fence_frames=prefix,pipeline_tail_frames=tail)
    accepted=array('h',((index%30000)-15000 for index in range(accepted_frames)))
    if sys.byteorder!='little':accepted.byteswap()
    microphone=accepted.tobytes();source=microphone+(30001).to_bytes(2,'little',signed=True)*200
    (result/'freedv-usb-session-unit-mic.raw').write_bytes(microphone)
    pcm=result/'mic-observer.pcm';pcm.write_bytes(source)
    source_rows=[];cursor=0
    while cursor<len(source)//2:
        count=min(960,len(source)//2-cursor)
        source_rows.append({'valid':True,'index_start':row['start_index']+cursor,'index_end':row['start_index']+cursor+count,
                            'tap_start':cursor,'tap_frames':count});cursor+=count
    index=result/'mic-observer.jsonl';index.write_text(''.join(json.dumps(value)+'\n' for value in source_rows))
    turns=sink_turn_fixture(close,True,root)
    rows=[json.loads(line) for line in (result/'freedv-usb-session-unit.jsonl').read_text().splitlines()]
    raw=next(value for value in rows if value.get('kind')=='capture-close')
    text='INJECT MIC close-timing requested_ms=3000 mode=tail120 phase=drain\n'
    text+='INJECT MIC close-timing mode=tail120 real_ack_ms=3001 success=1 withheld=0 phase=drain\n'
    text+='INJECT MIC close-timing tail120 real_source_usec=500 source_usec=120000 write_index_bytes='+str(fence*2)+' read_index_bytes='+str(fence*2)+' transport_usec=0 phase=drain pipeline_model=1\n'
    (root/'test.log').write_text(text)
    proof=validate_capture_pipeline_tail(raw,row,microphone,source_rows,source,text)
    proof.update(observer_pcm_sha256=sha(pcm),observer_index_sha256=sha(index))
    run={'harness':{'turns':1,'total_turns':1,'eoo':1,'total_eoo':1,'fault':1,'rf_clear_confirmed':True,
                    'rf_valid':True,'rf_tx':False,'physical_tx':False},
         'capture_close':close,'capture_pipeline_tail':proof,'actual_sink_turns':turns}
    attach_rf_windows_fixture(run,root)
    return run



def output_contract_fixture(root,harness,identity=None):
    """Hand-computed playback-tail receipts for metadata gate unit tests."""
    from usb_output_contract import validate_output_contract
    root=Path(root);result=root/'result';result.mkdir(parents=True,exist_ok=True)
    identity=identity or {'source_commit':'a'*40,'build_id':'synthetic-output-fixture','test_binary_sha256':'c'*64}
    paths=sorted(result.glob('freedv-usb-session*.jsonl'))
    if not paths:
        path=result/'freedv-usb-session-unit.jsonl';path.write_text('');paths=[path]
    sessions=[];session_ids=[]
    for path in paths:
        rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        rows=[row for row in rows if not row.get('_synthetic_output')]
        key={'session_id':path.stem,'source_commit':identity['source_commit'],'build_id':identity['build_id']}
        header={'kind':'session','schema':'x6200.usb-audio.v3','usb_output_contract_version':1,**key,'_synthetic_output':True}
        closes=[row for row in rows if row.get('kind')=='capture-close' and row.get('complete') is True]
        generated=[]
        for number,close in enumerate(closes,1):
            start,drain,closed=close['capture_started_ms'],close['close_started_ms'],close['closed_ms']
            ack_us=(closed+12)*1000;until=ack_us+33000+1;unkey=(until+999)//1000+1
            for state,at in ((3,start),(4,drain),(8,unkey)):
                generated.append({'kind':'event','event':'state','state':state,'ms':at,'_synthetic_output':True})
            generated.append({'kind':'backend-tail','schema':'x6200.usb.playback-tail.v1',**key,'turn':number,
                'drain_started_ms':drain,'protocol_ack_ms':closed+10,'timing_issued_ms':closed+11,
                'timing_issued_us':(closed+11)*1000,'timing_ack_ms':closed+12,'timing_ack_us':ack_us,
                'write_index_bytes':8820,'read_index_bytes':7938,'sink_usec':20000,'transport_usec':3000,
                'configured_sink_usec':20000,'protocol_pending_usec':10000,'wait_usec':33000,
                'wait_until_us':until,'completed_us':until,'complete':True,'rf_tx_confirmed':True,'rf_disabled':False,
                'scope':'pulse-reported-backend-tail','_synthetic_output':True})
        fault=harness.get('fault',1);state=6 if fault==1 else 5
        at=max([row.get('ms',0) for row in rows+generated]+[1000])+100
        final={'kind':'sample','ms':at,'turns':len(closes),'state':state,'fault':fault,'_synthetic_output':True}
        modem={'kind':'stream','ms':0,'stream':'modem','rate':44100,'channels':1,'tlength':39690,
               'reported_sink_usec':50000,'configured_sink_usec':50000,'_synthetic_output':True}
        extra=[{'kind':'stream','stream':'headset','ms':0,'rate':48000,'channels':1,'tlength':43200,
                'maxlength':96000,'minreq':1920,'prebuf':36482},
               {'kind':'stream','stream':'radio-rx','ms':0,'rate':44100,'channels':1,'fragsize':1764}]
        initial={'kind':'event','event':'state','state':1,'ms':min([item['capture_started_ms'] for item in closes]+[1000])-100,'_synthetic_output':True}
        trace_rows=[header,modem,initial,*extra,*rows,*generated,final]
        for item in trace_rows:
            if item.get('kind') in ('stream','audio-budget') and item.get('stream') in ('mic','radio-rx','headset','modem'):
                capture=item['stream'] in ('mic','radio-rx')
                item.update(requested_flags=0x2000 if capture else 0x44000 if item['stream']=='headset' else 0x4000,adjust_latency=capture,
                            request_domain='capture-fragments' if capture else 'per-stream-playback')
                if not capture:
                    rate=item['rate'];item.setdefault('maxlength',rate*2);item.setdefault('minreq',rate//25)
                    item.setdefault('prebuf',rate*2*380//1000)
        path.write_text(''.join(json.dumps(row)+'\n' for row in trace_rows))
        completion={'kind':'completion','schema':'x6200.usb-completion.v1',**key,'usb_output_contract_version':1,
            'terminal':True,'trace_complete':True,'turns':len(closes),'playback_tail_completed_intervals':len(closes),
            'playback_tail_interrupted_intervals':0,'state':state,'fault':fault,'qualification_turns':0,'last_sample_ms':at}
        cp=path.with_name(path.stem+'-completion.json');atomic_json(cp,completion)
        summary=validate_output_contract(trace_rows,completion,required_version=1,expected_identity=key)
        sessions.append(dict(summary,trace_file=path.name,completion_file=cp.name,trace_sha256=sha(path),completion_sha256=sha(cp)))
        session_ids.append(path.stem)
    return {'usb_output_contract_version':1,'identity':{'status':'PASS','source_commit':identity['source_commit'],
            'build_id':identity['build_id'],'binary_sha256':identity['test_binary_sha256'],'session_ids':session_ids},
            'output_contract':{'schema':'x6200.usb.output-contract-set.v1','status':'PASS','usb_output_contract_version':1,'sessions':sessions}}



def rf_intervals_from_turns(turns):
    return [dict(key_sequence=turn['rf_window']['rf_key_sequence'],**{key:turn['rf_window']['rf_'+key] for key in
            ('on_before_ns','on_after_ns','off_before_ns','off_after_ns')}) for turn in turns
            if turn.get('rf_window',{}).get('status')=='PASS']


def attach_rf_windows_fixture(run,root=None):
    from usb_test_support import fifo_rf_window
    from test_usb_audio_integration import rf_window_proof
    turns=run.get('actual_sink_turns',[]);intervals=[]
    for number,turn in enumerate(turns,1):
        start,end=turn['start_ms'],turn['end_ms']
        interval={'key_sequence':number,'on_before_ns':(start-2)*1000000,'on_after_ns':(start-1)*1000000,
                  'off_before_ns':(end-10)*1000000,'off_after_ns':(end-9)*1000000}
        intervals.append(interval);turn['monitor_frame_start']=0
        turn['rf_window']={'schema':'x6200.usb.rf-window.v1','status':'PASS',
            'scope':'pulse-reported backend model; not physical hardware','basis':'fresh_backend_tail_before_RF_off',
            'receipt_turn':[row for row in run['capture_close']['turns'] if row['rf_completed']][number-1]['turn'],
            'receipt_sha256':'e'*64,'backend_completed_ns':(end-20)*1000000,'rf_key_sequence':number,
            **{'rf_'+key:interval[key] for key in ('on_before_ns','on_after_ns','off_before_ns','off_after_ns')},
            'complete_reference_covered':True,'all_signal_consumed_while_rf':True,'finite_deadline_before_rf_off':True}
    run['harness']['rf_intervals']=intervals
    if root is None:return
    root=Path(root);fixed=run.get('backend_profile',{}).get('name')=='fixed-pipe'
    if fixed and turns:
        turn=turns[0];data=Path(turn['artifacts']['actual_stimulus']['path']).read_bytes()[:turn['decoder_control']['control_frames']*2]
        frames=len(data)//2;origin=intervals[0]['on_after_ns']
        header={'kind':'session','schema':'x6200.usb.fifo-clock.v1','scope':'paced FIFO backend model; not physical hardware',
            'rate_hz':44100,'channels':2,'sample_width_bytes':2,'frame_bytes':4,'period_ms':10,'clock_origin_ns':origin}
        clock=[header]
        for number,start in enumerate(range(0,frames,441)):
            end=min(start+441,frames);deadline=origin+(number+1)*10000000
            clock.append({'kind':'block','block':number,'period':number+1,'frame_start':start,'frame_end':end,
                'bytes_read':(end-start)*4,'partial_frame_bytes':0,'before_ns':deadline,'after_ns':deadline+1000})
        clock.append({'kind':'terminal','status':'STOPPED','exit_code':0,'partial_frame_bytes':0,'frames':frames,
            'raw_bytes':frames*4,'blocks':len(clock)-1,'clock_origin_ns':origin,'finished_ns':clock[-1]['after_ns']+1000})
        (root/'fifo-modem.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in clock))
        (root/'fifo-modem-mono.raw').write_bytes(data)
        (root/'fifo-modem.raw').write_bytes(b''.join(data[index:index+2]*2 for index in range(0,len(data),2)))
    for turn in turns:
        path=root/'result'/turn['trace_file'];rows=[json.loads(line) for line in path.read_text().splitlines()]
        transport=Path(turn['artifacts']['transport441']['path']).read_bytes()
        turn['rf_window']=rf_window_proof(root,rows,turn,turn['rf_window']['receipt_turn'],transport,
            turn['decoder_control'],turn['monitor_frame_start'],run['harness'],fifo=fixed)



def backend_failure_fixture(case,root,identity=None):
    from usb_output_contract import validate_output_contract
    from usb_test_support import validate_backend_failure
    root=Path(root);run=capture_case_fixture('normal',root,identity)
    mode,fault,origin={'backend-timing-missing':('missing',14,'modem-backend-timing'),
        'backend-timing-invalid':('invalid',9,'modem-backend-timing-invalid'),
        'backend-stop-before-timing':('stop-before-timing',1,'local-stop'),
        'backend-stop-during-wait':('stop-during-wait',1,'local-stop')}[case]
    interval=run['capture_close']['turns'][0];interval['rf_completed']=False;run['capture_close']['completed_rf_turns']=0
    run['actual_sink_turns']=[]
    harness=run['harness'];harness.update(status='PASS',scenario='--'+case,turns=0,total_turns=0,eoo=1,total_eoo=1,
        fault=fault,first_fault_origin=origin,first_fault_operation_ms=1000 if mode=='missing' else 0,
        keys=1,physical_tx=False,rf_valid=True,rf_tx=False,rf_clear_confirmed=True)
    entry=run['output_contract']['sessions'][0];path=root/'result'/entry['trace_file'];cp=root/'result'/entry['completion_file']
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    rows=[row for row in rows if not(row.get('event')=='state' and row.get('state')==8)]
    receipt=next(row for row in rows if row.get('kind')=='backend-tail');issued=receipt['timing_issued_ms']
    request=issued+1 if mode=='missing' else issued;ack=request+1
    fault_ms=issued+1000 if mode=='missing' else ack+1
    receipt.update(complete=False,rf_tx_confirmed=False,completed_us=0)
    if mode in ('missing','stop-before-timing'):
        receipt.update(timing_ack_ms=0,timing_ack_us=0,wait_usec=0,wait_until_us=0)
    if mode=='invalid':receipt['write_index_bytes']=-1
    text='INJECT backend timing requested_ms='+str(request)+' mode='+mode+' phase=drain\n'
    text+='INJECT backend timing mode='+mode+' real_ack_ms='+str(ack)+' success=1 withheld='+str(int(mode=='missing'))+' phase=drain\n'
    if mode=='invalid':text+='INJECT backend timing invalid write_index_bytes=-1 phase=drain\n'
    if mode=='stop-before-timing':text+='INJECT backend timing stop-before-forward stop_ms='+str(fault_ms)+' action_invoked=1 phase=drain\n'
    if mode=='stop-during-wait':
        before=receipt['timing_ack_us']*1000+100000
        text+='BACKEND_STOP_DURING_WAIT request_before_ns='+str(before)+' request_after_ns='+str(before+1000)+' wait_started_ms='+str(receipt['timing_ack_ms'])+' wait_until_us='+str(receipt['wait_until_us'])+'\n'
    first={'kind':'event','event':'first-fault','state':4,'status':fault,'operation':origin,'ms':fault_ms,
           'duration_ms':1000 if mode=='missing' else 0}
    rows.insert(-1,first);rows[-1].update(turns=0,rf_valid=True,rf_tx=False,state=6 if fault==1 else 5,fault=fault,ms=fault_ms+10)
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    completion=json.loads(cp.read_text());completion.update(turns=0,playback_tail_completed_intervals=0,
        playback_tail_interrupted_intervals=1,state=rows[-1]['state'],fault=fault,last_sample_ms=rows[-1]['ms']);atomic_json(cp,completion)
    summary=validate_output_contract(rows,completion,required_version=1)
    run['output_contract']['sessions']=[dict(summary,trace_file=path.name,completion_file=cp.name,trace_sha256=sha(path),completion_sha256=sha(cp))]
    if fault==1:interval['completion_interruption']={'schema':'x6200.usb.capture-interruption.v1','status':'CONFIRMED',
        'fault':1,'origin':'local-stop','state':4,'ms':fault_ms,'closed_ms':interval['closed_ms'],'trace_file':path.name,'trace_sha256':sha(path)}
    harness['rf_intervals'][0].update(off_before_ns=(fault_ms+1)*1000000,off_after_ns=(fault_ms+2)*1000000)
    (root/'test.log').write_text(text)
    run['backend_failure']=validate_backend_failure(rows,harness,text,mode)
    run['backend_profile'].update(name='legacy-low-latency',null_norewinds=True)
    (root/'default.pa').write_text('load-module module-null-sink sink_name=radio_sink_fixture norewinds=1\n')
    return run


def audio_controls_fixture(live=False,root=None):
    """Synthetic gate-unit control receipts; no audio or controller executes."""
    if root:Path(root).mkdir(parents=True,exist_ok=True)
    initial={'alsa_output.usb-fixture':{'mute':True,'volume':[26214,19661]},
             'radio_sink_fixture':{'mute':False,'volume':[65536,65536]},
             'unrelated_sink':{'mute':False,'volume':[15073,12452]}}
    names=('gain-turn-up','gain-turn-down','gain-turn-rx') if live else ('baseline','up','down','gain6','gain24','gainminus12','gain0')
    gains=(6,-6,0) if live else (0,0,0,6,24,-12,0)
    volumes=(43,40,40) if live else (40,43,40,40,40,40,40)
    phases=[]
    for index,(name,gain,volume) in enumerate(zip(names,gains,volumes)):
        tx=live and index<2;mute=not live and index==0
        snapshot={'phase':name,'monotonic_ms':1000+index*100,'state':3 if tx else 1,'generation':2 if live and index==2 else 0,
            'armed':live,'rf_tx':tx,'keys':(2 if tx else 3) if live else 0,'turns':2 if live and index==2 else 0,
            'submitted_mic':(index+1)*48000 if live else 0,'mic_clipped_samples':0,
            'mic_meter_ready':True,'mic_gain_db':gain,'mic_input_rms_dbfs10':-150,'mic_input_peak_dbfs10':-120,
            'mic_rms_dbfs10':-10 if gain==24 else -150+gain*10,'mic_peak_dbfs10':0 if gain==24 else -120+gain*10,
            'headset_volume_ready':True,'headset_volume_percent':volume,'headset_muted':mute,
            'rade_creations':1,'encoder_creations':1,'decoder_creations':1,'resampler_creations':4,'worker_creations':2}
        pulse=copy.deepcopy(initial);pulse['alsa_output.usb-fixture']={'mute':mute,'volume':[int(volume*65536/100),round(volume*.75*65536/100)]}
        entry={'snapshot':snapshot,'pulse':pulse}
        if root:
            for kind in ('snapshot','pulse'):
                path=Path(root)/(name+'-'+kind+'.json');atomic_json(path,entry[kind])
                entry[kind+'_path']=str(path);entry[kind+'_sha256']=sha(path)
        phases.append(entry)
    key='audio_gain_turn' if live else 'audio_controls'
    return {'harness':{'keys':3 if live else 0,'turns':2 if live else 0,'total_turns':2 if live else 0,
                      'eoo':2 if live else 0,'fault':1,'physical_tx':False,'rf_clear_confirmed':True},
            key:{'schema':'x6200.usb.audio-gain-turn.v1' if live else 'x6200.usb.audio-controls.v1',
                 'status':'PASS','initial_sinks':initial,'phases':phases}}


def capture_case_fixture(case,root,identity=None):
    from test_usb_audio_integration import SPEECH_CASES,WAVEFORM_CASES
    if case in ('backend-timing-missing','backend-timing-invalid','backend-stop-before-timing','backend-stop-during-wait'):
        return backend_failure_fixture(case,root,identity)
    if case=='capture-close-tail120':result=capture_pipeline_tail_fixture(root)
    elif case in ('capture-release-split','capture-release-held150'):result=capture_source_boundary_fixture(case,root)
    elif case in ('capture-close-missing','capture-close-invalid','capture-close-stop'):result=capture_close_failure_fixture(case,root)
    elif case=='eoo-backpressure':result=tail_backpressure_fixture()
    elif case in ('audio-controls','audio-gain-turn'):
        result=audio_controls_fixture(case=='audio-gain-turn',root)
        result['capture_close']=capture_close_fixture(result['harness']['turns'])
    else:
        count=int(case in WAVEFORM_CASES)
        result={'harness':{'turns':count,'total_turns':count,'eoo':count,'fault':1},'capture_close':capture_close_fixture(count)}
    if case.startswith('operational-'):
        count=int(case=='operational-leave-tx');fault=10 if case=='operational-timeout' else 2 if case=='operational-device-loss' else 1 if case=='operational-abort' else 0
        result['harness'].update(turns=count,total_turns=count,eoo=count,fault=fault,operational=True,
            startup_ready=True,qualification_complete=False,qualification_turns=0,tx_timeout_seconds=30 if case=='operational-timeout' else 180,
            physical_tx=False,rf_clear_confirmed=True,configuration_ready=True,tuning_revision=2 if case in ('operational-retune','operational-receive-only') else 1,
            physical_ptt_pressed=case in ('operational-leave-tx','operational-abort','operational-device-loss','operational-timeout'),
            physical_release_sequence=1 if case in ('operational-held-ptt','operational-retune','operational-receive-only') else 0,
            rade_creations=1,encoder_creations=1,decoder_creations=1,rx_synthesis_blocks=4 if case=='operational-retune' else 0)
        result['capture_close']=capture_close_fixture(count)
        if count:sink_turn_fixture(result['capture_close'],False,root) # Produce raw finite-close fixture records.
    if case in WAVEFORM_CASES:
        result['actual_sink_turns']=sink_turn_fixture(result['capture_close'],case in SPEECH_CASES,root)
    else:result['actual_sink_turns']=[]  # Match the matrix summary's explicit default.
    profile_name={'backend-fixed-pipe':'fixed-pipe','io-pause150':'fixed-pipe','rx-pause150':'default'}.get(case,'legacy-low-latency')
    module='module-pipe-sink' if profile_name=='fixed-pipe' else 'module-null-sink'
    result['backend_profile']={'schema':'x6200.usb.backend-profile.v1','name':profile_name,'passive_monitor':case!='cold-idle','observer_excluded_for_idle':case=='cold-idle',
        'competing_monitor':profile_name=='monitor-competition','modem_sink_module':module,'null_norewinds':profile_name=='legacy-low-latency'}
    Path(root).mkdir(parents=True,exist_ok=True)
    (Path(root)/'default.pa').write_text('load-module '+module+' sink_name=radio_sink_fixture'+(' norewinds=1' if profile_name=='legacy-low-latency' else '')+'\n')
    result.update(output_contract_fixture(root,{**result['harness'],'fault':1} if case.startswith('operational-') else result['harness'],identity))
    attach_rf_windows_fixture(result,root)
    if case.startswith('operational-'):operational_case_fixture(case,root,result)
    starts=[turn['start_ms'] for turn in result.get('actual_sink_turns',[])]
    ready=(min(starts+[1000])-10)*1000000;fixed=profile_name=='fixed-pipe'
    result['observer_admission']={'schema':'x6200.usb.observer-admission.v1','phase':'after-initial-RX-before-PTT',
        'ready_ns':ready,'first_block_required':not fixed,'first_block_confirmed':not fixed,
        'independent_fifo_reader':fixed,'cold_idle_unobserved':case=='cold-idle'}
    (Path(root)/'result/output-observer-ready').write_text(str(ready)+'\n')
    with (Path(root)/'test.log').open('a') as log:log.write('FIXTURE_OUTPUT_READY\n')
    ledger=[{'kind':'ready','monotonic_ns':ready-2,'flags':0,'adjust_latency':False,'early_requests':False,
             'scope':'passive rendered sink monitor; not physical DAC time'},
            {'kind':'block','received_ns':ready-1,'frame_start':0,'frames':1}]
    (Path(root)/('headset-output.jsonl' if case=='rx-pause150' else 'radio-output.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in ledger))
    return result


def operational_case_fixture(case,root,result):
    """Synthetic v4 records for metadata gate tests; never hardware evidence."""
    path=Path(root)/'result/freedv-usb-session-unit.jsonl';cp=path.with_name(path.stem+'-completion.json')
    rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    header=next(row for row in rows if row.get('kind')=='session');key={name:header[name] for name in ('session_id','source_commit','build_id')}
    header.update(schema='x6200.usb-audio.v4',usb_startup_contract_version=4,operational=True,qualification_target=0)
    if case=='operational-timeout':
        rows.extend([dict(kind='event',event='state',state=3,ms=1000),dict(kind='event',event='first-fault',state=3,ms=31000,status=10,operation='turn-deadline')])
        h=result['harness'];h['first_fault_origin']='turn-deadline';h['rf_intervals']=[dict(on_before_ns=1000000000,off_after_ns=31000000000)]
    ready=dict(kind='startup-readiness',schema='x6200.usb.operational-readiness.v1',status='PASS',ready_ms=100,
               generation=1,tuning_revision=1,radio_sequence=20,tx_eligible=case!='operational-receive-only',ptt_release_sequence=int(case=='operational-held-ptt'),
               ptt=False,rf_valid=True,rf_tx=False,configuration_ready=True,captures_fresh=True,buffers_ready=True,
               headset_id='usb-fixture',headset_name='USB fixture',source='usb-source',sink='usb-sink',**key)
    rows.insert(1,ready);h=result['harness'];completion=json.loads(cp.read_text())
    fields=dict(usb_startup_contract_version=4,state=5 if h['fault']>1 else 6,fault=h['fault'],rf_disabled=False,rf_valid=True,rf_tx=False,
                rf_clear_confirmed=True,operational=True,startup_ready=True,leaving=True,configuration_ready=True,tx_eligible=True,
                tuning_revision=h['tuning_revision'],tx_timeout_seconds=h['tx_timeout_seconds'],qualification_target=0,
                qualification_complete=False,qualification_turns=0)
    samples=[row for row in rows if row.get('kind')=='sample'];samples[-1].update(fields)
    for row in rows:
        if row.get('kind')=='capture-close':row.update(close_trigger='navigation',physical_release_sequence=0)
        if row.get('event')=='capture-release-observed':row['event']='capture-navigation-observed'
    if case=='operational-timeout':
        samples[-1]['ms']=31100;completion['last_sample_ms']=31100
    completion.update(fields,completion_reason='fault' if h['fault']>1 else 'stop' if h['fault'] else 'navigation',
                      finalized_ms=completion['last_sample_ms']+1,trace_rows=sum(row.get('kind') in ('event','sample') for row in rows),
                      events_dropped=0,trace_history_truncated=False,storage_errno=0)
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows));atomic_json(cp,completion)
    atomic_json(path.with_name(path.stem+'-readiness.json'),ready)
    from usb_output_contract import validate_output_contract
    summary=validate_output_contract(rows,completion)
    result['output_contract']['sessions']=[dict(summary,trace_file=path.name,completion_file=cp.name,trace_sha256=sha(path),completion_sha256=sha(cp))]
    result['operational']=operational_proof(rows,completion,h,case)
    if case=='operational-retune':result['actual_headset_output']=operational_headset_fixture(root,path)


def operational_headset_fixture(root,trace):
    """Numerical artifact fixture only; real RX is executed by the C harness."""
    from usb_test_support import modem_waveform_match
    root=Path(root);decoded=trace.with_name(trace.stem+'-decoded.raw');monitor=root/'headset-output.raw';ledger=root/'headset-output.jsonl'
    samples=array('h',((index*1237+index*index*31)%16001-8000 for index in range(48000)))
    if sys.byteorder!='little':samples.byteswap()
    raw=samples.tobytes();decoded.write_bytes(raw);observed=b'\0\0'*9600+raw+b'\0\0'*9600;monitor.write_bytes(observed)
    ledger.write_text(json.dumps(dict(kind='completion',error=0))+'\n')
    matched=modem_waveform_match(raw,48000,observed,48000,expected_gain=1.0,maximum_lag_ms=1000)
    return dict(status='PASS',scope='Synthetic numerical RX gate fixture; no controller executed',waveform=matched,
        capture_frame_budget=dict(status='PASS'),rendered_frames=len(raw)//2,observed_frames=len(observed)//2,
        monitor_frame_start=0,alignment_budget=dict(maximum_lag_ms=1000),
        artifacts={name:dict(path=str(path),sha256=sha(path)) for name,path in
            (('decoded',decoded),('monitor',monitor),('ledger',ledger),('trace',trace))})


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
             'turns':1,'total_turns':1,'eoo':1,'fault':1,'rf_clear_confirmed':True},
            'capture_close':capture_close_fixture(1),
            'actual_sink_turns':sink_turn_fixture(capture_close_fixture(1))}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identity = {'source_commit': 'a' * 40, 'source_tree_sha256': 'b' * 64,
                         'build_id': 'campaign-test', 'test_binary_sha256': 'c' * 64,
                         'runner_sha256': 'd' * 64, 'speech_sha256': 'e' * 64,
                         'modem_sha256': 'f' * 64, 'fault_injector_sha256': '1' * 64}
        self.identity['host_resource_configuration']=host_resource_configuration()
        self.identity['transport_library_sha256']=SYNTHETIC_TRANSPORT_SHA
        self.identity['usb_output_contract_version']=1
        self.plan = {'usb_output_contract_version':1,'profile': 'qualification', 'repetitions': 2,
                     'expected_cases': ['normal'], 'selected_cases': ['normal'],
                     'debug_subset': False, 'case_timeout_seconds': {'normal': 90},
                     'infrastructure_prerequisites':list(INFRASTRUCTURE_PREREQUISITES),
                     'host_resource_configuration':self.identity['host_resource_configuration']}
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
            case_root=evidence/('normal-case-'+str(repeat))
            case = case_root/'case-result.json'
            extra=capture_case_fixture('normal',case_root,self.identity)
            case.write_text(json.dumps({'status': 'PASS', 'repeat': repeat,**extra}) + '\n')
            runs.append({'status': 'PASS', 'repeat': repeat, 'log_path': str(log),
                         'log_sha256': sha(log), 'case_record_path': str(case),
                         'case_record_sha256': sha(case),**extra})
        result = {'usb_output_contract_version':1,'schema': MATRIX_SCHEMA, 'status': status, 'identity': self.identity,
                  'plan': self.plan, 'complete_matrix': status == 'PASS',
                  'expected_executions': ['normal@1', 'normal@2'],
                  'executed': ['normal@1', 'normal@2'], 'not_executed': [], 'skipped': [],
                  'cases': {'normal': {'status': status, 'runs': runs}},
                  'host_resources':resource_fixture(evidence,self.identity),
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

    def optional_sink_report(self,retained='ABSENT',summary=None):
        """Use the real matrix default for a case with no sink oracle."""
        name='stop-during-key'
        self.plan.update(expected_cases=[name],selected_cases=[name],case_timeout_seconds={name:90})
        campaign,evidence=self.begin();result=self.result(evidence)
        runs=result['cases'].pop('normal')['runs']
        for run in runs:
            case_root=evidence/('optional-'+str(run['repeat']))
            extra=capture_case_fixture(name,case_root,self.identity)
            run['case_record_path']=str(case_root/'case-result.json')
            details={'status':'PASS',**extra}
            if retained=='ABSENT':details.pop('actual_sink_turns',None)
            else:details['actual_sink_turns']=copy.deepcopy(retained)
            atomic_json(run['case_record_path'],details)
            run.update(extra,actual_sink_turns=[] if summary is None else copy.deepcopy(summary),
                       case_record_sha256=sha(run['case_record_path']))
        result['cases']={name:{'status':'PASS','runs':runs}}
        result['expected_executions']=result['executed']=[name+'@1',name+'@2']
        return self.finish(campaign,result)

    def test_optional_sink_absence_matches_only_the_matrix_empty_list_default(self):
        report=self.optional_sink_report()
        validate_campaign(report,['stop-during-key'])
        self.assertEqual(report['cases']['stop-during-key']['runs'][0]['actual_sink_turns'],[])
        self.assertNotIn('actual_sink_turns',json.loads(Path(report['cases']['stop-during-key']['runs'][0]['case_record_path']).read_text()))

    def test_explicit_optional_null_nonempty_and_stale_summary_are_not_normalized(self):
        for retained,summary in ((None,[]),([{'status':'PASS'}],[]),('ABSENT',[{'status':'PASS'}])):
            report=self.optional_sink_report(retained,summary)
            with self.subTest(retained=retained,summary=summary),self.assertRaisesRegex(ValueError,'retained per-turn'):
                validate_campaign(report,['stop-during-key'])

    def test_legacy_or_unknown_output_feature_cannot_qualify_a_new_image(self):
        for target,value in (('matrix',None),('plan',0),('identity',2),('matrix',True)):
            campaign,evidence=self.begin();result=self.result(evidence)
            if target=='matrix':result['usb_output_contract_version']=value
            else:result[target]=dict(result[target],usb_output_contract_version=value)
            report=self.finish(campaign,result)
            with self.subTest(target=target,value=value),self.assertRaisesRegex(ValueError,'output contract version1'):
                validate_campaign(report,['normal'],verify_files=False)

    def test_required_positive_sink_absence_still_blocks_retained_binding(self):
        campaign,evidence=self.begin();result=self.result(evidence)
        run=result['cases']['normal']['runs'][0]
        details=json.loads(Path(run['case_record_path']).read_text());details.pop('actual_sink_turns')
        atomic_json(run['case_record_path'],details)
        run.update(actual_sink_turns=[],case_record_sha256=sha(run['case_record_path']))
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'Required per-turn'):
            validate_campaign(report,['normal'])

    def test_run_summary_cannot_replace_different_retained_sink_proof(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        result['cases']['normal']['runs'][0]['actual_sink_turns'][0]['summary_only_claim']=True
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'retained per-turn'):
            validate_campaign(report,['normal'])

    def test_missing_infrastructure_proof_cannot_promote_functional_passes(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        result['infrastructure_prerequisites']={}
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'infrastructure'):
            validate_campaign(report,['normal'])

    def test_qualification_identity_must_pin_transport_dependency(self):
        campaign,evidence=self.begin();result=self.result(evidence)
        result['identity']=dict(result['identity']);result['identity'].pop('transport_library_sha256')
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'transport library dependency'):
            validate_campaign(report,['normal'],verify_files=False)

    def test_failed_child_proofs_without_release_protocol_cannot_promote(self):
        campaign,evidence=self.begin();result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        entry['record'].pop('release_lock_protocol');entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'Release lock protocol'):
            validate_campaign(report,['normal'],verify_files=False)

    def test_infrastructure_child_failure_cannot_be_relabelled_success(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        entry['record']['variants']['initial_buffer']['driver_exit_code']=0
        atomic_json(entry['report_path'],entry['record'])
        entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'functional test'):
            validate_campaign(report,['normal'],verify_files=False)

    def test_infrastructure_artifact_mutation_blocks_promotion(self):
        campaign,evidence=self.begin()
        report=self.finish(campaign,self.result(evidence))
        proof=report['infrastructure_prerequisites']['unexpected_runtime_failure']['record']
        Path(proof['variants']['initial_buffer']['evidence_files']['completion']['path']).write_text('{}\n')
        with self.assertRaisesRegex(ValueError,'child evidence changed'):
            validate_campaign(report,['normal'])

    def test_infrastructure_summary_cannot_contradict_retained_child(self):
        campaign,evidence=self.begin()
        result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        item=entry['record']['variants']['initial_buffer']['evidence_files']['case_result']
        atomic_json(item['path'],{'status':'PASS','controller_exit_code':0})
        item['sha256']=sha(item['path'])
        atomic_json(entry['report_path'],entry['record'])
        entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'contradict'):
            validate_campaign(report,['normal'])

    def test_both_startup_and_qualification_failure_paths_are_mandatory(self):
        campaign,evidence=self.begin();result=self.result(evidence)
        entry=result['infrastructure_prerequisites']['unexpected_runtime_failure']
        del entry['record']['variants']['qualification_telemetry']
        atomic_json(entry['report_path'],entry['record']);entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
        report=self.finish(campaign,result)
        with self.assertRaisesRegex(ValueError,'both real failure paths'):validate_campaign(report,['normal'])

    def test_qualification_failure_requires_actual_stale_epoch_and_recovery(self):
        for mutation in ('stale-age','recovery','keys'):
            campaign,evidence=self.begin();result=self.result(evidence)
            entry=result['infrastructure_prerequisites']['unexpected_runtime_failure'];variant=entry['record']['variants']['qualification_telemetry']
            if mutation=='stale-age':variant['qualification_failure']['fault_ms']=1200
            elif mutation=='recovery':variant['qualification_failure']['recovered_ms']=1200
            else:variant['keys']=1
            atomic_json(entry['report_path'],entry['record']);entry['report_sha256']=sha(entry['report_path']);entry['record_sha256']=digest(entry['record'])
            report=self.finish(campaign,result)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_campaign(report,['normal'],verify_files=False)

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
        self.malformed_sink = False
        self.missing_output_contract = False
        self.flags_only_output_contract = False
        self.infrastructure_fail = False
        self.infrastructure_fake_exit = False
        self.infrastructure_wrong_identity = False
        self.counter = 0
        self.resource_blocked=False
        self.resource_end_pressure=False

    def resources_start(self,**kwargs):
        config=host_resource_configuration(kwargs['profile'],kwargs['reclaim_roots'],kwargs['min_headroom_bytes'],kwargs['min_end_headroom_bytes'])
        identity={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'build_id':'test','test_binary_sha256':sha(self.binary)}
        self.resource_template=resource_fixture(Path(kwargs['output']).parent,identity,config)
        pre=self.resource_template['preflight']
        if self.resource_blocked:pre.update(status='BLOCKED',assessment='BLOCKED',promotion_eligible=False,reasons=['headroom low'])
        atomic_json(kwargs['output'],pre)
        return pre

    def resources_end(self,preflight,**kwargs):
        end=copy.deepcopy(self.resource_template['end']);end['before']=preflight['after']
        if self.resource_end_pressure:
            end['after']['memory']['events']['max']+=1
            end.update(status='BLOCKED',assessment='BLOCKED',promotion_eligible=False,reasons=['pressure'],new_memory_pressure={'memory.events.max':1})
            end['counter_deltas']['memory.events.max']=1
        atomic_json(kwargs['output'],end)
        return end

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
            if self.infrastructure_fake_exit: proof['variants']['initial_buffer']['driver_exit_code']=0
            if self.infrastructure_wrong_identity: proof['source_commit']='wrong-source'
            atomic_json(output/'infrastructure-result.json',proof)
            return {'exit_code':1 if self.infrastructure_fail else 0,'timed_out':False,'duration_ms':1}
        case = command[command.index('--case') + 1]
        output = Path(command[command.index('--output') + 1]); output.mkdir()
        if self.hang:
            return {'exit_code': -9, 'timed_out': True, 'duration_ms': 90000}
        details = {'status': 'FAIL' if self.fail else 'PASS', 'case': case,
                   'test_binary_sha256': sha(self.binary), 'harness': {'status':'PASS','turns':0,'total_turns':0,'fault':1},
                   'capture_close':capture_close_fixture(),
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
        details.update(capture_case_fixture(case,output,{'source_commit':expected_env['USB_TEST_EXPECTED_SOURCE_COMMIT'],
            'build_id':'older-build' if self.old_build else expected_env['USB_TEST_EXPECTED_BUILD_ID'],'test_binary_sha256':sha(self.binary)}))
        details['actual_sink_decode']={'synthesis_blocks':sum(turn['observed']['synthesis_blocks'] for turn in details.get('actual_sink_turns',[]))}
        if self.aggregate_only:
            details['actual_sink_decode']={'synthesis_blocks':20}
            details['actual_sink_turns']=[]
        if self.malformed_sink:details['actual_sink_turns'][0]['content_match']['complete_reference_covered']=False
        if self.missing_output_contract:details.pop('output_contract')
        if self.flags_only_output_contract:
            entry=details['output_contract']['sessions'][0]
            trace=output/'result'/entry['trace_file'];completion=output/'result'/entry['completion_file']
            rows=[json.loads(line) for line in trace.read_text().splitlines()]
            trace.write_text(''.join(json.dumps(row)+'\n' for row in rows if row.get('kind')!='backend-tail'))
            final=json.loads(completion.read_text());final['playback_tail_completed_intervals']=0;atomic_json(completion,final)
            entry.update(completed_intervals=0,receipt_count=0,trace_sha256=sha(trace),completion_sha256=sha(completion))
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
                mock.patch.object(matrix,'observed_transport_library',return_value={'library_sha256':SYNTHETIC_TRANSPORT_SHA}), \
                mock.patch.object(matrix,'run_resource_preflight',self.resources_start), \
                mock.patch.object(matrix,'observe_resource_end',self.resources_end), \
                mock.patch.object(matrix, 'source_snapshot', return_value=({}, 'b'*64)), \
                mock.patch.object(matrix.subprocess, 'check_output', return_value='a'*40+'\n'), \
                mock.patch.object(matrix, 'sha', stable_sha):
            status = matrix.main()
        return status, json.loads((output/'matrix-result.json').read_text())

    def test_resource_preflight_blocks_before_any_audio_prerequisite(self):
        self.resource_blocked=True
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertEqual(record['cases'],{})
        self.assertEqual(record['infrastructure_prerequisites'],{})
        self.assertFalse(record['promotion_eligible'])

    def test_resource_end_pressure_blocks_otherwise_passing_campaign(self):
        self.resource_end_pressure=True
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertEqual(record['status'],'FAIL')
        self.assertTrue(all(case['status']=='PASS' for case in record['cases'].values()))
        self.assertFalse(record['promotion_eligible'])

    def test_end_resource_failure_does_not_replace_original_controller_failure(self):
        self.fail=True;self.resource_end_pressure=True
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertIn('USB integration case failed',record['reason'])
        self.assertIn('host_resource_error',record)
        self.assertEqual(record['cases']['normal']['status'],'FAIL')

    def test_resource_counter_change_does_not_reset_same_configuration_history(self):
        self.resource_blocked=True
        self.invoke();self.resource_blocked=False
        status,record=self.invoke()
        self.assertEqual(status,1);self.assertEqual(record['campaign']['status'],'BLOCKED')

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

    def test_advertised_flags_without_completion_proof_fail_before_seal(self):
        for flag in ('missing_output_contract','flags_only_output_contract'):
            setattr(self,flag,True)
            status,record=self.invoke()
            self.assertEqual(status,1)
            self.assertEqual(record['status'],'FAIL')
            self.assertEqual(record['cases']['normal']['runs'][0]['status'],'FAIL')
            self.assertEqual(record['campaign']['history'][-1]['result']['status'],'FAIL')
            setattr(self,flag,False)

    def test_promotion_validation_failure_is_printed_and_retained(self):
        with mock.patch.object(matrix,'validate_campaign',side_effect=ValueError('sentinel promotion failure')), \
                mock.patch('builtins.print') as printed:
            status,record=self.invoke()
        self.assertEqual(status,1)
        self.assertEqual(record['promotion_error'],'sentinel promotion failure')
        self.assertFalse(record['promotion_eligible'])
        printed.assert_any_call('FAIL: USB promotion validation: sentinel promotion failure',flush=True)

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

    def test_partial_positive_sink_proof_is_sealed_as_failure_before_replay(self):
        self.malformed_sink=True
        status,record=self.invoke()
        self.assertEqual(status,1)
        self.assertEqual(record['status'],'FAIL')
        self.assertEqual(record['cases']['normal']['runs'][0]['status'],'FAIL')
        self.assertIn('waveform',record['cases']['normal']['runs'][0]['reason'])
        self.assertEqual(record['campaign']['history'][0]['result']['status'],'FAIL')
        self.malformed_sink=False
        status,replayed=self.invoke()
        self.assertEqual(status,1)
        self.assertEqual(replayed['campaign']['status'],'BLOCKED')

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


class PositiveTurnGateTests(unittest.TestCase):
    def fixture(self,case='normal',turns=2):
        close=capture_close_fixture(turns)
        run={'harness':{'turns':turns,'total_turns':turns,'eoo':turns,'total_eoo':turns,'fault':1},
             'capture_close':close,'actual_sink_turns':sink_turn_fixture(close,case!='short-turn200')}
        run['harness']['rf_intervals']=rf_intervals_from_turns(run['actual_sink_turns'])
        return run

    def test_every_positive_turn_and_short_waveform_pass(self):
        validate_sink_turns_run(self.fixture(),'normal',verify_files=False)
        validate_sink_turns_run(self.fixture('short-turn200',1),'short-turn200',verify_files=False)

    def test_aggregate_or_partial_turns_cannot_mask_missing_turn(self):
        for mutation in ('missing','partial','duplicate','zero','eoo'):
            run=self.fixture()
            if mutation=='missing':run.pop('actual_sink_turns')
            elif mutation=='partial':run['actual_sink_turns'].pop()
            elif mutation=='duplicate':run['actual_sink_turns'][1]=copy.deepcopy(run['actual_sink_turns'][0])
            elif mutation=='zero':run=self.fixture(turns=0)
            else:run['harness']['total_eoo']=1
            run['decoded_speech_blocks']=20
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)

    def test_waveform_status_alone_or_weakened_threshold_cannot_promote(self):
        mutations={'complete_reference_covered':False,'correlation':.9,'lag_ms':1001,'fitted_gain':.23,
                   'maximum_window_normalized_error':.36,'allowed_window_normalized_error':.5,
                   'minimum_correlation':.9,'resampler_edge_allowance_ms':6,'reference_frames_compared_at_8k':0,
                   'expected_gain':1.,'maximum_lag_ms':2000,'method':'partial_waveform'}
        for key,value in mutations.items():
            run=self.fixture();run['actual_sink_turns'][1]['content_match'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)
        run=self.fixture();run['actual_sink_turns'][1]['content_match']={'status':'PASS'}
        with self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)

    def test_a_previous_turn_artifact_or_source_interval_cannot_count_twice(self):
        for mutation in ('artifact','source'):
            run=self.fixture();first,second=run['actual_sink_turns']
            if mutation=='artifact':second['artifacts']['reference_decoded']['path']=first['artifacts']['reference_decoded']['path']
            else:
                second['source_tap_start']=first['source_tap_start'];second['source_tap_end']=first['source_tap_end']
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'reused'):
                validate_sink_turns_run(run,'normal',verify_files=False)

    def test_nan_or_missing_numeric_evidence_is_rejected(self):
        for value in (float('nan'),float('inf'),True,None):
            run=self.fixture();run['actual_sink_turns'][0]['content_match']['correlation']=value
            with self.subTest(value=value),self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)

    def test_each_turn_is_bound_to_its_capture_session_and_budget(self):
        for mutation in ('trace','budget','turn','timestamp','boundary'):
            run=self.fixture();turn=run['actual_sink_turns'][1]
            if mutation=='trace':turn['trace_file']='old-session.jsonl'
            elif mutation=='budget':turn['microphone_frame_budget']['accepted_frames']-=1
            elif mutation=='turn':turn['turn']=1
            elif mutation=='timestamp':turn['end_ms']=turn['start_ms']-1
            else:turn['reference_boundary_basis']='approximate'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)

    def test_speech_requires_synthesis_and_unchanged_duration_bounds(self):
        for mutation in ('missing','failed','no_sync','no_synthesis','blocks','duration','tolerance','unassessed'):
            run=self.fixture();turn=run['actual_sink_turns'][1]
            if mutation=='missing':turn.pop('observed')
            elif mutation=='failed':turn['observed']['failures']=1
            elif mutation=='no_sync':turn['observed']['synchronized_blocks']=0
            elif mutation=='no_synthesis':turn['observed']['synthesis_blocks']=0
            elif mutation=='blocks':turn['observed']['synthesis_blocks']-=2
            elif mutation=='duration':turn['observed']['decoded_seconds']-=.141
            elif mutation=='tolerance':turn['allowed_rounding_and_acquisition_seconds']=.25
            else:turn['speech_status']='UNASSESSED_INSUFFICIENT_DURATION'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)



class MatchedDecoderGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def fixture(self,files=False):
        run=capture_case_fixture('normal',self.root) if files else PositiveTurnGateTests().fixture(turns=1)
        run['case_record_path']=str(self.root/'case-result.json')
        return run

    def test_exact_same_domain_control_and_retained_artifacts_pass(self):
        validate_sink_turns_run(self.fixture(files=True),'normal')

    def test_control_status_cannot_replace_missing_model_or_eof(self):
        for mutation in ('missing','rate','gain','source_context','source_boundary','whole_transport','eof',
                         'input','output','final','leading','phase','matched_waveform','native_rms'):
            run=self.fixture();turn=run['actual_sink_turns'][0];control=turn['decoder_control']
            if mutation=='missing':turn.pop('decoder_control')
            elif mutation=='rate':control['rate']=48000
            elif mutation=='gain':control['gain_divisor']=1
            elif mutation=='source_context':control['source_context']='actual_sink_payload'
            elif mutation=='source_boundary':turn['source_tap_end']-=1
            elif mutation=='whole_transport':control['native_alignment']['complete_transport_covered']=False
            elif mutation=='eof':control['transport']['eof_complete']=False
            elif mutation=='input':control['transport']['calls'][0]['input_frames']-=1
            elif mutation=='output':control['transport']['calls'][0]['generated_frames']-=1
            elif mutation=='final':control['transport']['calls'].pop()
            elif mutation=='leading':control['control_frames']=control['leading_frames']+control['transport_frames']-1
            elif mutation=='phase':control['native_alignment']['offset_frames']+=1
            elif mutation=='native_rms':control['native_alignment']['normalized_rms_error']=float('nan')
            else:control['matched_waveform']['complete_reference_covered']=False
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                validate_sink_turns_run(run,'normal',verify_files=False)

    def test_alignment_horizon_is_derived_from_raw_backend_profile(self):
        run=self.fixture(files=True);turn=run['actual_sink_turns'][0]
        path=self.root/'result'/turn['trace_file'];rows=[json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            if row.get('kind')=='stream' and row.get('stream')=='modem':row.update(reported_sink_usec=700000,configured_sink_usec=700000)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        budget=dict(turn['alignment_budget'],backend_usec=700000,maximum_lag_ms=1650)
        turn['alignment_budget']=budget;turn['decoder_control']['alignment_budget']=copy.deepcopy(budget)
        turn['content_match']['maximum_lag_ms']=1650;turn['decoder_control']['matched_waveform']['maximum_lag_ms']=1650
        validate_sink_turns_run(run,'normal')

    def test_arbitrary_larger_alignment_horizon_cannot_replace_raw_profile(self):
        run=self.fixture(files=True);turn=run['actual_sink_turns'][0]
        budget=dict(turn['alignment_budget'],backend_usec=700000,maximum_lag_ms=1650)
        turn['alignment_budget']=budget;turn['decoder_control']['alignment_budget']=copy.deepcopy(budget)
        turn['content_match']['maximum_lag_ms']=1650;turn['decoder_control']['matched_waveform']['maximum_lag_ms']=1650
        with self.assertRaisesRegex(ValueError,'actual negotiated outputs'):
            validate_sink_turns_run(run,'normal')

    def test_loaded_transport_dependency_must_match_campaign_identity(self):
        run=self.fixture()
        validate_sink_turns_run(run,'normal',verify_files=False,expected_transport_sha256=SYNTHETIC_TRANSPORT_SHA)
        with self.assertRaisesRegex(ValueError,'transport SRC'):
            validate_sink_turns_run(run,'normal',verify_files=False,expected_transport_sha256='0'*64)
        run['actual_sink_turns'][0]['decoder_control']['transport'].pop('library_sha256')
        with self.assertRaises(ValueError):validate_sink_turns_run(run,'normal',verify_files=False)

    def test_unmatched_decoder_rate_duration_or_input_is_rejected(self):
        for key,value in {'input_rate':48000,'input_channels':2,'input_frames':44100,
                          'unpadded_frames':1,'input_sha256':'0'*64,'decoded_frames':1,'decoded_sha256':'0'*64}.items():
            run=self.fixture();run['actual_sink_turns'][0]['observed'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_sink_turns_run(run,'normal',verify_files=False)

    def test_missing_partial_tampered_or_wrong_stats_artifact_blocks(self):
        for mutation in ('missing','partial','tampered','counter','source','trace'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root)
                run['case_record_path']=str(root/'case-result.json');turn=run['actual_sink_turns'][0]
                item=turn['artifacts']['actual_stimulus'];path=Path(item['path'])
                if mutation=='missing':path.unlink()
                elif mutation=='partial':path.write_bytes(b'\0\0');item['sha256']=sha(path);turn['decoder_control']['actual_stimulus_sha256']=sha(path);turn['observed']['input_sha256']=sha(path)
                elif mutation=='tampered':path.write_bytes(b'changed')
                elif mutation=='counter':
                    item=turn['artifacts']['actual_stats'];path=Path(item['path']);path.write_text('{}');item['sha256']=sha(path)
                elif mutation=='source':(root/'result/freedv-usb-session-unit-modem.raw').write_bytes(b'other')
                else:(root/'result/freedv-usb-session-unit.jsonl').write_text('')
                with self.subTest(mutation=mutation),self.assertRaises((ValueError,OSError)):
                    validate_sink_turns_run(run,'normal')

    def test_matching_rate_cannot_relax_existing_speech_loss_bound(self):
        run=self.fixture();turn=run['actual_sink_turns'][0]
        turn['observed'].update(synthesis_blocks=turn['reference']['synthesis_blocks']-2,
                               decoded_frames=round((turn['reference']['decoded_seconds']-.24)*44100))
        turn['observed']['decoded_seconds']=turn['observed']['decoded_frames']/44100
        with self.assertRaisesRegex(ValueError,'unchanged decoder bound'):
            validate_sink_turns_run(run,'normal',verify_files=False)


class ReleaseLockProtocolGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.identity={'source_commit':'a'*40,'build_id':'synthetic','test_binary_sha256':'b'*64,
            'runner_sha256':'c'*64,'speech_sha256':'d'*64,'modem_sha256':'e'*64}
        proof=infrastructure_fixture(self.root,self.identity,'f'*64)['record']
        self.protocol=proof['release_lock_protocol'];self.expected=proof

    def test_real_child_contract_is_required_independent_of_failed_variants(self):
        validate_release_lock_protocol(self.protocol,self.expected)
        with self.assertRaises(ValueError):validate_release_lock_protocol({},self.expected)

    def test_hang_or_wrong_binary_build_scope_and_checks_cannot_pass(self):
        for mutation in ('timeout','exit','source','build','binary','injector','radio','blocked','posted','unlock'):
            proof=copy.deepcopy(self.protocol)
            if mutation=='timeout':proof['execution']['timed_out']=True
            elif mutation=='exit':proof['execution']['exit_code']=-9
            elif mutation=='source':proof['source_commit']='old'
            elif mutation=='build':proof['build_id']='old'
            elif mutation=='binary':proof['controller_binary_sha256']='0'*64
            elif mutation=='injector':proof['fault_injector_sha256']='0'*64
            elif mutation=='radio':proof['radio_accessed']=True
            else:proof['witness'][{'blocked':'coordinator_blocked','posted':'release_posted_without_wait','unlock':'observed_after_unlock'}[mutation]]=False
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                validate_release_lock_protocol(proof,self.expected,verify_files=False)

    def test_witness_log_is_retained_and_summary_is_bound_to_actual_child(self):
        path=Path(self.protocol['evidence_files']['log']['path']);path.write_text('PASS\n')
        with self.assertRaises(ValueError):validate_release_lock_protocol(self.protocol,self.expected)
        self.protocol['evidence_files']['log']['sha256']=sha(path)
        with self.assertRaises(ValueError):validate_release_lock_protocol(self.protocol,self.expected)



class OutputContractRunGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def fixture(self,case='normal'):
        run=capture_case_fixture(case,self.root);run['case_record_path']=str(self.root/'case-result.json');return run

    def test_every_completed_turn_requires_typed_backend_contract(self):
        validate_output_contract_run(self.fixture())

    def test_zero_turn_stop_needs_markers_but_no_completed_backend_interval(self):
        run=self.fixture('stop-during-key')
        self.assertEqual(run['output_contract']['sessions'][0]['completed_intervals'],0)
        validate_output_contract_run(run)
        run.pop('usb_output_contract_version')
        with self.assertRaises(ValueError):validate_output_contract_run(run)

    def test_missing_unknown_or_diagnostic_contract_never_qualifies_new_build(self):
        for mutation in ('missing','version','diagnostic','sessions','turns','identity'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
                if mutation=='missing':run.pop('output_contract')
                elif mutation=='version':run['usb_output_contract_version']=2
                elif mutation=='diagnostic':run['output_contract']['status']='DIAGNOSTIC_ONLY'
                elif mutation=='sessions':run['output_contract']['sessions']=[]
                elif mutation=='turns':run['harness']['total_turns']+=1
                else:run['identity']['session_ids']=['old-session']
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_output_contract_run(run)

    def test_raw_tail_deletion_or_early_unkey_is_rejected_even_with_updated_hash(self):
        for mutation in ('missing','early_unkey','wrong_identity','wait_arithmetic','deadline'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
                entry=run['output_contract']['sessions'][0];trace=root/'result'/entry['trace_file']
                rows=[json.loads(line) for line in trace.read_text().splitlines()]
                receipt=next(row for row in rows if row.get('kind')=='backend-tail')
                if mutation=='missing':rows=[row for row in rows if row.get('kind')!='backend-tail']
                elif mutation=='early_unkey':next(row for row in rows if row.get('event')=='state' and row.get('state')==8)['ms']=receipt['completed_us']//1000-1
                elif mutation=='wrong_identity':receipt['source_commit']='old'
                elif mutation=='wait_arithmetic':receipt['wait_usec']-=1
                else:receipt['timing_ack_ms']=receipt['timing_issued_ms']+1000;receipt['timing_ack_us']=receipt['timing_ack_ms']*1000
                trace.write_text(''.join(json.dumps(row)+'\n' for row in rows));entry['trace_sha256']=sha(trace)
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_output_contract_run(run)

    def test_copied_session_under_new_filename_cannot_inflate_turn_coverage(self):
        run=self.fixture();first=run['output_contract']['sessions'][0];second=copy.deepcopy(first)
        result=self.root/'result'
        for key in ('trace_file','completion_file'):
            source=result/first[key]
            target=result/('copied-'+first[key]);target.write_bytes(source.read_bytes())
            second[key]=target.name
        second['trace_sha256']=sha(result/second['trace_file'])
        second['completion_sha256']=sha(result/second['completion_file'])
        run['output_contract']['sessions'].append(second)
        run['harness'].update(total_turns=2,turns=2)
        run['identity']['session_ids']*=2
        with self.assertRaisesRegex(ValueError,'session identity was reused'):
            validate_output_contract_run(run)

    def test_session_summary_and_receipts_cannot_be_reused_or_tampered(self):
        run=self.fixture();run['output_contract']['sessions'].append(copy.deepcopy(run['output_contract']['sessions'][0]))
        with self.assertRaises(ValueError):validate_output_contract_run(run)
        run=self.fixture();entry=run['output_contract']['sessions'][0]
        path=self.root/'result'/entry['completion_file'];path.write_text('{}')
        with self.assertRaises(ValueError):validate_output_contract_run(run)



class OutputProfileAndRfWindowGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def fixture(self,case='normal'):
        run=capture_case_fixture(case,self.root);run['case_record_path']=str(self.root/'case-result.json');return run

    def test_default_and_fixed_models_cover_complete_rf_payload(self):
        for case in ('backend-fixed-pipe','io-pause150'):
            with tempfile.TemporaryDirectory() as directory:
                run=capture_case_fixture(case,Path(directory));run['case_record_path']=str(Path(directory)/'case-result.json')
                validate_backend_profile_run(run,case);validate_observer_admission_run(run,case)
                validate_sink_turns_run(run,case)

    def test_playback_adjust_flag_or_old_small_reserve_cannot_hide_in_summary(self):
        for mutation in ('adjust','noearly','domain','reserve'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
                path=root/'result'/run['output_contract']['sessions'][0]['trace_file']
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                modem=next(row for row in rows if row.get('kind')=='stream' and row.get('stream')=='modem')
                if mutation=='adjust':modem.update(requested_flags=0x6000,adjust_latency=True)
                elif mutation=='noearly':modem['requested_flags']&=~0x4000
                elif mutation=='domain':modem['request_domain']='sink-plus-client'
                else:modem.update(tlength=21609,minreq=1764,prebuf=19845)
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_backend_profile_run(run,'normal')

    def test_relative_volume_belongs_only_to_actual_headset_stream(self):
        for stream in ('headset','modem','mic','radio-rx'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
                validate_backend_profile_run(run,'normal')
                path=root/'result'/run['output_contract']['sessions'][0]['trace_file']
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                target=next(row for row in rows if row.get('kind')=='stream' and row.get('stream')==stream)
                target['requested_flags']^=0x40000
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.subTest(stream=stream),self.assertRaisesRegex(ValueError,'RELATIVE_VOLUME'):
                    validate_backend_profile_run(run,'normal')

    def test_relative_volume_requires_typed_headset_descriptor(self):
        rows=[{'kind':'stream','stream':'headset','requested_flags':0x44000},
              {'kind':'audio-budget','stream':'modem','requested_flags':0x4000}]
        validate_headset_volume_flags(rows,required=True)
        for mutation in ('missing-headset','absolute-headset','relative-modem','bool-flags'):
            changed=copy.deepcopy(rows)
            if mutation=='missing-headset':changed.pop(0)
            elif mutation=='absolute-headset':changed[0]['requested_flags']=0x4000
            elif mutation=='relative-modem':changed[1]['requested_flags']|=0x40000
            else:changed[0]['requested_flags']=True
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                validate_headset_volume_flags(changed,required=True)

    def test_observer_cannot_request_latency_or_begin_before_startup_admission(self):
        for mutation in ('missing','early','activeobserver','noblock'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
                if mutation=='missing':run.pop('observer_admission')
                elif mutation=='early':run['observer_admission']['ready_ns']=run['harness']['rf_intervals'][0]['on_before_ns']+1
                else:
                    path=root/'radio-output.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
                    if mutation=='activeobserver':rows[0].update(flags=0x2000,adjust_latency=True)
                    else:rows=[row for row in rows if row.get('kind')!='block']
                    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_observer_admission_run(run,'normal')

    def test_rf_clear_before_backend_completion_rejects_unchanged_waveform_pass(self):
        run=self.fixture();turn=run['actual_sink_turns'][0];window=turn['rf_window']
        self.assertEqual(turn['content_match']['status'],'PASS')
        self.assertEqual(turn['speech_status'],'DECODED_REFERENCE_MATCH')
        early=window['backend_completed_ns']-1
        window.update(rf_off_before_ns=early,rf_off_after_ns=early+1)
        run['harness']['rf_intervals'][0].update(off_before_ns=early,off_after_ns=early+1)
        with self.assertRaisesRegex(ValueError,'RF interval'):validate_sink_turns_run(run,'normal')

    def test_independent_fifo_late_signal_rejects_good_waveform_and_backend_flags(self):
        run=self.fixture('backend-fixed-pipe');turn=run['actual_sink_turns'][0];proof=turn['rf_window']
        path=Path(proof['fifo_artifacts']['timing']['path']);rows=[json.loads(line) for line in path.read_text().splitlines()]
        last=next(row for row in rows if row.get('kind')=='block' and row['frame_start']<=proof['last_signal_frame']<row['frame_end'])
        last['after_ns']=proof['rf_off_before_ns']+1000
        rows[-1]['finished_ns']=last['after_ns']+1000
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows));proof['fifo_artifacts']['timing']['sha256']=sha(path)
        self.assertEqual(turn['content_match']['status'],'PASS')
        with self.assertRaises(ValueError):validate_sink_turns_run(run,'backend-fixed-pipe')


class BackendFailureGateTests(unittest.TestCase):
    def test_all_four_post_eoo_backend_interruptions_keep_zero_completed_rf_turns(self):
        cases=('backend-timing-missing','backend-timing-invalid','backend-stop-before-timing','backend-stop-during-wait')
        for case in cases:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture(case,root);run['case_record_path']=str(root/'case-result.json')
                validate_capture_close_run(run,verify_files=True);validate_backend_failure_run(run,case)
                self.assertEqual(run['harness']['total_turns'],0)
                self.assertEqual(run['harness']['total_eoo'],1)

    def test_wrong_phase_or_original_fault_and_erased_interruption_cannot_pass(self):
        for mutation in ('missing','phase','fault','eoo','RFturn','clear','log'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('backend-timing-invalid',root);run['case_record_path']=str(root/'case-result.json')
                if mutation=='missing':run.pop('backend_failure')
                elif mutation=='phase':run['backend_failure']['requested_phase']='tx'
                elif mutation=='fault':run['backend_failure']['fault']=1
                elif mutation=='eoo':run['harness']['total_eoo']=0
                elif mutation=='RFturn':run['harness']['total_turns']=1
                elif mutation=='clear':run['harness']['rf_clear_confirmed']=False
                else:(root/'test.log').write_text('PASS')
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_backend_failure_run(run,'backend-timing-invalid')



class HostResourceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.config=host_resource_configuration()
        self.identity={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'build_id':'unit-resource',
                       'test_binary_sha256':'c'*64,'host_resource_configuration':self.config}

    def fixture(self):
        return {'identity':self.identity,'plan':{'host_resource_configuration':self.config},
                'host_resources':resource_fixture(self.root,self.identity,self.config)}

    def test_known_host_with_past_pressure_and_no_new_pressure_passes(self):
        validate_host_resources(self.fixture())

    def test_start_end_headroom_are_independent_fixed_minima(self):
        for phase,current in (('preflight',13*1024**3),('end',16*1024**3-512*1024**2)):
            evidence=self.fixture();report=evidence['host_resources'][phase];snapshot=report['after']
            head=16*1024**3-current;snapshot['memory'].update(current_bytes=current,headroom_bytes=head)
            snapshot['headroom_bytes']=report['headroom_bytes']=head
            with self.subTest(phase=phase),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_unknown_cgroup_does_not_use_unbounded_host_fallback(self):
        evidence=self.fixture();evidence['host_resources']['preflight']['after']['memory']['max_bytes']=None
        with self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_new_or_reset_pressure_counter_cannot_be_hidden_by_pass_flag(self):
        for delta in (-1,1):
            evidence=self.fixture();evidence['host_resources']['end']['after']['memory']['events']['max']+=delta
            with self.subTest(delta=delta),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_changed_limits_or_postreclaim_baseline_block_promotion(self):
        for mutation in ('cpuquota','baseline'):
            evidence=self.fixture()
            if mutation=='cpuquota':evidence['host_resources']['end']['after']['cpu']['quota_us']=200000
            else:evidence['host_resources']['end']['before']=copy.deepcopy(evidence['host_resources']['end']['before']);evidence['host_resources']['end']['before']['memory']['events']['max']=0
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_cpu_throttling_alone_is_diagnostic_not_a_fabricated_audio_fault(self):
        evidence=self.fixture();cpu=evidence['host_resources']['end']['after']['cpu']['stat']
        cpu.update(nr_throttled=200,throttled_usec=999999)
        validate_host_resources(evidence,verify_files=False)

    def test_preflight_alone_cannot_promote(self):
        evidence=self.fixture();del evidence['host_resources']['end']
        validate_host_resources(evidence,require_end=False)
        with self.assertRaises(ValueError):validate_host_resources(evidence)

    def test_cache_preparation_must_remain_readonly_and_bounded(self):
        for key,value in (('contents_modified',True),('timed_out',True),('worker_still_running',True)):
            evidence=self.fixture();evidence['host_resources']['preflight']['reclaim'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)

    def test_thresholds_and_resource_config_cannot_be_weakened(self):
        for key,value in (('min_headroom_bytes',1024**3),('min_end_headroom_bytes',0)):
            evidence=self.fixture();evidence['plan']['host_resource_configuration'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_host_resources(evidence,verify_files=False)
            self.config=host_resource_configuration();self.identity['host_resource_configuration']=self.config

    def test_retained_resource_receipts_cannot_be_replaced(self):
        evidence=self.fixture();Path(evidence['host_resources']['end_report_path']).write_text('{}\n')
        with self.assertRaises(ValueError):validate_host_resources(evidence)


class CaptureCloseGateTests(unittest.TestCase):
    def run_fixture(self,rf=1,extra=False,fault=1):
        return {'harness':{'turns':rf,'total_turns':rf,'fault':fault},
                'capture_close':capture_close_fixture(rf,extra)}

    def test_finite_closed_interval_and_later_rf_fault_are_distinct(self):
        validate_capture_close_run(self.run_fixture())
        validate_capture_close_run(self.run_fixture(0,extra=True,fault=4))

    def test_missing_proof_or_rf_interval_is_rejected(self):
        for evidence in ({'harness':{'turns':1}},self.run_fixture()):
            if 'capture_close' in evidence:evidence['capture_close']['turns']=[]
            with self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_postcutoff_acceptance_or_missing_frame_cannot_conserve_interval(self):
        for key,delta in (('consumed_index',1),('accepted_frames',-1),('mic_tap_end',1)):
            evidence=self.run_fixture();evidence['capture_close']['turns'][0][key]+=delta
            with self.subTest(key=key),self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_monitor_latency_cannot_rewrite_capture_frontier(self):
        evidence=self.run_fixture();row=evidence['capture_close']['turns'][0]
        row['cutoff_index']-=480;row['consumed_index']-=480;row['accepted_frames']-=480;row['mic_tap_end']-=480
        row['microphone_frame_budget']['accepted_frames']-=480
        with self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_deadline_and_existing_source_budget_cannot_be_relaxed(self):
        for mutation in ('deadline','tolerance','fragments'):
            evidence=self.run_fixture();row=evidence['capture_close']['turns'][0]
            if mutation=='deadline':row['close_elapsed_ms']=751
            elif mutation=='tolerance':row['microphone_frame_budget']['delivery_tolerance_percent']=2
            else:row['microphone_frame_budget']['boundary_frames']+=1
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_capture_close_run(evidence)

    def test_closed_capture_without_completed_rf_requires_actual_fault(self):
        with self.assertRaises(ValueError):validate_capture_close_run(self.run_fixture(0,extra=True,fault=1))

    def test_abandoned_interval_never_counts_as_completed_rf(self):
        evidence=self.run_fixture(0);evidence['capture_close']['abandoned_intervals']=1
        evidence['harness']['total_turns']=1;evidence['capture_close']['completed_rf_turns']=1
        with self.assertRaises(ValueError):validate_capture_close_run(evidence)


class CaptureTailBudgetGateTests(unittest.TestCase):
    def fixture(self,prefix=57600,tail=5830):
        close=capture_close_fixture(1);row=close['turns'][0]
        start=1000;fence=start+prefix;cutoff=fence+tail
        usec=(tail*1000000)//48000
        row.update(start_index=start,release_fence_index=fence,cutoff_index=cutoff,consumed_index=cutoff,
            accepted_frames=prefix+tail,mic_tap_start=0,mic_tap_end=prefix+tail,tail_frames=tail,
            write_index_bytes=fence*2,source_usec=120348 if tail==5830 else usec,
            transport_usec=1106 if tail==5830 else 0,frontier_extra_frames=tail,
            capture_started_ms=1000,close_started_ms=2200,timing_issued_ms=2201,frozen_ms=2330,
            reached_ms=2350,closed_ms=2351,close_elapsed_ms=151)
        row['microphone_frame_budget'].update(active_ms=1200,accepted_frames=prefix+tail,expected_frames=57600.,
            minimum_frames=55104,maximum_frames=60096,pre_fence_frames=prefix,pipeline_tail_frames=tail)
        return {'harness':{'turns':1,'total_turns':1,'fault':1},'capture_close':close}

    def test_indexed_pipeline_tail_is_separate_from_unchanged_live_upper_bound(self):
        run=self.fixture();row=run['capture_close']['turns'][0]
        self.assertGreater(row['accepted_frames'],row['microphone_frame_budget']['maximum_frames'])
        self.assertEqual(row['microphone_frame_budget']['pre_fence_frames'],57600)
        validate_capture_close_run(run)

    def test_total_lower_and_live_prefix_upper_are_not_relaxed(self):
        for prefix,tail in ((52000,2000),(60100,3000)):
            with self.subTest(prefix=prefix,tail=tail),self.assertRaises(ValueError):
                validate_capture_close_run(self.fixture(prefix,tail))

    def test_tail_cannot_hide_a_different_fence_or_prefix_deficit(self):
        for mutation in ('tail','prefix','fence','legacy','basis'):
            run=self.fixture();row=run['capture_close']['turns'][0];budget=row['microphone_frame_budget']
            if mutation=='tail':budget['pipeline_tail_frames']+=1
            elif mutation=='prefix':budget['pre_fence_frames']-=1
            elif mutation=='fence':row['release_fence_index']+=1
            elif mutation=='legacy':budget.pop('schema')
            else:budget['comparison_basis']='maximum_on_total_plus_tolerance'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_capture_close_run(run)

    def stop_after_complete_fixture(self,root):
        from usb_output_contract import validate_output_contract
        run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
        run['harness'].update(turns=0,total_turns=0,first_fault_origin='local-stop')
        interval=run['capture_close']['turns'][0];interval['rf_completed']=False
        run['capture_close']['completed_rf_turns']=0
        path=root/'result'/interval['trace_file'];rows=[json.loads(line) for line in path.read_text().splitlines()]
        rows=[row for row in rows if not (row.get('event')=='state' and row.get('state')==8)]
        first_fault={'kind':'event','event':'first-fault','status':1,'operation':'local-stop','state':4,'ms':interval['closed_ms']+100}
        rows.insert(-1,first_fault);rows[-1]['turns']=0
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        cp=path.with_name(path.stem+'-completion.json');completion=json.loads(cp.read_text());completion['turns']=0;atomic_json(cp,completion)
        summary=validate_output_contract(rows,completion,required_version=1)
        run['output_contract']['sessions']=[dict(summary,trace_file=path.name,completion_file=cp.name,trace_sha256=sha(path),completion_sha256=sha(cp))]
        interval['completion_interruption']={'schema':'x6200.usb.capture-interruption.v1','status':'CONFIRMED',
            'fault':1,'origin':'local-stop','state':4,'ms':first_fault['ms'],'closed_ms':interval['closed_ms'],
            'trace_file':path.name,'trace_sha256':sha(path)}
        return run

    def test_completed_capture_before_local_stop_does_not_claim_rf_success(self):
        with tempfile.TemporaryDirectory() as directory:
            run=self.stop_after_complete_fixture(Path(directory))
            validate_output_contract_run(run);validate_capture_close_run(run,verify_files=True)
            self.assertEqual(run['capture_close']['completed_rf_turns'],0)
            self.assertFalse(run['capture_close']['turns'][0]['rf_completed'])

    def test_stop_summary_without_exact_retained_drain_chronology_is_rejected(self):
        for mutation in ('missing','early','phase','hash'):
            with tempfile.TemporaryDirectory() as directory:
                run=self.stop_after_complete_fixture(Path(directory));interval=run['capture_close']['turns'][0]
                witness=interval['completion_interruption']
                if mutation=='missing':interval.pop('completion_interruption')
                elif mutation=='early':witness['ms']=interval['closed_ms']-1
                elif mutation=='phase':witness['state']=3
                else:witness['trace_sha256']='0'*64
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    validate_capture_close_run(run,verify_files=False)

    def test_source_horizon_and_capture_close_deadline_remain_bounded(self):
        for mutation in ('horizon','deadline'):
            run=self.fixture()
            if mutation=='horizon':
                row=run['capture_close']['turns'][0];row['source_usec']=750001;row['transport_usec']=0
            else:run['capture_close']['turns'][0]['close_elapsed_ms']=751
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_capture_close_run(run)

    def test_retained_raw_gap_or_altered_tail_cannot_be_masked_by_budget_receipt(self):
        for mutation in ('index_gap','release_fence_index','source_usec','submitted_first_index'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_case_fixture('normal',root);run['case_record_path']=str(root/'case-result.json')
                path=root/'result/freedv-usb-session-unit.jsonl'
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                raw=next(row for row in rows if row.get('kind')=='capture-close')
                raw[mutation]=True if mutation=='index_gap' else raw[mutation]+1
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'raw finite source receipt'):
                    validate_capture_close_run(run,verify_files=True)



class CapturePipelineTailCaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def fixture(self):
        run=capture_pipeline_tail_fixture(self.root);run['case_record_path']=str(self.root/'case-result.json');return run

    def test_complete_source_model_observer_sink_and_rx_proof_pass(self):
        validate_capture_pipeline_tail_run(self.fixture())

    def test_read_frontier_can_include_observed_postcutoff_pending_pcm(self):
        run=self.fixture();proof=run['capture_pipeline_tail'];interval=run['capture_close']['turns'][0]
        original=proof['read_index_bytes'];proof['read_index_bytes']=(interval['cutoff_index']+890)*2
        log=self.root/'test.log';log.write_text(log.read_text().replace('read_index_bytes='+str(original),
                                                                       'read_index_bytes='+str(proof['read_index_bytes'])))
        validate_capture_pipeline_tail_run(run)

    def test_missing_model_or_old_upper_counterexample_cannot_claim_coverage(self):
        for mutation in ('missing','phase','model','counterexample','prefix','total','minimum','eoo','rx'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_pipeline_tail_fixture(root);run['case_record_path']=str(root/'case-result.json')
                proof=run['capture_pipeline_tail']
                if mutation=='missing':run.pop('capture_pipeline_tail')
                elif mutation=='phase':proof['phase']='initial-rx'
                elif mutation=='model':proof['model_source_usec']=119999
                elif mutation=='counterexample':proof['original_upper_rejected']=False
                elif mutation=='prefix':proof['pre_fence_frames']+=1
                elif mutation=='total':proof['total_accepted_frames']-=1
                elif mutation=='minimum':proof['minimum_frames']-=1
                elif mutation=='eoo':run['harness']['total_eoo']=0
                else:run['harness']['rf_tx']=True
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_capture_pipeline_tail_run(run,verify_files=False)

    def test_timing_indices_and_source_sentinel_are_bound_to_actual_artifacts(self):
        for mutation in ('log','observer','sentinel','boundary','hash'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);run=capture_pipeline_tail_fixture(root);run['case_record_path']=str(root/'case-result.json')
                proof=run['capture_pipeline_tail']
                if mutation=='log':(root/'test.log').write_text('arbitrary success')
                elif mutation=='observer':(root/'result/mic-observer.pcm').write_bytes(b'wrong')
                elif mutation=='sentinel':proof['source_boundary']['post_cutoff_sentinel_excluded']=False
                elif mutation=='boundary':proof['read_index_bytes']+=2
                else:proof['source_boundary']['expected_source_sha256']=proof['source_boundary']['accepted_sha256']='0'*64
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_capture_pipeline_tail_run(run)



class CaptureBoundaryProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.count=0

    def fixture(self,case='capture-release-split'):
        self.count+=1;root=self.root/str(self.count);root.mkdir()
        evidence=capture_source_boundary_fixture(case,root)
        path=root/'case-result.json';path.write_text('{}\n');evidence['case_record_path']=str(path)
        return evidence,root

    def test_exact_source_boundary_both_delivery_histories(self):
        for case in ('capture-release-split','capture-release-held150'):
            evidence,_=self.fixture(case);validate_capture_source_boundary_run(evidence,case)

    def test_split_callback_can_return_before_coordinator_observes_release(self):
        evidence,_=self.fixture();exercise=evidence['capture_source_boundary']['turns'][0]['exercise']
        exercise['callback_ended_ms']=exercise['release_posted_ms']
        self.assertGreater(exercise['release_observed_ms'],exercise['callback_ended_ms'])
        validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_split_fixture_cannot_require_coordinator_while_holding_lock(self):
        for mutation in ('wait','late_post'):
            evidence,_=self.fixture();exercise=evidence['capture_source_boundary']['turns'][0]['exercise']
            if mutation=='wait':exercise['coordinator_wait_required']=1
            else:exercise['release_posted_ms']=exercise['callback_ended_ms']+1
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_matching_counts_cannot_replace_missing_prefix_with_future_audio(self):
        evidence,root=self.fixture()
        path=root/'result/freedv-usb-session-unit-mic.raw';original=path.read_bytes()
        path.write_bytes(original[20:]+(30001).to_bytes(2,'little',signed=True)*10)
        with self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_faked_hash_labels_do_not_replace_independent_pcm_evidence(self):
        evidence,_=self.fixture();row=evidence['capture_source_boundary']['turns'][0]
        row['expected_source_sha256']=row['accepted_sha256']='0'*64
        with self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_postcutoff_exclusion_and_straddling_block_are_mandatory(self):
        for field in ('post_cutoff_excluded','post_cutoff_sentinel_excluded'):
            evidence,_=self.fixture();evidence['capture_source_boundary']['turns'][0][field]=False
            with self.subTest(field=field),self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-split')

    def test_held_delivery_must_overlap_async_frontier_freeze(self):
        evidence,_=self.fixture('capture-release-held150');row=evidence['capture_close']['turns'][0]
        row['frozen_ms']=3110;row['reached_ms']=3120
        with self.assertRaises(ValueError):validate_capture_source_boundary_run(evidence,'capture-release-held150')


class CaptureCloseFailureProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.count=0

    def fixture(self,mode):
        self.count+=1;root=self.root/str(self.count);root.mkdir()
        case='capture-close-'+mode;evidence=capture_close_failure_fixture(case,root)
        path=root/'case-result.json';path.write_text('{}\n');evidence['case_record_path']=str(path)
        return evidence,case

    def test_all_actual_drain_failure_paths_keep_clear_and_no_eoo(self):
        for mode in ('missing','invalid','stop'):
            evidence,case=self.fixture(mode);validate_capture_close_failure_run(evidence,case)

    def test_initial_phase_cannot_claim_drain_failure_coverage(self):
        evidence,case=self.fixture('missing');evidence['capture_close_failure']['phase']='initial-rx'
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)

    def test_late_successful_callback_cannot_commit_after_stop(self):
        evidence,case=self.fixture('stop');evidence['capture_close_failure']['abandoned_interval']['frozen_ms']=1003
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)

    def test_unrelated_fault_cannot_prove_invalid_frontier(self):
        evidence,case=self.fixture('invalid');evidence['capture_close_failure']['fault']=evidence['harness']['fault']=13
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)

    def test_withheld_ack_must_be_real_and_no_completed_voice_turn_claimed(self):
        evidence,case=self.fixture('missing');evidence['capture_close_failure']['withheld']=False
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)
        evidence,case=self.fixture('missing');evidence['harness']['eoo']=1
        with self.assertRaises(ValueError):validate_capture_close_failure_run(evidence,case)


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


class OperationalEvidenceTests(unittest.TestCase):
    def fixture(self,case,root):
        run=capture_case_fixture(case,root)
        run['case_record_path']=str(Path(root)/'case-result.json')
        return run

    def test_new_operational_cases_have_complete_bound_evidence(self):
        from test_usb_audio_integration import OPERATIONAL_CASES
        for case in sorted(OPERATIONAL_CASES):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                run=self.fixture(case,directory)
                validate_operational_run(run,case,verify_files=True)
                validate_output_contract_run(run,verify_files=True)
                validate_backend_profile_run(run,case,verify_files=True)
                validate_capture_close_run(run,verify_files=True)

    def test_operational_retune_requires_unchanged_actual_headset_pcm(self):
        for mutation in ('missing','silent-monitor','changed-tap'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                run=self.fixture('operational-retune',directory)
                if mutation=='missing':run.pop('actual_headset_output')
                else:
                    name='monitor' if mutation=='silent-monitor' else 'decoded'
                    artifact=run['actual_headset_output']['artifacts'][name];path=Path(artifact['path'])
                    path.write_bytes(b'\0'*path.stat().st_size)
                    if mutation=='silent-monitor':artifact['sha256']=sha(path) # Updated metadata cannot hide lost speech.
                with self.assertRaises(ValueError):validate_operational_run(run,'operational-retune',verify_files=True)

    def test_timeout_requires_actual_30_second_rf_window(self):
        for mutation in ('old20','late','setting'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                run=self.fixture('operational-timeout',directory)
                if mutation=='setting':run['harness']['tx_timeout_seconds']=20
                else:run['harness']['rf_intervals'][0]['off_after_ns']=21000000000 if mutation=='old20' else 34000000000
                with self.assertRaises(ValueError):
                    validate_operational_run(run,'operational-timeout',verify_files=True)

    def test_graceful_exit_cannot_be_relabelled_as_a_physical_release_or_abort(self):
        for mutation in ('missing','counter','reason','codec','receipt','readiness'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                run=self.fixture('operational-leave-tx',directory)
                if mutation=='missing':run.pop('operational')
                elif mutation=='counter':run['harness']['physical_release_sequence']=1
                elif mutation=='reason':run['operational']['completion_reason']='stop'
                elif mutation=='codec':run['harness']['rade_creations']=2
                elif mutation=='receipt':
                    path=Path(directory)/'result/freedv-usb-session-unit.jsonl'
                    rows=[json.loads(line) for line in path.read_text().splitlines()]
                    for row in rows:
                        if row.get('kind')=='capture-close':row['close_trigger']='ptt-release'
                    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                else:(Path(directory)/'result/freedv-usb-session-unit-readiness.json').unlink()
                with self.assertRaises((ValueError,OSError)):
                    validate_operational_run(run,'operational-leave-tx',verify_files=True)


class AudioControlEvidenceTests(unittest.TestCase):
    """Synthetic gate negatives; the real Pulse/controller case supplies evidence."""
    def fixture(self,live=False,root=None):
        return audio_controls_fixture(live,root)

    def test_rx_preview_and_live_turn_receipts_pass(self):
        for live,validator in ((False,validate_audio_controls_run),(True,validate_audio_gain_turn_run)):
            with self.subTest(live=live),tempfile.TemporaryDirectory() as directory:
                case='audio-gain-turn' if live else 'audio-controls'
                run=capture_case_fixture(case,directory)
                run['case_record_path']=str(Path(directory)/'case-result.json')
                validator(run,verify_files=True)
                validate_output_contract_run(run,verify_files=True)
                validate_backend_profile_run(run,case,verify_files=True)
                validate_capture_close_run(run,verify_files=True)
                validate_sink_turns_run(run,case,verify_files=True)

    def test_rx_preview_cannot_key_or_recreate_codec(self):
        for field,value in (('state',3),('armed',True),('rf_tx',True),('keys',1),('submitted_mic',1),
                            ('mic_clipped_samples',1),('encoder_creations',2),('generation',1),('mic_meter_ready',False)):
            run=self.fixture();run['audio_controls']['phases'][3]['snapshot'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_audio_controls_run(run,False)

    def test_unscaled_clipped_or_stale_microphone_preview_rejects(self):
        for index,field,value in ((3,'mic_rms_dbfs10',-150),(4,'mic_peak_dbfs10',-120),
                                  (5,'mic_gain_db',0),(6,'mic_input_peak_dbfs10',-960)):
            run=self.fixture();run['audio_controls']['phases'][index]['snapshot'][field]=value
            with self.subTest(index=index,field=field),self.assertRaises(ValueError):validate_audio_controls_run(run,False)

    def test_lost_detents_mute_balance_or_unrelated_sink_change_rejects(self):
        for live,validator,key in ((False,validate_audio_controls_run,'audio_controls'),(True,validate_audio_gain_turn_run,'audio_gain_turn')):
            for mutation in ('detent','mute','balance','modem','other'):
                run=self.fixture(live);entry=run[key]['phases'][0 if live else 1];headset=entry['pulse']['alsa_output.usb-fixture']
                if mutation=='detent':headset['volume']=[26869,20152]  # Only one +1% update survived.
                elif mutation=='mute':headset['mute']=True
                elif mutation=='balance':headset['volume'][1]=headset['volume'][0]
                else:entry['pulse']['radio_sink_fixture' if mutation=='modem' else 'unrelated_sink']['volume'][0]-=1000
                with self.subTest(live=live,mutation=mutation),self.assertRaises(ValueError):validator(run,False)

    def test_live_controls_require_same_tx_generation_and_completed_return(self):
        for mutation in ('generation','reset_progress','codec','eoo','return'):
            run=self.fixture(True);phases=run['audio_gain_turn']['phases']
            if mutation=='generation':phases[1]['snapshot']['generation']=1
            elif mutation=='reset_progress':phases[1]['snapshot']['submitted_mic']=0
            elif mutation=='codec':phases[1]['snapshot']['rade_creations']=2
            elif mutation=='eoo':run['harness']['eoo']=1
            else:phases[2]['snapshot']['state']=3
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_audio_gain_turn_run(run,False)

    def test_modified_actual_pulse_readback_rejects(self):
        for live,validator,key in ((False,validate_audio_controls_run,'audio_controls'),(True,validate_audio_gain_turn_run,'audio_gain_turn')):
            with self.subTest(live=live),tempfile.TemporaryDirectory() as directory:
                run=self.fixture(live,directory);path=Path(run[key]['phases'][0]['pulse_path'])
                path.write_text('{}\n')
                with self.assertRaises(ValueError):validator(run,True)


if __name__ == '__main__':
    unittest.main()
