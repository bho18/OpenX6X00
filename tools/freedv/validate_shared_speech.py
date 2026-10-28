#!/usr/bin/env python3
"""Native correctness comparison against the frozen pre-extraction benchmarks.

Requires an already built pinned RADE acquisition library and Opus tree.
Timings from this development check never qualify radio performance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, capture_output=True, text=True, **kwargs).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('rade-source', 'rade-library', 'opus-build', 'fixtures', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--compiler', default='cc')
    parser.add_argument('--reference', default='4f3be47')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    opus, rade = args.opus_build.resolve(), args.rade_source.resolve()
    library = args.rade_library.resolve()
    reference = run(['git', 'rev-parse', args.reference], cwd=root).strip()
    flags = [args.compiler, '-O2', '-g', '-std=c11', '-Wall', '-Wextra', '-Werror',
             '-Wno-error=cpp', '-pthread', '-DRADE_ACQ_PARALLEL=1', '-include', opus / 'config.h']
    flags += ['-I' + str(p) for p in (rade, opus / 'dnn', opus / 'celt', opus / 'include', opus)]
    libraries = [library, opus / '.libs/libopus.a', '-lsamplerate', '-lm',
                 '-Wl,-rpath,' + str(library.parent)]
    for source in ('rade_frame_bench', 'rade_pipeline_bench'):
        frozen = out / (source + '-reference.c')
        frozen.write_text(run(['git', 'show', reference + ':tools/freedv/' + source + '.c'], cwd=root))
        for kind, path in [('reference', frozen), ('shared', root / ('tools/freedv/' + source + '.c'))]:
            run(flags + [path] + libraries + ['-o', out / (source + '-' + kind)])
    run(flags + ['-UNDEBUG', root / 'tools/freedv/rade_speech_test.c'] + libraries +
        ['-o', out / 'rade-speech-test'])
    kernel_result = run([out / 'rade-speech-test'])
    (out / 'speech-kernel-test.txt').write_text(kernel_result)
    cases = []
    for mode, fixture in [('tx', 'rade-speech.raw'), ('rx', 'rade-modem.raw'), ('rx', 'silence.raw')]:
        for workload in (['continuous'] if mode == 'tx' else ['continuous', 'interrupt']):
            cases.append(('rade_frame_bench', mode, fixture, workload, 4))
    for fixture in ('rade-modem.raw', 'silence.raw'):
        for workload in ('continuous', 'interrupt'):
            cases.append(('rade_pipeline_bench', 'parallel', fixture, workload, 14))
    results = []
    for executable, mode, fixture, workload, seconds in cases:
        name = '-'.join((executable, mode, fixture, workload))
        pair = []
        for kind in ('reference', 'shared'):
            argv = [out / (executable + '-' + kind), mode, args.fixtures.resolve() / fixture,
                    str(seconds), 'unpaced', '1', workload]
            if mode == 'parallel':
                argv += ['--acquisition-workers', '3']
            raw = run(argv)
            (out / (name + '-' + kind + '.json')).write_text(raw)
            pair.append(json.loads(raw))
        before, after = pair
        if mode == 'parallel':
            keys = ['full', 'measured', 'acquisition_recovery']
            assert before['execution_complete'] and after['execution_complete'], name
            assert after['acquisition_workers']['effective'] == 3, name
        else:
            keys = [k for k in before if 'samples' in k or k in
                    ('frames', 'sync_blocks', 'feature_blocks', 'audio_seconds', 'sync_blocks_after_first_interrupt')]
        for key in keys:
            if before[key] != after[key]:
                raise RuntimeError(f'{name}: mismatch in {key}')
        results.append({'case': name, 'status': 'PASS', 'compared_fields': keys})
    manifest = {'schema_version': 1, 'hardware_qualified': False, 'reference_commit': reference,
                'integration_commit': run(['git', 'rev-parse', 'HEAD'], cwd=root).strip(),
                'working_tree_dirty': bool(run(['git', 'status', '--porcelain'], cwd=root)),
                'compiler': run([args.compiler, '--version']).splitlines()[0],
                'rade_library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
                'speech_core_sha256': hashlib.sha256((root / 'tools/freedv/rade_speech.h').read_bytes()).hexdigest(),
                'kernel_test': kernel_result.strip(), 'cases': results}
    (out / 'summary.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'PASS: {len(results)} native comparisons; results in {out}; radio performance was not qualified')


if __name__ == '__main__':
    main()
