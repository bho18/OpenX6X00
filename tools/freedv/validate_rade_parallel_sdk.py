#!/usr/bin/env python3
"""Mandatory matching-SDK gates for optional acquisition worker scaling.

The original default-one-worker V1 loss/speech/text gate remains separate.
This gate exercises actual parallel detector/receiver paths and full FARGAN
fixture pipelines with 1..4 workers. QEMU timings never qualify radio capacity.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess

from acquisition_evidence import validate_acquisition_result, compare_acquisition_results, uint
from validate_rade_acq_sdk import EXPECTED_DIFFERENTIAL_CASES


RADE_SOURCES = {
    'rade_acq.c', 'rade_acq_parallel.c', 'rade_api.c', 'rade_bpf.c', 'rade_dec.c',
    'rade_dec_data.c', 'rade_dec_v2.c', 'rade_dec_v2_data.c', 'rade_dsp.c',
    'rade_enc.c', 'rade_enc_data.c', 'rade_enc_v2.c', 'rade_enc_v2_data.c',
    'rade_ofdm.c', 'rade_rx.c', 'rade_rx_v2.c', 'rade_sync.c', 'rade_sync_data.c',
    'rade_tx.c', 'rade_tx_v2.c', 'rade_v2_ofdm.c',
}
STREAM_CASE_CALLS = {
    'digital-zero': 5, 'seeded-nonzero-noise': 6, 'seeded-noise-bpf': 5,
    'reset-and-shifts': 12, 'configuration-invalidation': 10,
    'retained-mutation-and-tail-dimensions': 12, 'numeric-transitions': 18,
    'FP-flush-control-transition': 6, 'acquisition-reinitialization': 6,
    'standalone-detector': 5, 'interleaved-receiver-instances': 8,
    'coarse-pilot-timing-0': 6, 'coarse-pilot-timing-959': 6,
    'valid-modem-and-recovery': 74, 'low-normal-numeric-domain': 4,
    'high-level-noise': 4, 'weak-between-bins': 20,
    'weak-upper-bin-timing-boundary': 20, 'strong-lower-bin': 20,
}
LIFECYCLE_CASES = {
    'null-public-arguments': 5, 'v2-api-rejected': 7, 'invalid-private-counts': 3,
    'private-close-before-run': 16, 'public-pre-rx-reconfigure': 26,
    'create-failure-after-0': 8, 'create-failure-after-1': 8, 'create-failure-after-2': 8,
    'public-after-rx-busy': 13, 'multiple-live-instances': 16, 'repeated-open-close': 20,
}
GUARDED_ACQ_SHA = '28f72e33aa6ad70cdad2ad0e311651e9298b03abbff93c8de50083097e0f6090'
ORIGINAL_RX_SHA = '4759c931d47305ca8778619e1d2f45089094b0890f17f393da9e0ed2214495c5'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strict_json(text):
    def reject(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(text, parse_constant=reject, object_pairs_hook=pairs)


def json_rows(text):
    return [strict_json(line) for line in text.splitlines() if line.strip()]


def complete_rows(rows, expected):
    if type(rows) is not list or len(rows) != len(expected) + 1 or any(type(r) is not dict for r in rows):
        raise ValueError('complete named case rows and terminal summary required')
    if rows[-1].get('summary') is not True or any('summary' in r for r in rows[:-1]):
        raise ValueError('exactly one terminal summary required')
    names = [r.get('case') for r in rows[:-1]]
    if any(type(name) is not str for name in names) or len(set(names)) != len(expected) or set(names) != set(expected):
        raise ValueError('missing, duplicate or unexpected cases')
    return rows[:-1], rows[-1]


def validate_detector_rows(rows, *, workers):
    if type(workers) is not int or workers not in (1, 2, 3, 4):
        raise ValueError('worker count must be 1..4')
    cases, summary = complete_rows(rows, EXPECTED_DIFFERENTIAL_CASES)
    if summary.get('workers') != workers or summary.get('radio_qualified') is not False:
        raise ValueError('detector worker count/qualification differs')
    for row in cases:
        if type(row.get('workers')) is not int or row['workers'] != workers:
            raise ValueError('detector case worker count differs')
        for name in ('bit_exact', 'decision_equal', 'path_expected'):
            if row.get(name) is not True:
                raise ValueError('detector comparison failed: ' + str(row.get('case')))
        for name in ('grid_bit_differences', 'grid_value_differences'):
            if uint(row.get(name), name):
                raise ValueError('detector grid mismatch')
        for name in ('max_absolute_grid_difference', 'rms_grid_difference'):
            value = row.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or value != 0:
                raise ValueError('detector grid error is nonzero/nonfinite')
        for name in ('neon_used', 'expected_neon', 'parallel_used', 'expected_parallel'):
            if type(row.get(name)) is not bool:
                raise ValueError('detector path evidence missing')
        if row['neon_used'] != row['expected_neon'] or row['parallel_used'] != row['expected_parallel']:
            raise ValueError('detector selected an unexpected path')
        nmf = uint(row.get('nmf'), 'nmf')
        if not nmf or not uint(row.get('nfreq'), 'nfreq'):
            raise ValueError('detector dimensions missing')
        parallel = row['neon_used'] and workers > 1 and nmf % 4 == 0 and nmf // 4 >= workers
        if row['parallel_used'] != parallel:
            raise ValueError('parallel selection disagrees with vector domain and group count')
        for suffix in ('detection', 't', 'f', 'threshold', 'max'):
            left, right = 'reference_' + suffix, 'candidate_' + suffix
            if left not in row or right not in row or row[left] != row[right]:
                raise ValueError('detector selected metric differs')
    for field in ('cases', 'strict_failures', 'path_failures', 'parallel_cases', 'neon_cases', 'scalar_cases'):
        uint(summary.get(field), field)
    if summary['cases'] != 47 or summary['strict_failures'] or summary['path_failures']:
        raise ValueError('detector case coverage/comparison failed')
    if (summary['neon_cases'] != sum(row['neon_used'] for row in cases) or
            summary['scalar_cases'] != sum(not row['neon_used'] for row in cases) or
            summary['parallel_cases'] != sum(row['parallel_used'] for row in cases)):
        raise ValueError('detector path totals differ')
    if summary['neon_cases'] != 34 or summary['scalar_cases'] != 13:
        raise ValueError('SDK detector must exercise all 34 vector and 13 scalar fallback cases')
    if (workers == 1 and summary['parallel_cases']) or (workers > 1 and not summary['parallel_cases']):
        raise ValueError('requested detector helper count did not execute')
    return dict(summary)


def validate_stream_rows(rows, *, workers):
    cases, summary = complete_rows(rows, STREAM_CASE_CALLS)
    if (summary.get('workers') != workers or summary.get('quick') is not False or
            summary.get('radio_qualified') is not False):
        raise ValueError('complete receiver corpus with requested workers required')
    for row in cases:
        for name in ('receiver_calls', 'parallel_calls', 'feature_calls', 'sync_entries', 'failures'):
            uint(row.get(name), name)
        if row['receiver_calls'] != STREAM_CASE_CALLS[row['case']] or row['failures']:
            raise ValueError('receiver case coverage or comparison failed')
        if any(row[name] > row['receiver_calls'] for name in ('parallel_calls', 'feature_calls', 'sync_entries')):
            raise ValueError('receiver counters exceed case calls')
    for name in ('receiver_calls', 'search_calls', 'sync_calls', 'parallel_calls', 'feature_calls', 'states_seen', 'failures'):
        uint(summary.get(name), name)
    if summary['receiver_calls'] != 247 or summary['failures'] or summary['states_seen'] != 7:
        raise ValueError('full receiver coverage, states or equality failed')
    if summary['search_calls'] + summary['sync_calls'] != summary['receiver_calls']:
        raise ValueError('receiver state population does not account for calls')
    for name in ('receiver_calls', 'parallel_calls', 'feature_calls', 'failures'):
        if summary[name] != sum(row[name] for row in cases):
            raise ValueError('receiver case and summary totals differ')
    if (workers == 1 and summary['parallel_calls']) or (workers > 1 and not summary['parallel_calls']):
        raise ValueError('requested receiver helpers did not execute')
    if not summary['feature_calls'] or not summary['sync_calls'] or not summary['search_calls']:
        raise ValueError('receiver acquisition/synchronized decode did not execute')
    recovery = next(row for row in cases if row['case'] == 'valid-modem-and-recovery')
    if recovery['sync_entries'] < 2 or not recovery['feature_calls']:
        raise ValueError('receiver did not acquire and recover after interruption')
    return dict(summary)


def compare_stream_rows(reference, candidate):
    """Require same-SDK reference state counts, without pinning GCC14 outcomes."""
    ignored = {'parallel_calls', 'workers'}
    left = [{k: v for k, v in row.items() if k not in ignored} for row in reference]
    right = [{k: v for k, v in row.items() if k not in ignored} for row in candidate]
    if left != right:
        raise ValueError('receiver corpus state/feature totals differ from same-SDK one-worker run')


def validate_lifecycle_rows(rows):
    cases, summary = complete_rows(rows, LIFECYCLE_CASES)
    for row in cases:
        if uint(row.get('checks'), 'checks') != LIFECYCLE_CASES[row['case']] or uint(row.get('failures'), 'failures'):
            raise ValueError('lifecycle checks missing or failed')
    if (summary.get('radio_qualified') is not False or summary.get('cases') != len(LIFECYCLE_CASES) or
            uint(summary.get('failures'), 'failures') or uint(summary.get('live_helpers'), 'live_helpers') or
            uint(summary.get('checks'), 'checks') != sum(row['checks'] for row in cases)):
        raise ValueError('lifecycle summary or final helper cleanup failed')
    return dict(summary)


def validate_default_off_rows(rows):
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise ValueError('default-OFF API summary required')
    summary = rows[0]
    if (summary.get('summary') is not True or summary.get('experiment_enabled') is not False or
            summary.get('radio_qualified') is not False or
            uint(summary.get('checks'), 'checks') != 14 or uint(summary.get('failures'), 'failures')):
        raise ValueError('default-OFF API checks did not complete')
    return dict(summary)


def validate_layout_commands(rows, source):
    source = Path(source).resolve()
    reviewed = []
    if type(rows) is not list:
        raise ValueError('compilation database must be a list')
    for row in rows:
        if type(row) is not dict or type(row.get('directory')) is not str:
            raise ValueError('invalid compiler command entry')
        args = row.get('arguments')
        if args is None:
            args = shlex.split(row.get('command', ''))
        if type(args) is not list or not args or any(type(v) is not str for v in args):
            raise ValueError('compiler arguments required')
        if not any('CMakeFiles/rade.dir/' in value for value in args):
            continue
        file = Path(row.get('file', ''))
        if not file.is_absolute(): file = Path(row['directory']) / file
        file = file.resolve()
        if file.parent != source or file.name not in RADE_SOURCES or args.count('-c') != 1:
            raise ValueError('unexpected RADE compilation source')
        compiled = Path(args[args.index('-c') + 1])
        if not compiled.is_absolute(): compiled = Path(row['directory']) / compiled
        if compiled.resolve() != file:
            raise ValueError('declared and compiled source differ')
        definitions = [arg[2:] for arg in args if arg.startswith('-D')]
        if [d for d in definitions if d.split('=')[0] == 'RADE_ACQ_PARALLEL'] != ['RADE_ACQ_PARALLEL=1']:
            raise ValueError('inconsistent public API handle layout')
        if any(d.startswith('RADE_ACQ_TEST_HOOKS') for d in definitions):
            raise ValueError('test hooks enabled in production library')
        rename = [d for d in definitions if d.startswith('rade_acq_')]
        expected = ['rade_acq_detect_pilots=rade_acq_detect_pilots_serial'] if file.name == 'rade_acq.c' else []
        if rename != expected:
            raise ValueError('serial-detector rename is not source-local')
        reviewed.append(file.name)
    if len(reviewed) != len(RADE_SOURCES) or set(reviewed) != RADE_SOURCES:
        raise ValueError('all RADE library sources must be checked exactly once')
    return sorted(reviewed)


def validate_prior_gates(acquisition, pipeline):
    if (acquisition.get('radio_hardware_tested') is not False or acquisition.get('timing_qualifies_radio') is not False or
            acquisition.get('tested_cases_bit_exact') is not True or
            acquisition.get('neon_and_scalar_fallback_exercised') is not True or
            acquisition.get('v1_text_cases_passed') != 6 or acquisition.get('feature_values_finite') is not True or
            acquisition.get('upstream_v1_loss_passed') is not True or acquisition.get('upstream_v1_loss_threshold') != 0.119 or
            acquisition.get('rade_stage_timing_checked') is not True or acquisition.get('rade_stage_timing_workloads_checked') != 4):
        raise ValueError('original acquisition/V1/stage gates did not complete')
    loss = acquisition.get('upstream_v1_reported_loss_rounded')
    if type(loss) not in (int, float) or not math.isfinite(loss) or not 0 <= loss <= 0.119:
        raise ValueError('original V1 loss is invalid')
    for variant in ('default-off', 'neon'):
        value = acquisition.get('differential', {}).get(variant, {})
        if value.get('cases') != 47 or value.get('strict_failures') != 0 or value.get('path_failures') != 0:
            raise ValueError('original 47-case differential gate did not complete')
    bench = acquisition.get('benchmark_cases')
    if type(bench) is not list or len(bench) != 7 or any(row.get('passed') is not True for row in bench):
        raise ValueError('original codec benchmark gates did not complete')
    if (pipeline.get('radio_hardware_tested') is not False or pipeline.get('timing_qualifies_radio') is not False or
            pipeline.get('paired_workloads_checked') != 3 or pipeline.get('original_serial_counter_comparisons') != 3 or
            pipeline.get('controlled_failures_checked') != 2):
        raise ValueError('original pipeline gates did not complete')
    expected = {label + '-' + suffix for label in ('sync', 'silence', 'interrupted')
                for suffix in ('serial', 'parallel', 'comparison', 'original-stage')} | {'queue-full', 'worker-failure'}
    cases = pipeline.get('cases')
    if type(cases) is not list or len(cases) != len(expected) or {row.get('case') for row in cases} != expected:
        raise ValueError('original pipeline gate case coverage differs')
    for row in cases:
        if row['case'] in ('queue-full', 'worker-failure'):
            if row.get('expected_failure_verified') is not True or row.get('workers_joined') is not True:
                raise ValueError('original controlled failure gate failed')
        elif row['case'].endswith('-comparison'):
            if row.get('status') != 'MATCH': raise ValueError('original pipeline comparison failed')
        elif row.get('passed') is not True:
            raise ValueError('original pipeline execution failed')
    return {'upstream_v1_worker_count': 1, 'original_gates_retained': True}


class Runner:
    def __init__(self, output):
        self.output = output

    def __call__(self, name, command, timeout=2400):
        print('+', name, ' '.join(map(str, command)), flush=True)
        try:
            result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            for suffix, value in [('stdout', error.stdout), ('stderr', error.stderr)]:
                text = value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
                (self.output / (name + '.' + suffix)).write_text(text)
            raise RuntimeError(name + ': timed out after ' + str(timeout) + ' seconds; partial logs retained') from error
        (self.output / (name + '.stdout')).write_text(result.stdout)
        (self.output / (name + '.stderr')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(name + ': exit ' + str(result.returncode) + '\n' + result.stderr[-3000:] + '\n' + result.stdout[-1000:])
        return result.stdout


def run_pipeline_scaling(package, output, prefix, *, environment):
    """Execute actual packaged pipeline; fixed inputs and unpaced correctness only."""
    run = Runner(output)
    cases = []
    for label, fixture, seconds, workload in [('sync', 'rade-modem', 3, 'continuous'),
                                             ('silence', 'silence', 3, 'continuous'),
                                             ('interrupted', 'rade-modem', 14, 'interrupt')]:
        reference = None
        for workers in (1, 2, 3, 4):
            name = 'pipeline-' + label + '-' + str(workers)
            command = [*prefix, package / 'bin/rade-pipeline-bench', 'parallel',
                       package / 'fixtures' / (fixture + '.raw'), str(seconds), 'unpaced', '1', workload,
                       '--acquisition-workers', str(workers)]
            result = strict_json(run(name, command))
            validate_acquisition_result(result, workers=workers, execution='parallel', pacing='unpaced',
                                        workload=workload, seconds=seconds, warmup=1, fixture_name=fixture)
            if reference is None:
                reference = result
            comparison = compare_acquisition_results(reference, result)
            if comparison['status'] != 'MATCH':
                raise ValueError(name + ': functional output differs from one-worker reference')
            cases.append({'case': name, 'workers': workers, 'passed': True, 'comparison': comparison,
                          'acquisition_workers': result['acquisition_workers']})
    return {'environment': environment, 'cases': cases, 'workloads': 3, 'worker_counts': [1, 2, 3, 4],
            'radio_hardware_tested': False, 'timing_qualifies_radio': False}


def compile_and_run_corpora(work, source, code, compiler, prefix, output):
    run = Runner(output)
    opus = work / 'rade_c/build_opus-prefix/src/build_opus'
    reference_acq = work / 'rade-acq-parallel-reference.c'
    if sha(reference_acq) != GUARDED_ACQ_SHA or sha(source / 'rade_acq.c') != GUARDED_ACQ_SHA:
        raise ValueError('guarded serial acquisition source changed')
    if sha(source / 'rade_rx.c') != ORIGINAL_RX_SHA:
        raise ValueError('receiver state machine source changed')
    reference_rx = output / 'reference_rx.c'
    shutil.copy2(source / 'rade_rx.c', reference_rx)
    includes = [source, opus / 'dnn', opus / 'celt', opus / 'include', opus]
    flags = [compiler, '-O2', '-g', '-DNDEBUG', '-fPIC', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
             '-Wno-error=unused-variable', '-Wno-error=unused-parameter', '-pthread',
             '-marm', '-mfpu=neon-vfpv4', '-ffp-contract=fast', '-include', opus / 'config.h',
             *['-I' + str(p) for p in includes], '-DRADE_ACQ_PARALLEL=1', '-DRADE_ACQ_NEON=1',
             '-DRADE_ACQ_TEST_HOOKS=1', '-DRADE_ACQ_EXPECT_NEON=1']
    names = ('rade_rx_init', 'rade_rx_reset', 'rade_rx_nin', 'rade_rx_nin_max', 'rade_rx_n_features_out',
             'rade_rx_n_eoo_bits', 'rade_rx_sync', 'rade_rx_snrdB_3k_est', 'rade_rx_freq_offset',
             'rade_rx_sum_uw_errors', 'rade_rx_process', 'rade_acq_init', 'rade_acq_detect_pilots',
             'rade_acq_refine', 'rade_acq_check_pilots', 'rade_acq_test_neon_used')
    renames = ['-D' + name + '=reference_' + name for name in names]
    units = [('reference_rx', reference_rx, renames), ('reference_acq', reference_acq, renames),
             ('candidate_rx', source / 'rade_rx.c', []),
             ('candidate_acq', source / 'rade_acq.c', ['-Drade_acq_detect_pilots=rade_acq_detect_pilots_serial']),
             ('candidate_parallel', source / 'rade_acq_parallel.c', []),
             ('candidate_api', source / 'rade_api.c', [])]
    objects = []
    for name, file, extra in units:
        obj = output / (name + '.o')
        run('compile-' + name, [*flags, *extra, '-c', file, '-o', obj])
        objects.append(obj)
    harnesses = {'detector': 'rade_acq_parallel_differential.c',
                 'stream': 'rade_acq_parallel_stream.c', 'lifecycle': 'rade_acq_parallel_lifecycle.c'}
    for name, file in harnesses.items():
        run('link-' + name, [*flags, code / file, *objects, '-L' + str(work / 'package/lib'),
                            '-Wl,-rpath,' + str(work / 'package/lib'), '-lrade', '-lm', '-o', output / name])
    off_flags = [*flags, '-URADE_ACQ_PARALLEL', '-DRADE_ACQ_PARALLEL=0']
    off_object = output / 'default_off_api.o'
    run('compile-default-off-api', [*off_flags, '-c', source / 'rade_api.c', '-o', off_object])
    off_harness = code / 'rade_acq_parallel_default_off.c'
    run('link-default-off', [*off_flags, off_harness, off_object,
                             '-L' + str(work / 'package/lib'), '-Wl,-rpath,' + str(work / 'package/lib'),
                             '-lrade', '-lm', '-o', output / 'default-off'])
    default_off = validate_default_off_rows(json_rows(run('default-off', [*prefix, output / 'default-off'])))
    detectors, streams = {}, {}
    baseline_stream = None
    for workers in (1, 2, 3, 4):
        detector_rows = json_rows(run('detector-' + str(workers), [*prefix, output / 'detector', workers]))
        detectors[str(workers)] = validate_detector_rows(detector_rows, workers=workers)
        stream_rows = json_rows(run('stream-' + str(workers), [*prefix, output / 'stream', workers]))
        streams[str(workers)] = validate_stream_rows(stream_rows, workers=workers)
        if baseline_stream is None:
            baseline_stream = stream_rows
        else:
            compare_stream_rows(baseline_stream, stream_rows)
    lifecycle = validate_lifecycle_rows(json_rows(run('lifecycle', [*prefix, output / 'lifecycle'])))
    return {'detector_by_workers': detectors, 'receiver_by_workers': streams,
            'receiver_state_totals_match_same_sdk_one_worker': True, 'lifecycle': lifecycle,
            'disabled_api': default_off, 'disabled_api_harness_sha256': sha(off_harness),
            'harness_sha256': {name: sha(code / file) for name, file in harnesses.items()},
            'reference_acquisition_sha256': sha(reference_acq), 'reference_receiver_sha256': sha(reference_rx),
            'instrumented_objects_separate_from_packaged_library': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--sysroot', type=Path, required=True)
    parser.add_argument('--radae-reference', type=Path, required=True)
    parser.add_argument('--validation-python', type=Path, required=True)
    args = parser.parse_args()
    compiler = args.compiler.absolute()  # Preserve Buildroot wrapper argv[0].
    triplet = subprocess.check_output([str(compiler), '-dumpmachine'], text=True).strip()
    if not triplet.startswith('arm') or 'buildroot' not in triplet:
        parser.error('matching ARM Buildroot compiler required')
    qemu = shutil.which('qemu-arm')
    if not qemu:
        parser.error('qemu-arm required; validation cannot be skipped')
    work, sysroot = args.work.resolve(), args.sysroot.resolve()
    code, source, package = work / 'firmware/tools/freedv', work / 'sources/rade_c/src', work / 'package'
    output = work / 'parallel-sdk-validation'
    output.mkdir(parents=True, exist_ok=False)
    pin = strict_json((code / 'patches/rade-acq-parallel.json').read_text())
    for name, expected in pin['candidate_files'].items():
        if sha(work / 'sources/rade_c' / name) != expected:
            raise ValueError('parallel source hash differs: ' + name)
    if sha(source / 'rade_acq.c') != GUARDED_ACQ_SHA:
        raise ValueError('original full-search implementation changed')
    layout = validate_layout_commands(strict_json((work / 'rade_c/compile_commands.json').read_text()), source)
    old_acq_path, old_pipeline_path = work / 'sdk-validation/summary.json', work / 'pipeline-sdk-validation/summary.json'
    baseline = validate_prior_gates(strict_json(old_acq_path.read_text()), strict_json(old_pipeline_path.read_text()))
    pins = strict_json((code / 'sources.json').read_text())
    reference = args.radae_reference.resolve()
    if subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip() != pins['radae_reference']['commit']:
        raise ValueError('V1 reference source revision differs from original gate')
    if sha(reference / 'wav/all.wav') != pins['radae_reference']['all_wav_sha256']:
        raise ValueError('V1 reference source fixture differs from original gate')
    libraries = ':'.join(map(str, [package / 'lib', sysroot / 'usr/lib', sysroot / 'lib']))
    prefix = [qemu, '-cpu', 'cortex-a7', '-L', sysroot, '-E', 'LD_LIBRARY_PATH=' + libraries]
    corpus = compile_and_run_corpora(work, source, code, compiler, prefix, output)
    pipeline = run_pipeline_scaling(package, output, prefix,
                                    environment='matching Buildroot SDK under QEMU cortex-a7')
    summary = {'schema_version': 1, 'experiment': 'rade-acquisition-worker-scaling',
               'environment': 'matching Buildroot SDK under QEMU cortex-a7',
               'radio_hardware_tested': False, 'timing_qualifies_radio': False,
               'original_70_percent_assessment': 'unchanged_serial_reference_required',
               'upstream_v1_gate': {**baseline, 'summary_sha256': sha(old_acq_path)},
               'existing_pipeline_gate_sha256': sha(old_pipeline_path),
               'validated_translation_units': layout,
               'compile_commands_sha256': sha(work / 'rade_c/compile_commands.json'),
               'source_pin_sha256': sha(code / 'patches/rade-acq-parallel.json'),
               'source_hashes': {name: sha(work / 'sources/rade_c' / name) for name in pin['candidate_files']},
               'fixture_sha256': sha(package / 'fixtures/rade-modem.raw'),
               'corpora': corpus, 'packaged_pipeline': pipeline,
               'numerical_limit': 'Finite same-SDK differential and hash evidence; no arbitrary-input proof or radio timing qualification.'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('PASS: acquisition workers 1..4, detector/receiver/lifecycle and packaged pipeline; no target timing qualification', flush=True)


if __name__ == '__main__':
    main()
