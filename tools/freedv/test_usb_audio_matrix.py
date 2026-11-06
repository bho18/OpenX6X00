#!/usr/bin/env python3
"""Run the production USB controller's realistic, fault-injected host matrix.

Every case uses the same four-stream controller and actual codec. All radio
control is a fake transport; this is software evidence, not RF qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from test_usb_audio_integration import CASES, SPEECH_CASES

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
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('speech', type=Path)
    parser.add_argument('modem', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--only', choices=CASES, action='append', help='Debug subset; never qualifies as the complete matrix')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    injector = output / 'usb-audio-fault-inject.so'
    record = {'schema': 'x6200.usb.integration.matrix.v2', 'status': 'FAIL',
              'scope': 'Actual codec and four-stream controller, representative audio rates and adversarial fake transport; no radio RF',
              'radio_accessed': False, 'hardware_qualified': False, 'cases': {}, 'coverage': [],
              'complete_matrix': not args.only, 'skipped': [], 'test_binary_sha256': sha(args.binary.resolve())}
    selected = args.only or CASES
    record['skipped'] = sorted(set(CASES) - set(selected))
    try:
        with (output / 'compile-injector.log').open('w') as log:
            subprocess.run([args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-shared', '-fPIC',
                            str(here / 'usb_audio_fault_inject.c'), '-lpulse', '-ldl', '-o', str(injector)],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
        record['fault_injector_sha256'] = sha(injector)
        for name in selected:
            case_dir = output / name
            command = [sys.executable, str(here / 'test_usb_audio_integration.py'), str(args.binary.resolve()),
                       str(args.speech.resolve()), str(args.modem.resolve()), '--case', name, '--output', str(case_dir),
                       '--runner', str(args.runner.resolve()), '--fault-inject-so', str(injector)]
            print('Running USB integration case:', name, flush=True)
            with (output / (name + '.log')).open('w') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            details = json.loads((case_dir / 'case-result.json').read_text()) if (case_dir / 'case-result.json').exists() else {}
            record['cases'][name] = {'status': 'PASS' if result.returncode == 0 and details.get('status') == 'PASS' else 'FAIL',
                                    'exit_code': result.returncode, 'log_sha256': sha(output / (name + '.log')),
                                    'case_record_sha256': sha(case_dir / 'case-result.json') if details else None,
                                    'harness': details.get('harness'),
                                    'decoded_speech_blocks': details.get('actual_sink_decode', {}).get('synthesis_blocks', 0)}
            if record['cases'][name]['status'] != 'PASS':
                raise RuntimeError('USB integration case failed: ' + name + '; inspect ' + str(output / (name + '.log')))
            if name in SPEECH_CASES:
                assert record['cases'][name]['decoded_speech_blocks'] > 0, name
        passed = {name for name, case in record['cases'].items() if case['status'] == 'PASS'}
        record['coverage'] = sorted(name for name, cases in COVERAGE.items() if cases <= passed)
        record['status'] = 'PASS'
        print('PASS:', len(passed), 'USB controller cases;', ', '.join(record['coverage']), flush=True)
    except Exception as error:
        record['reason'] = str(error)
        print('FAIL:', str(error), flush=True)
    finally:
        (output / 'matrix-result.json').write_text(json.dumps(record, indent=2) + '\n')
    return 0 if record['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
