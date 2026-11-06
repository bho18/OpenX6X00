#!/usr/bin/env python3
"""Prove unexpected controller failures stay failed and retain evidence.

This is an infrastructure prerequisite, never a passing functional radio case.
A pure lock protocol check runs first without opening audio or radio. Both
initial admission and active RF-disabled qualification must exit nonzero
after orderly failure reporting and teardown. Neither expected failed child
counts as a passing functional case.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys

from usb_test_support import require, run_bounded, save_json


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


FAILURE_VARIANTS={
    'initial_buffer':{'case':'normal','fault':13,'origin':'modem-buffer-budget','rf_disabled':False},
    'qualification_telemetry':{'case':'rf-disabled','fault':4,'origin':'radio-packet-stale','rf_disabled':True},
}


def _run_release_lock_protocol(binary,injector,output,identity):
    """Exercise the real injector action with a lock-blocked model worker.

    The child opens no audio or radio; the outer process bounds a broken join
    or reintroduced circular wait and preserves its log as failed evidence.
    """
    output.mkdir(parents=True,exist_ok=False)
    record={'schema':'x6200.usb.release-lock-protocol.v1','status':'FAIL',
            **{key:identity[key] for key in ('source_commit','build_id','controller_binary_sha256',
                                            'fault_injector_sha256','radio_accessed')},'checks':{}}
    log=output/'protocol.log'
    try:
        env=dict(os.environ,LD_PRELOAD=str(injector))
        executed=run_bounded([binary,'--release-lock-protocol-selftest'],log,5,env=env)
        record['execution']=executed
        require(not executed['timed_out'] and executed['exit_code']==0,
                'Release lock protocol hung or failed')
        prefix='RELEASE_LOCK_PROTOCOL '
        witnesses=[json.loads(line[len(prefix):]) for line in log.read_text().splitlines() if line.startswith(prefix)]
        require(len(witnesses)==1,'Release lock protocol did not retain one structured witness')
        witness=witnesses[0]
        require(witness.get('status')=='PASS' and witness.get('source_commit')==identity['source_commit'] and
                witness.get('build_id')==identity['build_id'] and witness.get('radio_accessed') is False,
                'Release lock protocol has the wrong binary identity or scope')
        checks=('coordinator_blocked','release_posted_without_wait','observed_after_unlock')
        require(all(witness.get(key) is True for key in checks),'Release lock protocol did not exercise the blocked interleaving')
        record['checks']={key:True for key in checks};record['witness']=witness;record['status']='PASS'
    except Exception as error:
        record['reason']=str(error)
    finally:
        if log.is_file():record['evidence_files']={'log':{'path':str(log),'sha256':sha(log)}}
        save_json(output/'protocol-result.json',record)
    return record


def _run_variant(name,spec,binary,speech,modem,runner,injector,output,identity):
    output.mkdir(parents=True,exist_ok=False)
    record={'schema':'x6200.usb.infrastructure.v1','kind':'unexpected-runtime-failure','status':'FAIL',
            'variant':name,'driver_case':spec['case'],**identity,'checks':{}}
    try:
        case=output/('failed-'+spec['case'])
        env=dict(os.environ,USB_TEST_EXPECTED_SOURCE_COMMIT=identity['source_commit'],
                 USB_TEST_EXPECTED_BUILD_ID=identity['build_id'],
                 USB_TEST_EXPECTED_BINARY_SHA256=identity['controller_binary_sha256'])
        script='''import sys
sys.path.insert(0,sys.argv[1])
import test_usb_audio_integration as driver
original=driver.injection_environment
if sys.argv[2]=='initial_buffer':
    driver.injection_environment=lambda case,root,library:original('buffer-contract-reject',root,library)
else:
    driver.injection_environment=lambda case,root,library:{**original(case,root,library),'USB_TEST_QUALIFICATION_STATUS_FREEZE':'1'}
sys.argv=['test_usb_audio_integration.py',*sys.argv[3:]]
raise SystemExit(driver.main())
'''
        command=[sys.executable,'-c',script,Path(__file__).resolve().parent,name,binary,speech,modem,
                 '--case',spec['case'],'--output',case,'--runner',runner,'--fault-inject-so',injector]
        executed=run_bounded(command,output/'driver.log',105,env=env,terminate_grace=8)
        record['driver_exit_code']=executed['exit_code'];record['execution']=executed
        require(not executed['timed_out'] and executed['exit_code']!=0,'Unexpected C failure was hidden or the driver hung')
        result=json.loads((case/'case-result.json').read_text())
        failed=json.loads((case/'result/harness-failure.json').read_text())
        pointer=json.loads((case/'result/freedv-usb-current.json').read_text())
        completion_path=case/'result'/pointer['completion_basename']
        completion=json.loads(completion_path.read_text())
        fault_path=case/'result'/(pointer['session_id']+'-fault.json')
        fault=json.loads(fault_path.read_text())
        trace_path=case/'result'/pointer['trace_basename']
        rows=[json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
        collector_path=case/'failure-collector-contract/actual/rf-evidence.json'
        collected=json.loads(collector_path.read_text())
        record.update(driver_status=result['status'],harness_exit_code=result.get('controller_exit_code'),
                      harness_status=failed.get('status'),terminal_fault_code=completion.get('fault'),
                      terminal_fault_origin=fault.get('first_fault',{}).get('origin'),collector_status=collected.get('status'),
                      rf_disabled=completion.get('rf_disabled'),keys=failed.get('keys'))
        require(record['driver_status']=='FAIL' and record['harness_status']=='FAIL','A failed runtime was represented as PASS')
        require(type(record['harness_exit_code']) is int and record['harness_exit_code']>0 and
                failed.get('exit_code')==record['harness_exit_code'],'The C harness aborted or hid its orderly failure exit')
        require(failed.get('cleanup_requested') and failed.get('stopped') and failed.get('closed'),
                'Unexpected fault did not complete bounded normal teardown')
        require(record['terminal_fault_code']==spec['fault'] and record['terminal_fault_origin']==spec['origin'] and
                failed.get('primary_fault')==spec['fault'] and failed.get('primary_origin')==spec['origin'],
                'Unexpected failure lost its original '+name+' fault')
        require(record['rf_disabled'] is spec['rf_disabled'] and failed.get('rf_disabled') is spec['rf_disabled'] and
                record['keys']==0,'The failure probe reached the wrong RF policy or keyed a transmitter')
        require(completion.get('terminal') is True and completion.get('trace_complete') is True and
                completion.get('rf_clear_confirmed') is True,'The original terminal fault/RX receipt was not retained')
        for value in (pointer,completion,fault):
            require(value.get('source_commit')==identity['source_commit'] and value.get('build_id')==identity['build_id'],
                    'Failure receipts identify a different source or build')
        require(record['collector_status']=='BLOCKED' and collected.get('ready_for_rf_test') is False and
                result.get('failure_collector_contract',{}).get('status')=='PASS',
                'Production collector did not preserve the failed-controller verdict')
        paths={'case_result':case/'case-result.json','harness_failure':case/'result/harness-failure.json',
               'completion':completion_path,'first_fault':fault_path,'collector_report':collector_path,'trace':trace_path}
        if name=='initial_buffer':
            require('INJECT negotiated buffer rejection' in (case/'test.log').read_text(),
                    'The unexpected real-controller admission fault was not actually injected')
        else:
            marker=case/'result/qualification-status-freeze.json';freeze=json.loads(marker.read_text())
            qualified=[row for row in rows if row.get('event')=='qualification-start']
            faults=[row for row in rows if row.get('event')=='first-fault' and row.get('status')==4 and
                    row.get('operation')=='radio-packet-stale']
            recovered=failed.get('telemetry_freeze_recovered_ms',0)
            require(freeze.get('kind')=='test-telemetry-freeze' and freeze.get('qualification_running') is True and
                    freeze.get('keys')==0 and freeze.get('duration_ms')==350 and
                    freeze['packet_ms']<=freeze['ms'] and freeze.get('sequence',0)>0,
                    'The telemetry failure was not injected during active RF-disabled qualification')
            require(qualified and faults and qualified[0]['ms']<=faults[0]['ms'] and
                    faults[0]['ms']>=freeze['packet_ms']+250 and recovered>=freeze['ms']+350 and
                    any(row.get('kind')=='sample' and row.get('qualification_running') is True for row in rows) and
                    completion.get('qualification_complete') is False,
                    'Active qualification failure or bounded telemetry recovery was not retained')
            record['qualification_failure']={'qualification_started_ms':qualified[0]['ms'],
                'freeze_started_ms':freeze['ms'],'packet_ms':freeze['packet_ms'],'sequence':freeze['sequence'],
                'freeze_duration_ms':350,'fault_ms':faults[0]['ms'],'recovered_ms':recovered,
                'qualification_complete':False,'keys':0}
            paths['injection_witness']=marker
        record['checks']={'driver_failed':True,'harness_failed':True,'terminal_fault_retained':True,'collector_blocked':True}
        record['evidence_files']={name:{'path':str(path),'sha256':sha(path)} for name,path in paths.items()}
        record['status']='PASS'
    except Exception as error:
        record['reason']=str(error)
    finally:
        save_json(output/'variant-result.json',record)
    return record


def run_failure_preservation(binary,speech,modem,runner,injector,output,
                             expected_source_commit,expected_build_id,expected_binary_sha256):
    binary,speech,modem,runner,injector,output=map(lambda value:Path(value).resolve(),
                                               (binary,speech,modem,runner,injector,output))
    output.mkdir(parents=True,exist_ok=False)
    record={'schema':'x6200.usb.infrastructure.v2','kind':'unexpected-runtime-failures','status':'FAIL',
            'scope':'Expected failed child preserves actual C receipts; not functional qualification or RF evidence',
            'radio_accessed':False,'source_commit':expected_source_commit,'build_id':expected_build_id,
            'controller_binary_sha256':sha(binary),'fixture_sha256':{'speech':sha(speech),'modem':sha(modem)},
            'runner_sha256':sha(runner),'fault_injector_sha256':sha(injector),'checks':{},'variants':{}}
    previous_term=signal.getsignal(signal.SIGTERM)
    def interrupted(signum,frame):
        del signum,frame
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise RuntimeError('Infrastructure prerequisite interrupted; preserving failed attempt')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        require(record['controller_binary_sha256']==expected_binary_sha256,'Infrastructure test received a different controller binary')
        identity={key:record[key] for key in ('source_commit','build_id','controller_binary_sha256','fixture_sha256',
                                            'runner_sha256','fault_injector_sha256','radio_accessed')}
        protocol=_run_release_lock_protocol(binary,injector,output/'release-lock-protocol',identity)
        record['release_lock_protocol']=protocol;save_json(output/'infrastructure-result.json',record)
        require(protocol['status']=='PASS',protocol.get('reason','Release lock protocol failed'))
        for name,spec in FAILURE_VARIANTS.items():
            variant=_run_variant(name,spec,binary,speech,modem,runner,injector,output/name,identity)
            record['variants'][name]=variant
            save_json(output/'infrastructure-result.json',record)
            require(variant['status']=='PASS',name+': '+variant.get('reason','failed preservation proof'))
        record['checks']={key:True for key in ('driver_failed','harness_failed','terminal_fault_retained','collector_blocked')}
        record['status']='PASS'
    except Exception as error:
        record['reason']=str(error)
    finally:
        signal.signal(signal.SIGTERM,previous_term)
        save_json(output/'infrastructure-result.json',record)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','speech','modem'):
        parser.add_argument(name,type=Path)
    parser.add_argument('--runner',type=Path,required=True)
    parser.add_argument('--fault-inject-so',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--build-id',required=True)
    parser.add_argument('--expected-binary-sha256',required=True)
    args=parser.parse_args()
    record=run_failure_preservation(args.binary,args.speech,args.modem,args.runner,args.fault_inject_so,args.output,
                                    args.source_commit,args.build_id,args.expected_binary_sha256)
    print(json.dumps(record,indent=2))
    return 0 if record['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
