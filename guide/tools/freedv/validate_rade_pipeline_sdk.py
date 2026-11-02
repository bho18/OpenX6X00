#!/usr/bin/env python3
"""Mandatory matching-SDK/QEMU functional gate for the optional RX pipeline."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

from pipeline_evidence import strict_json, validate_pipeline_result, compare_pipeline_pair, integer
from validate_rade_acq_sdk import validate_rade_stage_timing


def compare_original_stage(serial, original, *, workload):
    if not isinstance(original, dict):
        raise ValueError('original serial benchmark output must be an object')
    if (type(original.get('schema_version')) is not int or original['schema_version'] != 1 or
            (original.get('mode'), original.get('direction'), original.get('pacing'), original.get('workload')) !=
            ('RADE-V1', 'rx', 'unpaced', workload) or original.get('radio_io') is not False or
            original.get('full_pipeline_qualified') is not False or original.get('resampling_included') is not True or
            original.get('deadline_misses') is not None):
        raise ValueError('original serial benchmark identity or unpaced semantics differ')
    accounting = validate_rade_stage_timing(original)
    counters = ('frames', 'output_samples', 'resampled_output_samples', 'sync_blocks', 'feature_blocks',
                'nonzero_output_samples', 'interrupted_input_samples', 'sync_blocks_after_first_interrupt')
    for key in counters:
        integer(original.get(key), 'original.' + key)
        if original[key] != serial['measured'][key]:
            raise ValueError('serial refactor changed original functional counter: ' + key)
    for field, expected in [('audio_seconds', serial['measured']['audio_ns']),
                            ('warmup_audio_seconds', serial['warmup_audio_ns'])]:
        value = original.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or round(value * 1e9) != expected:
            raise ValueError('serial refactor changed original represented duration: ' + field)
    return {'status': 'MATCH', 'functional_counter_names': list(counters),
            'measured_and_warmup_durations_match': True, 'original_stage_accounting': accounting,
            'original_70_percent_assessment': 'unchanged_serial_reference_required'}


def run_validation(package, output, prefix, *, environment):
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    paired = {}
    for name, fixture, seconds, workload in [('sync', 'rade-modem', 3, 'continuous'),
                                           ('silence', 'silence', 3, 'continuous'),
                                           ('interrupted', 'rade-modem', 14, 'interrupt')]:
        paired[name] = {}
        for execution in ('serial', 'parallel'):
            label = name + '-' + execution
            command = [*prefix, str(package / 'bin/rade-pipeline-bench'), execution,
                       str(package / 'fixtures' / (fixture + '.raw')), str(seconds), 'unpaced', '1', workload]
            result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
            (output / (label + '.stdout')).write_text(result.stdout)
            (output / (label + '.stderr')).write_text(result.stderr)
            if result.returncode:
                raise RuntimeError(label + ': failed with exit ' + str(result.returncode) + '\n' + result.stderr[-2000:])
            value = strict_json(result.stdout)
            validate_pipeline_result(value, execution=execution, pacing='unpaced', workload=workload,
                                     seconds=seconds, warmup=1, fixture_name=fixture)
            paired[name][execution] = value
            cases.append({'case': label, 'passed': True})
        comparison = compare_pipeline_pair(paired[name]['serial'], paired[name]['parallel'])
        if comparison['status'] != 'MATCH':
            raise ValueError(name + ': serial and parallel functional evidence differs')
        cases.append({'case': name + '-comparison', **comparison})
        label = name + '-original-stage'
        command = [*prefix, str(package / 'bin/rade-frame-bench'), 'rx',
                   str(package / 'fixtures' / (fixture + '.raw')), str(seconds), 'unpaced', '1', workload]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        (output / (label + '.stdout')).write_text(result.stdout)
        (output / (label + '.stderr')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(label + ': failed with exit ' + str(result.returncode) + '\n' + result.stderr[-2000:])
        original = strict_json(result.stdout)
        original_comparison = compare_original_stage(paired[name]['serial'], original, workload=workload)
        cases.append({'case': label, 'passed': True, **original_comparison})
    for name, option, reason in [('queue-full', ['--test-hold-b-until-full'], 'queue_full'),
                                 ('worker-failure', ['--test-fail-b-after', '0'], 'injected_worker_b_failure')]:
        command = [*prefix, str(package / 'bin/rade-pipeline-bench'), 'parallel',
                   str(package / 'fixtures/rade-modem.raw'), '3', 'unpaced', '1', 'continuous', *option]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        (output / (name + '.stdout')).write_text(result.stdout)
        (output / (name + '.stderr')).write_text(result.stderr)
        if result.returncode != 3:
            raise ValueError(name + ': injection did not return the expected failure')
        value = strict_json(result.stdout)
        validate_pipeline_result(value, execution='parallel', pacing='unpaced', workload='continuous',
                                 seconds=3, warmup=1, fixture_name='rade-modem',
                                 require_complete=False, allow_test_hooks=True)
        if value['execution_complete'] or value['failure_reason'] != reason or not value['test_hooks_used']:
            raise ValueError(name + ': invalid controlled failure evidence')
        cases.append({'case': name, 'expected_failure_verified': True, 'workers_joined': True})
    summary = {'schema_version': 1, 'experiment': 'rade-rx-two-worker',
               'environment': environment,
               'radio_hardware_tested': False, 'timing_qualifies_radio': False,
               'original_70_percent_assessment': 'unchanged_serial_reference_required',
               'paired_workloads_checked': 3, 'original_serial_counter_comparisons': 3,
               'controlled_failures_checked': 2,
               'fixture_sha256': hashlib.sha256((package / 'fixtures/rade-modem.raw').read_bytes()).hexdigest(),
               'cases': cases}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('PASS: pipeline execution/equivalence and controlled failures in ' + environment + '; no target timing qualification')
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--compiler', type=Path, required=True)
    p.add_argument('--sysroot', type=Path, required=True)
    a = p.parse_args()
    triplet = subprocess.check_output([str(a.compiler.absolute()), '-dumpmachine'], text=True).strip()
    if not triplet.startswith('arm') or 'buildroot' not in triplet:
        p.error('matching ARM Buildroot compiler required')
    qemu = shutil.which('qemu-arm')
    if not qemu:
        p.error('qemu-arm required; SDK validation cannot be skipped')
    package = a.work.resolve() / 'package'
    sysroot = a.sysroot.resolve()
    libraries = ':'.join(map(str, [package / 'lib', sysroot / 'usr/lib', sysroot / 'lib']))
    run_validation(package, a.work.resolve() / 'pipeline-sdk-validation',
                   [qemu, '-cpu', 'cortex-a7', '-L', str(sysroot),
                    '-E', 'LD_LIBRARY_PATH=' + libraries],
                   environment='matching Buildroot SDK under QEMU cortex-a7')


if __name__ == '__main__':
    main()
