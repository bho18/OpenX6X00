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
        for target in re.findall(r'\b(?:blx|bl|b)(?:eq|ne|cs|cc|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?(?:\.w)?\s+[^<\n]*<([^>]+)>', assembly):
            target = target.split('+', 1)[0]
            if target.startswith(('rade_acq_grid_neon', 'rade_acq_correlate4')):
                pending.append(target)
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
        bench_results.append({'mode': mode, 'direction': direction, 'fixture': fixture, 'workload': workload, 'passed': True})

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
               'upstream_v1_loss_threshold': 0.119, 'upstream_v1_loss_passed': True, 'benchmark_cases': bench_results}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('PASS: SDK differential, V1 text/speech/loss and seven codec workloads; no target performance qualification', flush=True)


if __name__ == '__main__':
    main()
