#!/usr/bin/env python3
"""Build the production USB controller and codec runner without GUI dependencies.

The optional codec preparation uses the same pinned dependency builder and
acquisition patches as the image path. This builds software test executables;
it does not contact a radio, run a campaign, or confer release qualification.
"""
import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PINS = json.loads((ROOT / 'tools/freedv/sources.json').read_text())
from usb_test_support import run_bounded


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def snapshot():
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                    cwd=ROOT).decode().split('\0')
    files = {name: sha(ROOT / name) for name in sorted(set(names)) if name and (ROOT / name).is_file()}
    tree = hashlib.sha256(''.join(digest + '  ' + name + '\n' for name, digest in files.items()).encode()).hexdigest()
    return files, tree


def codec_layout(codec):
    """Reject a serial/unpatched codec or a different speech/model fixture."""
    source = codec / 'sources/rade_c'
    opus = codec / 'rade_c/build_opus-prefix/src/build_opus'
    paths = {
        'rade_source': source / 'src', 'opus_build': opus,
        'rade_library': codec / 'rade_c/src/librade.so.0.1',
        'opus_library': opus / '.libs/libopus.a',
        'samplerate_library': codec / 'samplerate/src/libsamplerate.a',
        'samplerate_include': codec / 'sources/libsamplerate-0.2.2/include',
        'speech': source / 'input_sample.wav',
        'opus_model': opus / ('opus_data-' + PINS['opus']['model_sha256'] + '.tar.gz'),
    }
    for name, path in paths.items():
        if not path.exists():
            raise ValueError('Missing pinned codec input ' + name + ': ' + str(path))
    patch = json.loads((ROOT / 'tools/freedv/patches/rade-acq-parallel.json').read_text())
    for name, expected in patch['candidate_files'].items():
        if sha(source / name) != expected:
            raise ValueError('Codec is not the production parallel-acquisition source: ' + name)
    if sha(source / 'src/rade_acq.c') != patch['unchanged_guarded_acquisition_sha256']:
        raise ValueError('Guarded acquisition source changed')
    if sha(paths['speech']) != PINS['rade_c']['speech_fixture_sha256']:
        raise ValueError('Speech fixture changed')
    if sha(paths['opus_model']) != PINS['opus']['model_sha256']:
        raise ValueError('Opus model changed')
    cache = (codec / 'rade_c/CMakeCache.txt').read_text()
    if 'RADE_ACQ_PARALLEL:BOOL=ON' not in cache or 'RADE_ACQ_NEON:BOOL=OFF' not in cache:
        raise ValueError('Host codec must use parallel acquisition and disable ARM NEON')
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required by the campaign hashing contract')
    parser.add_argument('--work', type=Path, required=True, help='Fresh directory outside the checkout')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--codec', type=Path, help='Existing verified host codec dependency tree')
    choice.add_argument('--prepare-codec', action='store_true', help='Build pinned dependencies in the new work directory')
    parser.add_argument('--build-id', default=None)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--allow-dirty', action='store_true', help='Local diagnosis only; record remains nonpromotable')
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError('Controller build interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    if not 1 <= args.jobs <= 8:
        parser.error('--jobs must be 1..8')
    work = args.work.resolve()
    if work == ROOT or ROOT in work.parents:
        parser.error('--work must be outside the checkout')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if dirty and not args.allow_dirty:
        parser.error('Commit all source inputs before a reproducible controller build')
    if dirty and args.prepare_codec:
        parser.error('Pinned codec preparation requires clean committed source')
    build_id = args.build_id or ('usb-controller-ci-' + revision)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', build_id):
        parser.error('build ID must contain only letters, numbers, dots, underscores and hyphens')
    work.mkdir(parents=True, exist_ok=False)
    files, tree = snapshot()
    record = {'schema': 'x6200.usb.controller-build.v1', 'status': 'RUNNING',
              'source_commit': revision, 'source_tree_sha256': tree, 'source_files': files,
              'source_worktree_dirty': dirty, 'build_id': build_id, 'commands': [],
              'radio_accessed': False, 'hardware_qualified': False,
              'release_qualified': False, 'scope': 'Standalone host executables; campaign evidence is separate'}
    save(work / 'build-record.json', record)

    def run(command, log_name, timeout=900):
        command = list(map(str, command))
        entry = {'command': command, 'timeout_seconds': timeout, 'log': log_name, 'status': 'RUNNING'}
        record['commands'].append(entry)
        save(work / 'build-record.json', record)
        try:
            result = run_bounded(command, work / log_name, timeout, cwd=ROOT)
            entry.update(result)
            if result['timed_out']:
                entry['status'] = 'TIMEOUT'
                raise TimeoutError('Command exceeded its deadline: ' + log_name)
            if result['exit_code'] != 0:
                entry['status'] = 'FAIL'
                raise RuntimeError('Command failed: ' + log_name + ' (exit ' + str(result['exit_code']) + ')')
            entry['status'] = 'PASS'
        finally:
            if (work / log_name).exists():
                entry['log_sha256'] = sha(work / log_name)
            save(work / 'build-record.json', record)

    try:
        codec = args.codec.resolve() if args.codec else work / 'codec'
        if args.prepare_codec:
            run([sys.executable, ROOT / 'tools/freedv/build_benchmark_package.py', '--host-validation',
                 '--work', codec, '--jobs', args.jobs], 'prepare-codec.log', timeout=1200)
            source = codec / 'sources/rade_c'
            run(['git', '-C', source, 'apply', '--check', ROOT / 'tools/freedv/patches/rade-acq-neon.patch'],
                'guarded-patch-check.log', timeout=30)
            run(['git', '-C', source, 'apply', ROOT / 'tools/freedv/patches/rade-acq-neon.patch'],
                'guarded-patch.log', timeout=30)
            # The exact guard text and patch validation are shared with the
            # existing image-side host codec validator, not reimplemented.
            from validate_native_host_codec import NEON_CMAKE
            from build_benchmark_package import apply_rade_acquisition_parallel
            cmake = source / 'src/CMakeLists.txt'
            cmake.write_text(cmake.read_text() + NEON_CMAKE)
            record['parallel_patch'] = apply_rade_acquisition_parallel(ROOT, source)
            run(['cmake', '-S', source, '-B', codec / 'rade_c', '-DCMAKE_BUILD_TYPE=RelWithDebInfo',
                 '-DRADE_ACQ_PARALLEL=ON', '-DRADE_ACQ_NEON=OFF'], 'parallel-configure.log', timeout=120)
            run(['cmake', '--build', codec / 'rade_c', '--target', 'rade', '-j', args.jobs],
                'parallel-build.log', timeout=600)
        paths = codec_layout(codec)
        record['runtime_environment'] = {'LD_LIBRARY_PATH': str(paths['rade_library'].parent)}
        record['codec_inputs'] = {name: {'path': str(path), 'sha256': sha(path)}
                                  for name, path in paths.items() if path.is_file()}
        headers = {paths['opus_build'] / 'config.h'}
        for directory in (paths['rade_source'], paths['opus_build'] / 'include', paths['opus_build'] / 'dnn',
                          paths['opus_build'] / 'celt', paths['samplerate_include']):
            headers.update(directory.rglob('*.h'))
        record['codec_header_sha256'] = {str(path): sha(path) for path in sorted(headers) if path.is_file()}
        record['codec_candidate_source'] = {
            name: sha(codec / 'sources/rade_c' / name)
            for name in json.loads((ROOT / 'tools/freedv/patches/rade-acq-parallel.json').read_text())['candidate_files']}
        record['compiler'] = subprocess.check_output([args.cc, '--version'], text=True, timeout=30).splitlines()[0]
        common = [args.cc, '-std=c11', '-O2', '-g', '-UNDEBUG', '-pthread', '-DRADE_ACQ_PARALLEL=1',
                  '-DFREEDV_BUILD_ID="' + build_id + '"', '-DFREEDV_SOURCE_COMMIT="' + revision + '"',
                  '-I' + str(ROOT), '-I' + str(paths['rade_source']),
                  '-I' + str(paths['opus_build']), '-I' + str(paths['opus_build'] / 'include'),
                  '-I' + str(paths['opus_build'] / 'dnn'), '-I' + str(paths['opus_build'] / 'celt'),
                  '-I' + str(paths['samplerate_include']), '-include', str(paths['opus_build'] / 'config.h')]
        engine = [ROOT / 'src/freedv/voice_engine.c', ROOT / 'tools/freedv/rade_stream.c', ROOT / 'src/freedv/capture_ring.c']
        links = [paths['rade_library'], paths['opus_library'], paths['samplerate_library'],
                 '-Wl,-rpath,' + str(paths['rade_library'].parent), '-lpulse', '-lm']
        controller = work / 'freedv_usb_audio_test'
        runner = work / 'freedv_voice_runner'
        run([*common, ROOT / 'tools/freedv/usb_audio_integration_test.c', ROOT / 'src/freedv/usb_audio.c',
             ROOT / 'src/freedv/usb_evidence_store.c', *engine, *links, '-o', controller], 'controller-build.log', timeout=180)
        run([*common, ROOT / 'tools/freedv/voice_runner.c', *engine, *links, '-o', runner], 'runner-build.log', timeout=180)
        fixture_pin = PINS['rade_c']['modem_fixture']
        frozen = ROOT / fixture_pin['path']
        if sha(frozen) != fixture_pin['sha256'] or frozen.stat().st_size != fixture_pin['bytes']:
            raise ValueError('Pinned modem fixture changed')
        pcm = array('h', frozen.read_bytes())
        if sys.byteorder != 'little':
            pcm.byteswap()
        converted = array('h')
        for index, value in enumerate(pcm):
            following = pcm[index + 1] if index + 1 < len(pcm) else value
            converted.extend(round(value + (following - value) * fraction / 6) for fraction in range(6))
        if sys.byteorder != 'little':
            converted.byteswap()
        fixture = work / 'usb-reference-48k.raw'
        fixture.write_bytes(converted.tobytes())
        for item in record['codec_inputs'].values():
            if sha(item['path']) != item['sha256']:
                raise ValueError('Codec input changed during compilation: ' + item['path'])
        for name, expected in record['codec_header_sha256'].items():
            if sha(name) != expected:
                raise ValueError('Codec header changed during compilation: ' + name)
        after, after_tree = snapshot()
        if files != after or tree != after_tree:
            raise ValueError('Source inputs changed during compilation')
        record.update(status='PASS', binaries={p.name: {'path': str(p), 'sha256': sha(p)} for p in (controller, runner)},
                      speech={'path': str(paths['speech']), 'sha256': sha(paths['speech'])},
                      modem={'path': str(fixture), 'sha256': sha(fixture), 'rate_hz': 48000,
                             'frozen_8k_source_sha256': fixture_pin['sha256']})
        print('PASS: production USB controller/codec runner built; no radio or release qualification')
        return 0
    except Exception as error:
        record.update(status='FAIL', reason=str(error))
        print('FAIL:', error, file=sys.stderr)
        return 1
    finally:
        save(work / 'build-record.json', record)


if __name__ == '__main__':
    raise SystemExit(main())
