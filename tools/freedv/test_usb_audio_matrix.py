#!/usr/bin/env python3
"""Run a bounded controller campaign and retain every qualification attempt.

The default qualification profile repeats the complete case registry twice.
One-pass regression and debug subsets help diagnosis, but cannot promote an
image. A later PASS cannot erase a failed/abandoned same-identity attempt.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys

from build_native_development import source_snapshot
from test_usb_audio_integration import CASES, SPEECH_CASES
from usb_test_campaign import (Campaign, MATRIX_SCHEMA, MIN_QUALIFICATION_REPEATS,
                               atomic_json, digest, sha, validate_campaign,
                               INFRASTRUCTURE_PREREQUISITES, validate_infrastructure_prerequisites, validate_tail_backpressure_run, validate_capture_close_run,
                               validate_capture_source_boundary_run, validate_capture_close_failure_run)
from usb_test_support import run_bounded

COVERAGE = {
    'prekey_audio': {'pa-delay', 'pa-reject', 'pa-missing'},
    'capture_reacquisition': {'cold-resume', 'cold-recovery-fails', 'cold-stop', 'cold-release', 'cold-prekey-unexpected-tx', 'cold-rx-resume'},
    'preparation_sample_accounting': {'cold-qualification'},
    'blocking_control': {'block-key', 'stop-during-key', 'release-during-key'},
    'slow_pa_ack': {'pa-delay'},
    'direction_health': {'inactive-hole', 'active-hole', 'inactive-stale', 'inactive-radio-hole', 'inactive-radio-stale', 'prekey-stale', 'capture-stale'},
    'rf_disabled': {'rf-disabled', 'qualification-physical-tx', 'qualification-held-ptt'},
    'retained_release': {'normal'},
    'stop_priority': {'stop-during-key'},
    'device_loss': {'disconnect'},
    'sink_decode': {'normal', 'rx-signal', 'rf-disabled'},
    'hardware_rates': {'normal'},
    'unsynchronized_rx': {'normal'},
    'stale_telemetry': {'stale-status'},
    'worker_watchdog': {'stall'},
    'bounded_queue_guard': {'tx-stall'},
    'many_turns': {'many-turns'},
    'independent_telemetry': {'normal', 'stale-status'},
    'phase_targeting': {'pa-delay', 'pa-reject', 'pa-missing', 'cold-rx-resume'},
    'per_turn_sink': {'normal', 'many-turns', 'reopen'},
    'durable_evidence': {'normal', 'cold-recovery-fails', 'reopen', 'storage-start-fails', 'storage-completion-fails'},
    'collector_contract': {'normal', 'cold-recovery-fails', 'reopen', 'storage-start-fails', 'storage-completion-fails'},
    'initial_audio': {'initial-pa-delay', 'initial-pa-reject', 'initial-pa-missing'},
    'rx_return_audio': {'return-pa-delay', 'return-pa-reject', 'return-pa-missing'},
    'session_reopen': {'reopen'},
    'cold_idle': {'cold-idle'},
    'drain_rf_ownership': {'drain-tx-loss'},
    'full_lifecycle': {'full-session'},
    'source_continuity': {'mic-valid-drop300', 'normal', 'many-turns'},
    'negotiated_buffers': {'buffer-contract-reject', 'io-pause150', 'rx-pause150'},
    'service_jitter': {'io-pause150', 'rx-pause150'},
    'receive_sink': {'rx-pause150'},
    'short_payload': {'short-turn200'},
    'tail_backpressure': {'eoo-backpressure'},
    'capture_close': {'normal','many-turns','reopen','full-session','capture-release-split','capture-release-held150',
                      'capture-close-missing','capture-close-invalid','capture-close-stop'},
    'source_boundary': {'capture-release-split','capture-release-held150'},
}
DURABILITY_CASES = {'many-turns', 'reopen', 'cold-idle', 'drain-tx-loss', 'full-session', 'mic-valid-drop300',
                    'io-pause150', 'rx-pause150', 'buffer-contract-reject', 'short-turn200', 'eoo-backpressure', 'capture-release-split',
                    'capture-release-held150', 'capture-close-missing', 'capture-close-invalid', 'capture-close-stop'}
CASE_TIMEOUTS = {'many-turns': 150, 'reopen': 210, 'cold-idle': 130, 'drain-tx-loss': 60, 'full-session': 120}


def _interrupted(signum, frame):
    del signum, frame
    raise KeyboardInterrupt('Matrix was interrupted; partial evidence is retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('speech', type=Path)
    parser.add_argument('modem', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--rade-library', type=Path, help='Actual dynamically loaded RADE library; required for qualification')
    parser.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--profile', choices=('qualification', 'regression', 'durability'), default='qualification')
    parser.add_argument('--repeats', type=int, help='Pinned repetitions; qualification requires at least two')
    parser.add_argument('--campaign-root', type=Path, help='Persistent source/configuration attempt history')
    parser.add_argument('--campaign-id', help='Human label only; does not change source/configuration history identity')
    parser.add_argument('--case-timeout', type=float, help='Override the bounded per-case deadline for diagnosis')
    parser.add_argument('--source-commit')
    parser.add_argument('--source-tree-sha256')
    parser.add_argument('--build-id', default='unqualified-development')
    parser.add_argument('--only', choices=CASES, action='append', help='Debug subset; never qualifies as the complete matrix')
    args = parser.parse_args()
    repetitions = args.repeats if args.repeats is not None else (MIN_QUALIFICATION_REPEATS if args.profile == 'qualification' else 1)
    if repetitions < 1 or (args.profile == 'qualification' and repetitions < MIN_QUALIFICATION_REPEATS):
        parser.error('qualification needs at least two repetitions; all profiles need a positive count')
    if args.profile == 'qualification' and (not args.rade_library or not args.rade_library.is_file()):
        parser.error('qualification requires --rade-library with the actual dynamically loaded codec')
    if args.case_timeout is not None and args.case_timeout <= 0:
        parser.error('--case-timeout must be positive')
    if args.only and len(args.only) != len(set(args.only)):
        parser.error('--only cases must be unique')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    repository = here.parents[1]
    selected = list(args.only or ([name for name in CASES if name in DURABILITY_CASES]
                                  if args.profile == 'durability' else CASES))
    expected = list(CASES)
    if not selected:
        parser.error('Selected profile has no registered cases')
    timeouts = {name: args.case_timeout or CASE_TIMEOUTS.get(name, 90) for name in selected}
    plan = {'profile': args.profile, 'repetitions': repetitions,
            'expected_cases': expected, 'selected_cases': selected,
            'debug_subset': bool(args.only), 'case_timeout_seconds': timeouts,
            'infrastructure_prerequisites':list(INFRASTRUCTURE_PREREQUISITES)}
    executions = [name + '@' + str(repeat + 1) for repeat in range(repetitions) for name in selected]
    record = {'schema': MATRIX_SCHEMA, 'status': 'FAIL', 'plan': plan,
              'scope': 'Actual codec and four-stream controller; fake RF transport, never hardware qualification',
              'radio_accessed': False, 'hardware_qualified': False, 'cases': {}, 'coverage': [],
              'infrastructure_prerequisites':{},
              'complete_matrix': False, 'expected_executions': executions, 'started': [], 'executed': [],
              'not_executed': list(executions), 'skipped': sorted(set(expected) - set(selected))}
    campaign = None
    result_code = 1
    old_handler = signal.signal(signal.SIGTERM, _interrupted)
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True, timeout=10).strip()
        _, tree = source_snapshot()
        if (args.source_commit and args.source_commit != revision) or (args.source_tree_sha256 and args.source_tree_sha256 != tree):
            raise ValueError('Matrix source identity differs from the requested frozen source')
        injector = output / 'usb-audio-fault-inject.so'
        compiler = shutil.which(args.cc)
        if not compiler:
            raise ValueError('Fault-injector compiler is unavailable: ' + args.cc)
        identity = {'source_commit': revision, 'source_tree_sha256': tree, 'build_id': args.build_id,
                    'test_binary_sha256': sha(args.binary.resolve()), 'runner_sha256': sha(args.runner.resolve()),
                    'speech_sha256': sha(args.speech.resolve()), 'modem_sha256': sha(args.modem.resolve()),
                    'rade_library_sha256': sha(args.rade_library.resolve()) if args.rade_library else None,
                    'fault_injector_source_sha256': sha(here/'usb_audio_fault_inject.c'),
                    'compiler_sha256': sha(Path(compiler).resolve()),
                    'pulse_tools_sha256': {name: sha(Path(shutil.which(name)).resolve()) for name in
                        ('pulseaudio','paplay','parec','pactl') if shutil.which(name)},
                    'test_tools_sha256': {name: sha(here / name) for name in
                        ('test_usb_audio_matrix.py', 'test_usb_audio_integration.py', 'usb_test_support.py',
                         'usb_test_campaign.py', 'usb_collector_contract.py', 'usb_rf_evidence.py',
                         'usb_harness_failure_selftest.py',
                         'usb_audio_fault_inject.c', 'usb_audio_integration_test.c')}}
        record.update(identity=identity, source_commit=revision, source_tree_sha256=tree,
                      build_id=args.build_id, test_binary_sha256=identity['test_binary_sha256'])
        campaign = Campaign(args.campaign_root or output.parent / 'usb-campaigns', identity, plan, output, args.campaign_id)
        atomic_json(output / 'attempt.json', {'path': str(campaign.attempt), 'identity_sha256': campaign.identity_sha256})
        compile_result = run_bounded([args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-shared', '-fPIC',
                                      str(here / 'usb_audio_fault_inject.c'), '-lpulse', '-ldl', '-o', str(injector)],
                                     output / 'compile-injector.log', 60)
        record['injector_compilation'] = compile_result
        if compile_result['timed_out'] or compile_result['exit_code'] != 0:
            record['status'] = 'TIMEOUT' if compile_result['timed_out'] else 'FAIL'
            raise ValueError('Fault injector compilation failed or timed out')
        record['fault_injector_sha256'] = sha(injector)
        child_env = dict(os.environ,
                         USB_TEST_EXPECTED_SOURCE_COMMIT=revision,
                         USB_TEST_EXPECTED_BUILD_ID=args.build_id,
                         USB_TEST_EXPECTED_BINARY_SHA256=identity['test_binary_sha256'])
        # This expected-negative checks test infrastructure, not the radio
        # functional case registry. It must finish before any functional case.
        infrastructure = output/'infrastructure'/'unexpected-runtime-failure'
        command = [sys.executable, str(here/'usb_harness_failure_selftest.py'),
                   str(args.binary.resolve()),str(args.speech.resolve()),str(args.modem.resolve()),
                   '--runner',str(args.runner.resolve()),'--fault-inject-so',str(injector),
                   '--output',str(infrastructure),'--source-commit',revision,'--build-id',args.build_id,
                   '--expected-binary-sha256',identity['test_binary_sha256']]
        infrastructure_log = output/'infrastructure-self-test.log'
        supervision = run_bounded(command,infrastructure_log,120,env=child_env,terminate_grace=16.0)
        report_path = infrastructure/'infrastructure-result.json'
        proof = json.loads(report_path.read_text()) if report_path.is_file() else {}
        entry = {'status':'PASS' if not supervision['timed_out'] and supervision['exit_code']==0 and proof.get('status')=='PASS'
                          else 'TIMEOUT' if supervision['timed_out'] else 'FAIL',
                 'record':proof,'record_sha256':digest(proof),
                 'report_path':str(report_path),'report_sha256':sha(report_path) if report_path.is_file() else None,
                 'log_path':str(infrastructure_log),'log_sha256':sha(infrastructure_log),'supervision':supervision}
        record['infrastructure_prerequisites']['unexpected_runtime_failure'] = entry
        atomic_json(output/'progress.json',record)
        if entry['status'] != 'PASS':
            record['status'] = entry['status']
        validate_infrastructure_prerequisites(record)
        record['infrastructure_verified'] = True
        for repeat in range(repetitions):
            repeat_dir = output / ('repeat-%02d' % (repeat + 1))
            repeat_dir.mkdir()
            for name in selected:
                execution = name + '@' + str(repeat + 1)
                case_dir = repeat_dir / name
                command = [sys.executable, str(here / 'test_usb_audio_integration.py'), str(args.binary.resolve()),
                           str(args.speech.resolve()), str(args.modem.resolve()), '--case', name, '--output', str(case_dir),
                           '--runner', str(args.runner.resolve()), '--fault-inject-so', str(injector)]
                print('Running USB integration case:', execution, flush=True)
                log_path = repeat_dir / (name + '.log')
                record['started'].append(execution)
                atomic_json(output / 'progress.json', record)
                outcome = run_bounded(command, log_path, timeouts[name], env=child_env, terminate_grace=8.0)
                details_path = case_dir / 'case-result.json'
                details = json.loads(details_path.read_text()) if details_path.exists() else {}
                passed = not outcome['timed_out'] and outcome['exit_code'] == 0 and details.get('status') == 'PASS'
                if passed and (details.get('case') != name or details.get('test_binary_sha256') != identity['test_binary_sha256']):
                    raise ValueError('Case result belongs to a different case/binary: ' + execution)
                run = {'status': 'PASS' if passed else ('TIMEOUT' if outcome['timed_out'] else 'FAIL'),
                       'repeat': repeat + 1, 'exit_code': outcome['exit_code'], 'supervision': outcome,
                       'log_path': str(log_path), 'log_sha256': sha(log_path),
                       'case_record_path': str(details_path),
                       'case_record_sha256': sha(details_path) if details_path.exists() else None,
                       'harness': details.get('harness'), 'identity': details.get('identity'),
                       'collector_contract':details.get('collector_contract'),
                       'audio_buffer_contract':details.get('audio_buffer_contract'),
                       'io_service_pause':details.get('io_service_pause'),
                       'actual_headset_output':details.get('actual_headset_output'),
                       'tail_backpressure':details.get('tail_backpressure'),
                       'capture_close':details.get('capture_close'),
                       'capture_source_boundary':details.get('capture_source_boundary'),
                       'capture_close_failure':details.get('capture_close_failure'),
                       'actual_sink_turns': details.get('actual_sink_turns', []),
                       'decoded_speech_blocks': details.get('actual_sink_decode', {}).get('synthesis_blocks', 0)}
                if passed:
                    expected_identity = {'status':'PASS', 'source_commit':revision, 'build_id':args.build_id,
                                         'binary_sha256':identity['test_binary_sha256']}
                    if any(details.get('identity', {}).get(key) != value for key,value in expected_identity.items()):
                        run['status'] = 'FAIL'
                        run['reason'] = 'Case did not verify the expected build/source/binary identity'
                    expected_inputs = {'speech':identity['speech_sha256'], 'modem':identity['modem_sha256']}
                    if (details.get('fixture_sha256') != expected_inputs or
                            details.get('runner_sha256') != identity['runner_sha256'] or
                            details.get('fault_injector_sha256') != record['fault_injector_sha256']):
                        run['status'] = 'FAIL'
                        run.setdefault('reason', 'Case fixture/runner/injector identity differs from the campaign')
                if passed and name in SPEECH_CASES:
                    turns = run['actual_sink_turns']
                    if (run['decoded_speech_blocks'] <= 0 or not turns or
                            any(turn.get('status') != 'PASS' for turn in turns)):
                        run['status'] = 'FAIL'
                        run.setdefault('reason', 'Required per-turn actual sink decoding evidence is missing')
                if passed and name in {'io-pause150','rx-pause150','buffer-contract-reject','short-turn200'}:
                    expected_budget = 'FAIL' if name == 'buffer-contract-reject' else 'PASS'
                    if (run['audio_buffer_contract'] or {}).get('status') != expected_budget:
                        run['status'] = 'FAIL'
                        run.setdefault('reason', 'Negotiated buffer contract proof is missing')
                    if name in {'io-pause150','rx-pause150'} and (run['io_service_pause'] or {}).get('count') != 1:
                        run['status'] = 'FAIL'
                        run.setdefault('reason', 'Controlled servicing pause proof is missing')
                    if name == 'rx-pause150' and (run['actual_headset_output'] or {}).get('status') != 'PASS':
                        run['status'] = 'FAIL'
                        run.setdefault('reason', 'Complete actual receive headset output proof is missing')
                    if name == 'short-turn200' and (not run['actual_sink_turns'] or
                            any(turn.get('status') != 'PASS' for turn in run['actual_sink_turns'])):
                        run['status'] = 'FAIL'
                        run.setdefault('reason', 'Complete short-payload delivery proof is missing')
                if passed:
                    try:
                        validate_capture_close_run(run)
                    except ValueError as error:
                        run['status'] = 'FAIL'
                        run.setdefault('reason',str(error))
                if passed and name in ('capture-release-split','capture-release-held150'):
                    try:
                        validate_capture_source_boundary_run(run,name)
                    except ValueError as error:
                        run['status'] = 'FAIL'
                        run.setdefault('reason',str(error))
                if passed and name in ('capture-close-missing','capture-close-invalid','capture-close-stop'):
                    try:
                        validate_capture_close_failure_run(run,name)
                    except ValueError as error:
                        run['status'] = 'FAIL'
                        run.setdefault('reason',str(error))
                if passed and name == 'eoo-backpressure':
                    try:
                        validate_tail_backpressure_run(run)
                    except ValueError as error:
                        run['status'] = 'FAIL'
                        run.setdefault('reason',str(error))
                contract = run['collector_contract'] or {}
                mutations = contract.get('mutations', {})
                if passed and (contract.get('status') != 'PASS' or contract.get('radio_accessed') is not False or
                        contract.get('source_commit') != revision or contract.get('build_id') != args.build_id or
                        contract.get('controller_binary_sha256') != identity['test_binary_sha256'] or
                        set(mutations) != {'missing-completion','truncated-completion','truncated-trace'} or
                        any(value.get('status') != 'PASS' for value in mutations.values())):
                    run['status'] = 'FAIL'
                    run.setdefault('reason', 'Actual C-to-collector contract proof is missing, incomplete or mismatched')
                aggregate = record['cases'].setdefault(name, {'status': 'PASS', 'runs': []})
                aggregate['runs'].append(run)
                aggregate.update(harness=run['harness'], decoded_speech_blocks=run['decoded_speech_blocks'])
                if run['status'] != 'PASS':
                    aggregate['status'] = run['status']
                record['executed'].append(execution)
                record['not_executed'].remove(execution)
                atomic_json(output / 'progress.json', record)
                if run['status'] != 'PASS':
                    record['status'] = run['status']
                    raise RuntimeError('USB integration case failed: ' + execution + '; inspect ' + str(log_path))
        passed = {name for name, case in record['cases'].items()
                  if case['status'] == 'PASS' and len(case['runs']) == repetitions}
        record['coverage'] = sorted([name for name, cases in COVERAGE.items() if cases <= passed] +
                                    ['infrastructure_failure_preservation'])
        _, after_tree = source_snapshot()
        rechecked = {'test_binary_sha256':sha(args.binary.resolve()), 'runner_sha256':sha(args.runner.resolve()),
                     'speech_sha256':sha(args.speech.resolve()), 'modem_sha256':sha(args.modem.resolve()),
                     'rade_library_sha256':sha(args.rade_library.resolve()) if args.rade_library else None}
        after_revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=repository,text=True,timeout=10).strip()
        if after_revision != revision or after_tree != tree or any(identity[key] != value for key,value in rechecked.items()) or sha(injector) != record['fault_injector_sha256']:
            raise ValueError('Source, binary, fixture, injector or codec changed during the USB campaign')
        record['inputs_rechecked_unchanged'] = True
        record['complete_matrix'] = not args.only and selected == expected and not record['not_executed'] and passed == set(expected)
        record['status'] = 'PASS'
        result_code = 0
    except KeyboardInterrupt as error:
        record.update(status='INTERRUPTED', reason=str(error))
        result_code = 130
    except Exception as error:
        record['reason'] = str(error)
        print('FAIL:', str(error), flush=True)
    finally:
        record['not_started'] = [item for item in executions if item not in record['started']]
        record['not_completed'] = list(record['not_executed'])
        remaining = {item.rsplit('@', 1)[0] for item in record['not_executed']}
        record['skipped'] = sorted(set(record['skipped']) | remaining)
        if campaign:
            try:
                snapshot = campaign.finish(record)
                record = {**record, 'campaign': snapshot, 'promotion_eligible': snapshot['promotion_eligible']}
                if args.profile == 'qualification' and not args.only:
                    validate_campaign(record, expected_cases=expected)
                if not record['promotion_eligible'] and args.profile == 'qualification' and not args.only:
                    result_code = 1
            except (OSError, ValueError, TypeError, KeyError) as error:
                record['promotion_eligible'] = False
                record['promotion_error'] = str(error)
                result_code = 1
            finally:
                campaign.close()
        else:
            record['promotion_eligible'] = False
        atomic_json(output / 'matrix-result.json', record)
        signal.signal(signal.SIGTERM, old_handler)
    if result_code == 0:
        print('PASS:', len(record['executed']), 'USB executions; promotion eligible:', record['promotion_eligible'], flush=True)
    return result_code


if __name__ == '__main__':
    raise SystemExit(main())
