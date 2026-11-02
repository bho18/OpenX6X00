#!/usr/bin/env python3
"""Gate an experimental acquisition build with matching-SDK ARM execution.

QEMU results establish execution/correctness only, never radio timing. This
helper runs before the package manifest/archive is finalized by the builder.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import wave


EXPECTED_DIFFERENTIAL_CASES = {
    'zero-full', 'noise-full', 'high-level-noise', 'low-normal-noise',
    'pilot-cfo--50', 'pilot-cfo--48.75', 'pilot-cfo--2.5', 'pilot-cfo-0',
    'pilot-cfo-1.25', 'pilot-cfo-47.5', 'pilot-cfo-49.9',
    'pilot-timing-boundary-0', 'pilot-timing-boundary-959',
    'timing-remainder-1', 'timing-remainder-2', 'timing-remainder-3',
    'one-timing-position', 'three-timing-positions', 'four-timing-positions',
    'equal-time-frequency-ties', 'threshold-bracket-low', 'threshold-bracket-high',
    *('near-threshold-%02d' % n for n in range(12)),
    'subnormal-noise', 'subnormal-products', 'nan-input', 'inf-input',
    'below-input-domain', 'above-input-domain', 'input-domain-boundaries',
    'signed-zero-input', 'below-pilot-domain', 'above-pilot-domain', 'nan-pilot',
    'pilot-domain-boundaries', 'upward-rounding',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_rade_stage_timing(result):
    """Validate measured-block stage accounting; never assess target speed."""
    def integer(value, name, positive=False):
        if type(value) is not int or not (1 if positive else 0) <= value <= (1 << 64) - 1:
            raise ValueError(name + ': expected unsigned 64-bit integer' + (' > 0' if positive else ''))
        return value

    def finite(value, name, positive=False):
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            valid = False
        if not valid or (positive and value <= 0):
            raise ValueError(name + ': expected finite nonnegative number' + (' > 0' if positive else ''))
        return value

    if not isinstance(result, dict) or result.get('mode') != 'RADE-V1' or result.get('direction') not in ('tx', 'rx'):
        raise ValueError('stage timing requires a RADE-V1 TX/RX result')
    frames = integer(result.get('frames'), 'frames', positive=True)
    features = integer(result.get('feature_blocks'), 'feature_blocks')
    output_samples = integer(result.get('output_samples'), 'output_samples')
    if features > frames:
        raise ValueError('feature count exceeds measured blocks')
    finite(result.get('fixture_preparation_ms'), 'fixture_preparation_ms')
    audio = finite(result.get('audio_seconds'), 'audio_seconds', positive=True)
    ratio = finite(result.get('processing_realtime_factor'), 'processing_realtime_factor')
    block_max = finite(result.get('max_block_ms'), 'max_block_ms')
    block_p99 = finite(result.get('p99_block_ms'), 'p99_block_ms')
    if block_p99 > block_max:
        raise ValueError('full-block p99 exceeds maximum')
    timing = result.get('stage_timing')
    if not isinstance(timing, dict) or type(timing.get('schema_version')) is not int or timing['schema_version'] != 1:
        raise ValueError('missing or unsupported stage timing schema')
    if (timing.get('unit') != 'nanoseconds' or timing.get('other_is_residual') is not True or
            timing.get('percentile_population') != 'measured_blocks_including_inactive_zeros' or
            timing.get('calls_semantics') != 'timed_regions_other_counts_residual_evaluations'):
        raise ValueError('stage timing semantics do not match measured-block schema')
    total = integer(timing.get('total_processing_ns'), 'total_processing_ns', positive=True)
    # Both ratio and audio_seconds are printed to six decimal places. Bound
    # their independent rounding errors, plus one nanosecond for FP arithmetic.
    rounding_ns = (0.5e-6 * audio + 0.5e-6 * ratio + 0.25e-12) * 1e9 + 1
    expected_ns = ratio * audio * 1e9
    if not math.isfinite(expected_ns) or not math.isfinite(rounding_ns) or abs(total - expected_ns) > rounding_ns:
        raise ValueError('stage total differs from full processing timer')
    # Existing millisecond fields have three fractional digits (<=500ns error).
    if total > (block_max * 1e6 + 501) * frames:
        raise ValueError('total processing time exceeds full maximum/frame bound')
    names = {'input_resampling', 'tx_speech_processing', 'rade_tx', 'rade_rx',
             'rx_speech_processing', 'output_resampling', 'other'}
    stages = timing.get('stages')
    if not isinstance(stages, dict) or set(stages) != names:
        raise ValueError('missing or unexpected stage names')
    keys = {'calls', 'active_blocks', 'sum_ns', 'p99_block_ns', 'max_block_ns'}
    for name, stage in stages.items():
        if not isinstance(stage, dict) or set(stage) != keys:
            raise ValueError(name + ': missing or unexpected stage fields')
        for key in keys:
            integer(stage[key], name + '.' + key)
        calls, active = stage['calls'], stage['active_blocks']
        if calls != active or active > frames:
            raise ValueError(name + ': calls/active blocks differ from measured region semantics')
        if not active and any(stage.values()):
            raise ValueError(name + ': inactive stage has nonzero statistics')
        if not stage['p99_block_ns'] <= stage['max_block_ns'] <= stage['sum_ns']:
            raise ValueError(name + ': inconsistent duration/percentile ordering')
        if stage['sum_ns'] > stage['max_block_ns'] * active:
            raise ValueError(name + ': sum exceeds maximum/active-block bound')
        if (stage['max_block_ns'] > block_max * 1e6 + 501 or
                stage['p99_block_ns'] > block_p99 * 1e6 + 501):
            raise ValueError(name + ': stage percentile exceeds full-block percentile')
        p99_index = (frames * 99 + 99) // 100 - 1
        if p99_index < frames - active and stage['p99_block_ns'] != 0:
            raise ValueError(name + ': percentile did not include inactive zero blocks')
    if sum(stage['sum_ns'] for stage in stages.values()) != total:
        raise ValueError('stage sums do not equal full processing time')
    for name in ('input_resampling', 'other', 'rade_' + result['direction']):
        if stages[name]['calls'] != frames:
            raise ValueError(name + ': must execute once per measured block')
    if result['direction'] == 'tx':
        if features or not output_samples:
            raise ValueError('TX feature/output counters are inconsistent')
        for name in ('tx_speech_processing', 'output_resampling'):
            if stages[name]['calls'] != frames:
                raise ValueError(name + ': TX region did not cover every measured block')
        unused = ('rade_rx', 'rx_speech_processing')
    else:
        if stages['rx_speech_processing']['calls'] != features:
            raise ValueError('RX speech region count differs from feature blocks')
        output_calls = stages['output_resampling']['calls']
        if output_calls > features or bool(output_calls) != bool(output_samples):
            raise ValueError('RX output region count differs from feature/output counters')
        unused = ('rade_tx', 'tx_speech_processing')
    if any(any(stages[name].values()) for name in unused):
        raise ValueError('inactive direction has nonzero stage statistics')
    return {'stage_schema_version': 1, 'accounting_checked': True,
            'measured_frames': frames, 'total_processing_ns': total}


def acquisition_callees(assembly):
    """Follow only the explicit serial detector and bounded acquisition helpers."""
    branch = r'\b(?:blx|bl|b)(?:eq|ne|cs|cc|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?(?:\.w)?\s+[^<\n]*<([^>]+)>'
    helper = r'(?:rade_acq_grid_neon|rade_acq_correlate4)(?:\.(?:constprop|isra|part)\.[0-9]+)*'
    targets = set()
    for target in re.findall(branch, assembly):
        target = re.sub(r'\+0x[0-9a-fA-F]+$', '', target)
        if target.endswith('@plt'):
            target = target[:-4]
        if target in ('rade_acq_pool_detect', 'rade_acq_detect_pilots_serial') or re.fullmatch(helper, target):
            targets.add(target)
    return sorted(targets)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--compiler', type=Path, required=True)
    p.add_argument('--sysroot', type=Path, required=True)
    p.add_argument('--radae-reference', type=Path, required=True)
    p.add_argument('--validation-python', type=Path, required=True)
    a = p.parse_args()
    work, sysroot = a.work.resolve(), a.sysroot.resolve()
    reference, python = a.radae_reference.resolve(), a.validation_python.absolute()
    # Do not resolve the SDK compiler symlink: Buildroot aliases may depend on
    # argv[0] to select the correct compiler wrapper/target.
    compiler = a.compiler.absolute()
    triplet = subprocess.check_output([str(compiler), '-dumpmachine'], text=True).strip()
    if not triplet.startswith('arm') or 'buildroot' not in triplet:
        p.error('matching ARM Buildroot SDK compiler required')
    qemu = shutil.which('qemu-arm')
    if not qemu:
        p.error('qemu-arm is required; no archive may be released without SDK validation')
    out = work / 'sdk-validation'
    out.mkdir(exist_ok=False)
    source = work / 'sources/rade_c/src'
    code = work / 'firmware/tools/freedv'
    package = work / 'package'
    pins = json.loads((code / 'sources.json').read_text())
    if subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip() != pins['radae_reference']['commit']:
        p.error('RADAE reference commit does not match source manifest')
    if subprocess.check_output(['git', '-C', str(reference), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        p.error('RADAE reference has tracked modifications')
    if sha(reference / 'wav/all.wav') != pins['radae_reference']['all_wav_sha256']:
        p.error('upstream V1 loss fixture hash mismatch')

    def run(name, command, timeout=1800, env=None):
        print('+', name, ' '.join(map(str, command)), flush=True)
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True,
                                timeout=timeout, env=env)
        (out / (name + '.stdout')).write_text(result.stdout)
        (out / (name + '.stderr')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name} failed ({result.returncode}):\n{result.stderr[-3000:]}\n{result.stdout[-3000:]}')
        return result.stdout

    qprefix = [qemu, '-cpu', 'cortex-a7', '-L', sysroot, '-E', 'LD_LIBRARY_PATH=' + ':'.join(map(str, [package / 'lib', sysroot / 'usr/lib', sysroot / 'lib']))]
    common = [compiler, '-O2', '-g', '-std=c11', '-Wall', '-Wextra', '-Werror', '-marm', '-mfpu=neon-vfpv4', '-ffp-contract=fast', '-I' + str(source)]
    rename = ['-D' + function + '=reference_' + function for function in
              ('rade_acq_init', 'rade_acq_detect_pilots', 'rade_acq_refine', 'rade_acq_check_pilots')]
    run('compile-reference', [*common, *rename, '-c', work / 'rade-acq-reference.c', '-o', out / 'reference.o'])
    run('compile-neon', [*common, '-DRADE_ACQ_NEON=1', '-DRADE_ACQ_TEST_HOOKS=1', '-c', source / 'rade_acq.c', '-o', out / 'neon.o'])
    run('compile-default-off', [*common, '-DRADE_ACQ_TEST_HOOKS=1', '-c', source / 'rade_acq.c', '-o', out / 'default-off.o'])
    run('compile-dsp', [*common, '-c', source / 'rade_dsp.c', '-o', out / 'dsp.o'])
    run('compile-ofdm', [*common, '-Wno-error=unused-variable', '-c', source / 'rade_ofdm.c', '-o', out / 'ofdm.o'])
    differential = {}
    for variant in ('default-off', 'neon'):
        executable = out / ('acq-' + variant)
        expected_neon = 1 if variant == 'neon' else 0
        run('link-' + variant, [*common, '-DRADE_ACQ_TEST_HOOKS=1', '-DRADE_ACQ_EXPECT_NEON=' + str(expected_neon), code / 'rade_acq_differential.c', out / 'reference.o', out / (variant + '.o'), out / 'dsp.o', out / 'ofdm.o', '-lm', '-o', executable])
        rows = [json.loads(line) for line in run('differential-' + variant, [*qprefix, executable]).splitlines()]
        if not rows or rows[-1].get('summary') is not True or rows[-1].get('strict_failures') != 0:
            raise ValueError(variant + ': missing or failing differential summary')
        cases = rows[:-1]
        if rows[-1]['cases'] != len(cases) or len(cases) != len(EXPECTED_DIFFERENTIAL_CASES) or {case.get('case') for case in cases} != EXPECTED_DIFFERENTIAL_CASES:
            raise ValueError(variant + ': differential cases did not all execute')
        if any(case.get('bit_exact') is not True for case in cases):
            raise ValueError(variant + ': differential mismatch, including fallback edge cases')
        if rows[-1].get('path_failures') != 0 or any(case.get('path_expected') is not True for case in cases):
            raise ValueError(variant + ': selected execution path differs from expected path')
        neon_cases = sum(case.get('neon_used') is True for case in cases)
        scalar_cases = sum(case.get('neon_used') is False for case in cases)
        if neon_cases + scalar_cases != len(cases) or rows[-1].get('neon_cases') != neon_cases or rows[-1].get('scalar_cases') != scalar_cases:
            raise ValueError(variant + ': path instrumentation/counters are incomplete')
        if variant == 'neon' and (neon_cases == 0 or scalar_cases == 0):
            raise ValueError('enabled candidate must exercise both NEON and conservative scalar fallback')
        if variant == 'default-off' and neon_cases != 0:
            raise ValueError('default-off source unexpectedly selected NEON')
        differential[variant] = rows[-1]
    # Confirm emitted target code uses vector FMA, rather than merely trusting
    # a macro or the harness result. Keep the exact disassembly for review.
    objdump = compiler.parent / (triplet + '-objdump')
    assembly = run('neon-disassembly', [objdump, '-d', out / 'neon.o'])
    if 'vfma.f32\tq' not in assembly and 'vfma.f32 q' not in assembly:
        raise ValueError('candidate object did not contain vector FMA instructions')
    # GCC can inline the kernel or outline it into private acquisition helpers.
    # Inspect only this bounded call tree, not unrelated Opus vector code.
    pending = ['rade_acq_detect_pilots']
    inspected = set()
    vector_functions = []
    while pending:
        function = pending.pop()
        if function in inspected:
            continue
        if len(inspected) >= 12:
            raise ValueError('unexpected acquisition helper call tree')
        inspected.add(function)
        assembly = run('library-' + function,
                       [objdump, '-d', '--disassemble=' + function, package / 'lib/librade.so.0.1'])
        if re.search(r'\bvfma\.f32\s+q', assembly):
            vector_functions.append(function)
        pending.extend(acquisition_callees(assembly))
    if not vector_functions:
        raise ValueError('packaged acquisition call tree did not contain vector FMA instructions')

    # These targets are built by the package builder against the same library.
    binary = work / 'rade_c/src'
    text = run('v1-text', [*qprefix, binary / 'rade_v1_text_test'])
    if '\nPASS (6/6)' not in text:
        raise ValueError('upstream six-case V1 text check did not execute fully')
    run('v1-speech-tx', [*qprefix, binary / 'rade_tx_wav', reference / 'wav/all.wav', out / 'tx.wav', '-f', out / 'tx.f32'])
    run('v1-speech-rx', [*qprefix, binary / 'rade_rx_wav', out / 'tx.wav', out / 'rx.wav', '-f', out / 'rx.f32'])
    with wave.open(str(reference / 'wav/all.wav')) as wav:
        source_seconds = wav.getnframes() / wav.getframerate()
    with wave.open(str(out / 'rx.wav')) as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError('V1 speech result has incorrect PCM format')
        decoded_samples = wav.getnframes()
        # This coarse completeness bound rejects severe truncation. It is not
        # an intrinsic-latency or production audio-quality acceptance criterion.
        if decoded_samples < int(0.95 * source_seconds * 16000) or not any(wav.readframes(decoded_samples)):
            raise ValueError('V1 speech result is truncated or silent')
    feature_frames = {}
    for label in ('tx', 'rx'):
        data = (out / (label + '.f32')).read_bytes()
        if len(data) % (36 * 4):
            raise ValueError(label + ': malformed feature frame size')
        count = len(data) // (36 * 4)
        if count < int(0.95 * source_seconds / 0.01) or count <= 400:
            raise ValueError(label + ': insufficient feature frames for completeness/loss clipping')
        if any(not math.isfinite(value[0]) for value in struct.iter_unpack('<f', data)):
            raise ValueError(label + ': nonfinite speech features')
        feature_frames[label] = count
    env = os.environ.copy()
    env.update(MPLCONFIGDIR=str(out / 'matplotlib'), XDG_CACHE_HOME=str(out / 'cache'))
    loss = run('v1-loss', [python, reference / 'loss.py', out / 'tx.f32', out / 'rx.f32', '--loss_test', '0.119', '--clip_start', '100', '--clip_end', '300'], env=env)
    if 'PASS' not in loss.splitlines():
        raise ValueError('upstream V1 loss test did not report PASS')
    loss_match = re.search(r'^\s+loss:\s+(\S+)', loss, re.M)
    if not loss_match or not math.isfinite(float(loss_match.group(1))):
        raise ValueError('upstream V1 loss result is missing or nonfinite')
    reported_loss = float(loss_match.group(1))
    if reported_loss > 0.119:
        raise ValueError('upstream V1 loss result exceeds threshold')

    workloads = [
        ('700E', 'tx', '700e-speech', 3, 'continuous'),
        ('700E', 'rx', '700e-modem', 3, 'continuous'),
        ('700E', 'rx', 'silence', 3, 'continuous'),
        ('RADE-V1', 'tx', 'rade-speech', 3, 'continuous'),
        ('RADE-V1', 'rx', 'rade-modem', 3, 'continuous'),
        ('RADE-V1', 'rx', 'silence', 3, 'continuous'),
        ('RADE-V1', 'rx', 'rade-modem', 14, 'interrupt'),
    ]
    bench_results = []
    for i, (mode, direction, fixture, seconds, workload) in enumerate(workloads, 1):
        executable = 'codec2-frame-bench' if mode == '700E' else 'rade-frame-bench'
        command = [*qprefix, package / 'bin' / executable, direction, package / 'fixtures' / (fixture + '.raw'), str(seconds), 'unpaced']
        command += ['resample', '1'] if mode == '700E' else ['1', workload]
        result = json.loads(run(f'benchmark-{i:02d}', command))
        frames = result['frames']
        if result['mode'] != mode or result['direction'] != direction or type(frames) is not int or frames < 1 or result['radio_io'] or result['full_pipeline_qualified']:
            raise ValueError('invalid benchmark identity/counters')
        # Input frame lengths can change slightly during synchronization. Bound
        # duration/coverage without assuming every RX interval is identical.
        max_interval = 0.1 if mode == '700E' else 0.14
        audio = result['audio_seconds']
        warmup = result['warmup_audio_seconds']
        if not math.isfinite(audio) or not seconds <= audio <= seconds + max_interval + 1e-6:
            raise ValueError('benchmark did not represent the requested input duration')
        if not math.isfinite(warmup) or not 1 <= warmup <= 1 + max_interval + 1e-6:
            raise ValueError('benchmark warm-up did not execute as requested')
        if frames < math.floor(seconds / max_interval) or not 0.01 <= audio / frames <= max_interval:
            raise ValueError('benchmark frame count is inconsistent with its represented duration')
        if result['pacing'] != 'unpaced' or result['deadline_misses'] is not None or result['resampling_included'] is not True:
            raise ValueError('benchmark pacing/resampling configuration differs from requested workload')
        if mode == 'RADE-V1' and result['workload'] != workload:
            raise ValueError('RADE did not execute the requested workload')
        sync = result['sync_frames' if mode == '700E' else 'sync_blocks']
        if fixture == 'silence':
            if sync != 0 or (mode == 'RADE-V1' and result['output_samples'] != 0):
                raise ValueError('unexpected sync/speech on silence')
        elif result['output_samples'] <= 0 or (direction == 'rx' and sync <= 0):
            raise ValueError('missing output or receive synchronization')
        if workload == 'interrupt' and (result['interrupted_input_samples'] != 16000 or result['sync_blocks_after_first_interrupt'] <= 0):
            raise ValueError('interrupted RX did not demonstrate reacquisition')
        checked = {'mode': mode, 'direction': direction, 'fixture': fixture, 'workload': workload, 'passed': True}
        if mode == 'RADE-V1':
            checked['stage_timing'] = validate_rade_stage_timing(result)
        bench_results.append(checked)

    stage_workloads_checked = sum('stage_timing' in item for item in bench_results)
    if stage_workloads_checked != 4:
        raise ValueError('all four RADE stage-accounting workloads must execute')
    summary = {'schema_version': 1, 'execution_environment': 'QEMU user-mode cortex-a7 with matching Buildroot SDK',
               'radio_hardware_tested': False, 'timing_qualifies_radio': False,
               'differential': differential,
               'tested_cases_bit_exact': True,
               'neon_and_scalar_fallback_exercised': True,
               'numerical_limit': 'Finite differential cases are evidence, not proof for every floating-point input; unsafe tested domains use the scalar fallback',
               'vector_kernel_functions': sorted(vector_functions),
               'v1_text_cases_passed': 6, 'decoded_speech_samples': decoded_samples,
               'minimum_retained_input_fraction': 0.95, 'source_speech_seconds': source_seconds,
               'feature_frames': feature_frames, 'feature_values_finite': True,
               'upstream_v1_reported_loss_rounded': reported_loss,
               'upstream_v1_loss_threshold': 0.119, 'upstream_v1_loss_passed': True, 'benchmark_cases': bench_results,
               'rade_stage_timing_checked': True,
               'rade_stage_timing_workloads_checked': stage_workloads_checked}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('PASS: SDK differential, V1 text/speech/loss, seven codec workloads and four RADE stage-accounting checks; no target performance qualification', flush=True)


if __name__ == '__main__':
    main()
