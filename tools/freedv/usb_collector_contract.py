#!/usr/bin/env python3
"""Test real controller evidence against the production collector, without RF.

Only inventory, host diagnostic commands and filesystem roots are adapted.
The runtime pointer, trace, atomic fault/completion and incident records come
from the executed C controller. Mutation checks operate on isolated copies.
"""
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import usb_rf_evidence as collector


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def collect_runtime(runtime, output, binary, manifest):
    output.mkdir(parents=True)
    proc = output / 'empty-proc'
    proc.mkdir()
    mapping = {'/tmp':runtime, '/proc':proc, '/mnt/freedv-usb-incidents':runtime/'incidents'}

    def inventory(**kwargs):
        require(kwargs['expected_gui'] == sha(binary), 'Collector identity adapter observed a changed controller binary')
        return dict(status='USB_DUPLEX_CANDIDATE', gui_sha256=kwargs['expected_gui'],
                    scope='Simulated endpoint inventory; actual C controller binary identity; no radio')

    def diagnostic(args, **kwargs):
        return dict(command=list(args), exit_code=127, stdout='', stderr='Host collector contract adapter; no radio diagnostics',
                    timed_out=False, output_truncated=False)

    with patch.object(collector, 'Path', side_effect=lambda value:mapping.get(str(value), Path(value))), \
            patch.object(collector, 'collect', side_effect=inventory), patch.object(collector, 'command', side_effect=diagnostic):
        return collector.collect_evidence(output, manifest)


def validate_collector_contract(runtime_dir, output_dir, binary, expected_source_commit,
                                expected_build_id, expected_binary_sha256, storage_failure_expected=False):
    runtime, output, binary = Path(runtime_dir), Path(output_dir), Path(binary)
    require(not output.exists(), 'Collector contract output must be a fresh attempt directory')
    require(sha(binary) == expected_binary_sha256, 'Collector contract received the wrong controller binary')
    pointer = json.loads((runtime/'freedv-usb-current.json').read_text())
    trace = runtime/pointer['trace_basename']
    completion_path = runtime/pointer['completion_basename']
    completion = json.loads(completion_path.read_text())
    rows = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    samples = [row for row in rows if row.get('kind') == 'sample']
    require(samples, 'Actual C controller produced no state samples for the collector contract')
    original = {str(path.relative_to(runtime)):sha(path) for path in runtime.rglob('*')
                if path.is_file() and path.suffix in ('.json', '.jsonl')}
    sessions = [row for row in rows if row.get('kind') == 'session']
    require(len(sessions) == 1, 'Collector contract needs exactly one controller session')
    operational = sessions[0].get('operational') is True
    manifest = dict(usb_startup_contract_version=4 if operational else 3, usb_output_contract_version=1, on_device_readiness_required=True,
                    firmware_commit=expected_source_commit, build_id=expected_build_id,
                    gui_sha256=expected_binary_sha256)
    output.mkdir(parents=True)
    result = collect_runtime(runtime, output/'actual', binary, manifest)
    last = samples[-1]
    if storage_failure_expected:
        require(result['status'] == 'BLOCKED' and type(completion.get('storage_errno')) is int and
                completion['storage_errno'] > 0, 'Storage fault was not retained as incomplete collector evidence')
    else:
        require(result['stages'].get('session', {}).get('status') == 'COMPLETE',
                'Production collector rejected actual C session/terminal evidence: '+str(result.get('reason')))
    if last['fault'] > 1:
        require(result['status'] == 'BLOCKED' and result.get('first_gui_fault', {}).get('code') == last['fault'],
                'Production collector lost the actual C first fault')
    elif not storage_failure_expected:
        expected = ('READY_FOR_RF_TEST' if last['rf_disabled'] and last['qualification_complete'] else
                    'COLLECTED_NO_SDR_RECORDING' if last['qualification_complete'] or operational else 'BLOCKED')
        require(result['status'] == expected, 'Production collector misclassified actual controller result: '+str(result))
    require(result['rf_tested'] is False and result['rf_recording_decoded'] is False,
            'Simulated controller evidence was promoted to RF validation')
    mutations = {}
    for name in ('missing-completion', 'truncated-completion', 'truncated-trace'):
        mutated = output/name/'runtime'
        mutated.mkdir(parents=True)
        for path in runtime.iterdir():
            if path.is_file() and path.suffix in ('.json', '.jsonl'):
                shutil.copy2(path, mutated/path.name)
        if name == 'missing-completion':
            (mutated/completion_path.name).unlink()
        elif name == 'truncated-completion':
            data = (mutated/completion_path.name).read_bytes()
            (mutated/completion_path.name).write_bytes(data[:max(1,len(data)//2)])
        else:
            lines = (mutated/trace.name).read_text().splitlines()
            terminal = max(index for index,line in enumerate(lines) if json.loads(line).get('kind') == 'sample')
            del lines[terminal]
            (mutated/trace.name).write_text('\n'.join(lines)+'\n')
        rejected = collect_runtime(mutated, output/name/'collected', binary, manifest)
        require(rejected['status'] == 'BLOCKED' and rejected['ready_for_rf_test'] is False and
                any(item['stage'] == 'collection' for item in rejected['blockers']),
                'Production collector accepted incomplete actual C evidence: '+name)
        mutations[name] = dict(status='PASS', collector_status=rejected['status'],
                               report_sha256=sha(output/name/'collected/rf-evidence.json'))
    after = {str(path.relative_to(runtime)):sha(path) for path in runtime.rglob('*')
             if path.is_file() and path.suffix in ('.json', '.jsonl')}
    require(original == after, 'Collector contract checks modified original controller evidence')
    require(sha(binary) == expected_binary_sha256, 'Controller binary changed during collector contract checks')
    record = dict(status='PASS', schema='x6200.usb.collector-contract.v1',
                  scope='Actual C evidence producer and production Python collector; simulated inventory and RF only',
                  radio_accessed=False, source_commit=expected_source_commit, build_id=expected_build_id,
                  controller_binary_sha256=expected_binary_sha256, session_id=pointer['session_id'],
                  collector_status=result['status'], actual_report_sha256=sha(output/'actual/rf-evidence.json'),
                  actual_trace_sha256=sha(trace), actual_completion_sha256=sha(completion_path),
                  storage_failure_expected=storage_failure_expected, mutations=mutations)
    (output/'collector-contract.json').write_text(json.dumps(record,indent=2)+'\n')
    return record
