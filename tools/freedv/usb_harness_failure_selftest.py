#!/usr/bin/env python3
"""Prove an unexpected real-controller failure stays failed and retains evidence.

This is an infrastructure prerequisite, never a passing functional radio case.
The existing normal driver receives an insufficient-buffer getter only in its
C child. It must exit nonzero after orderly failure reporting and teardown.
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


def run_failure_preservation(binary,speech,modem,runner,injector,output,
                             expected_source_commit,expected_build_id,expected_binary_sha256):
    binary,speech,modem,runner,injector,output=map(lambda value:Path(value).resolve(),
                                               (binary,speech,modem,runner,injector,output))
    output.mkdir(parents=True,exist_ok=False)
    record={'schema':'x6200.usb.infrastructure.v1','kind':'unexpected-runtime-failure','status':'FAIL',
            'scope':'Expected failed child preserves actual C receipts; not functional qualification or RF evidence',
            'radio_accessed':False,'source_commit':expected_source_commit,'build_id':expected_build_id,
            'controller_binary_sha256':sha(binary),'fixture_sha256':{'speech':sha(speech),'modem':sha(modem)},
            'runner_sha256':sha(runner),'fault_injector_sha256':sha(injector),'checks':{}}
    previous_term=signal.getsignal(signal.SIGTERM)
    def interrupted(signum,frame):
        del signum,frame
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise RuntimeError('Infrastructure prerequisite interrupted; preserving failed attempt')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        require(record['controller_binary_sha256']==expected_binary_sha256,'Infrastructure test received a different controller binary')
        case=output/'failed-normal'
        env=dict(os.environ,USB_TEST_EXPECTED_SOURCE_COMMIT=expected_source_commit,
                 USB_TEST_EXPECTED_BUILD_ID=expected_build_id,USB_TEST_EXPECTED_BINARY_SHA256=expected_binary_sha256)
        script='''import sys
sys.path.insert(0,sys.argv[1])
import test_usb_audio_integration as driver
original=driver.injection_environment
driver.injection_environment=lambda case,root,library:original('buffer-contract-reject',root,library)
sys.argv=['test_usb_audio_integration.py',*sys.argv[2:]]
raise SystemExit(driver.main())
'''
        command=[sys.executable,'-c',script,Path(__file__).resolve().parent,binary,speech,modem,
                 '--case','normal','--output',case,'--runner',runner,'--fault-inject-so',injector]
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
        collector_path=case/'failure-collector-contract/actual/rf-evidence.json'
        collected=json.loads(collector_path.read_text())
        record.update(driver_status=result['status'],harness_exit_code=result.get('controller_exit_code'),
                      harness_status=failed.get('status'),terminal_fault_code=completion.get('fault'),
                      terminal_fault_origin=fault.get('first_fault',{}).get('origin'),collector_status=collected.get('status'))
        require(record['driver_status']=='FAIL' and record['harness_status']=='FAIL','A failed runtime was represented as PASS')
        require(type(record['harness_exit_code']) is int and record['harness_exit_code']>0 and
                failed.get('exit_code')==record['harness_exit_code'],'The C harness aborted or hid its orderly failure exit')
        require(failed.get('cleanup_requested') and failed.get('stopped') and failed.get('closed'),
                'Unexpected fault did not complete bounded normal teardown')
        require(record['terminal_fault_code']==13 and record['terminal_fault_origin']=='modem-buffer-budget' and
                failed.get('primary_fault')==13 and failed.get('primary_origin')=='modem-buffer-budget',
                'Unexpected failure lost its original buffer-admission fault')
        require(completion.get('terminal') is True and completion.get('trace_complete') is True and
                completion.get('rf_clear_confirmed') is True,'The original terminal fault/RX receipt was not retained')
        for value in (pointer,completion,fault):
            require(value.get('source_commit')==expected_source_commit and value.get('build_id')==expected_build_id,
                    'Failure receipts identify a different source or build')
        require(record['collector_status']=='BLOCKED' and collected.get('ready_for_rf_test') is False and
                result.get('failure_collector_contract',{}).get('status')=='PASS',
                'Production collector did not preserve the failed-controller verdict')
        require('INJECT negotiated buffer rejection' in (case/'test.log').read_text(),
                'The unexpected real-controller fault was not actually injected')
        record['checks']={'driver_failed':True,'harness_failed':True,'terminal_fault_retained':True,'collector_blocked':True}
        paths={'case_result':case/'case-result.json','harness_failure':case/'result/harness-failure.json',
               'completion':completion_path,'first_fault':fault_path,'collector_report':collector_path,
               'trace':case/'result'/pointer['trace_basename']}
        record['evidence_files']={name:{'path':str(path),'sha256':sha(path)} for name,path in paths.items()}
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
