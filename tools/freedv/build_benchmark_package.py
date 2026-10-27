#!/usr/bin/env python3
"""Build pinned benchmarks and fixtures. SDK builds are for X6200; host builds are validation only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import wave

ROOT = Path(__file__).resolve().parents[2]
PINS = json.loads((ROOT / 'tools/freedv/sources.json').read_text())


def run(*args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', required=True, type=Path, help='new build directory outside the checkout')
    p.add_argument('--sdk', type=Path, help='relocated matching Buildroot SDK directory')
    p.add_argument('--sdk-manifest', type=Path, help='baseline buildroot-commit.txt from the SDK build')
    p.add_argument('--host-validation', action='store_true')
    p.add_argument('--cache-root', type=Path, help='optional local Git object cache')
    p.add_argument('--jobs', type=int, default=2)
    a = p.parse_args()
    if bool(a.sdk) == a.host_validation or not 1 <= a.jobs <= 8:
        p.error('choose exactly one of --sdk or --host-validation; jobs must be 1..8')
    work = a.work.resolve()
    if work == ROOT or ROOT in work.parents:
        p.error('work directory must be outside the repository')
    if a.sdk:
        if not a.sdk_manifest or a.sdk_manifest.read_text().strip() != PINS['x6200_buildroot']['commit']:
            p.error('matching baseline buildroot-commit.txt is required')
        sdk = a.sdk.resolve()
        tc = sdk / 'share/buildroot/toolchainfile.cmake'
        compilers = list((sdk / 'bin').glob('arm*-gcc'))
        if not tc.is_file() or len(compilers) != 1:
            p.error('expected relocated ARM Buildroot SDK with one arm*-gcc and CMake toolchain')
        compiler = compilers[0]
        triplet = subprocess.check_output([str(compiler), '-dumpmachine'], text=True).strip()
        readelf = sdk / 'bin' / (triplet + '-readelf')
        sysroot = Path(subprocess.check_output([str(compiler), '-print-sysroot'], text=True).strip())
        cross = ['-DCMAKE_TOOLCHAIN_FILE=' + str(tc), '-DCMAKE_C_COMPILER_TARGET=' + triplet]
    else:
        compiler = Path(shutil.which('cc'))
        readelf = Path(shutil.which('readelf'))
        sysroot = None
        cross = []
    work.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    tracked_changes = subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
    new_inputs = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--others', '--exclude-standard', '--', 'tools/freedv', 'docs/freedv'], text=True).strip()
    if tracked_changes or new_inputs:
        raise ValueError('commit source changes before making a reproducible package')
    code = work / 'firmware'; code.mkdir()
    run('git', '-C', ROOT, 'archive', '--format=tar', '-o', work / 'firmware.tar', revision, 'tools/freedv', 'docs/freedv')
    run('tar', '-xf', work / 'firmware.tar', '-C', code)
    src = work / 'sources'; src.mkdir()
    package = work / 'package'; package.mkdir()
    for directory in ('bin', 'lib', 'fixtures', 'licenses', 'tools/freedv'):
        (package / directory).mkdir(parents=True)
    manifest = {'schema_version': 1, 'kind': 'benchmark-package', 'target': 'host-validation' if a.host_validation else 'x6200-buildroot',
                'radio_control': False, 'hardware_qualified': False, 'sources': PINS,
                'firmware_commit': revision,
                'firmware_worktree_dirty': False,
                'compiler_version': subprocess.check_output([str(compiler), '--version'], text=True).splitlines()[0],
                'sdk_manifest_sha256': sha(a.sdk_manifest) if a.sdk_manifest else None}
    for name in ('codec2', 'rade_c', 'opus'):
        pin = PINS[name]
        cached = a.cache_root / name if a.cache_root else None
        origin = cached if cached and (cached / '.git').exists() else pin['repository']
        run('git', 'clone', '--no-checkout', origin, src / name)
        run('git', '-C', src / name, 'checkout', '--detach', pin['commit'])
        for license_file in (src / name).glob('*'):
            if license_file.is_file() and license_file.name.upper().startswith(('LICENSE', 'COPYING')):
                shutil.copy2(license_file, package / 'licenses' / (name + '-' + license_file.name))
    for name, fixture in [('codec2', 'raw/ve9qrp_10s.raw'), ('rade_c', 'input_sample.wav')]:
        if sha(src / name / fixture) != PINS[name]['speech_fixture_sha256']:
            raise ValueError('fixture hash mismatch: ' + name)
    # Opus source is generated from the pinned Git object, not an unversioned download.
    opus_zip = work / 'opus.zip'
    run('git', '-C', src / 'opus', 'archive', '--format=zip', '--prefix=opus/', '-o', opus_zip, PINS['opus']['commit'])
    manifest['opus_archive_sha256'] = sha(opus_zip)
    # Changes below apply only to these disposable source copies.
    cm = src / 'rade_c/cmake/BuildOpus.cmake'
    text = cm.read_text().replace('set(CONFIGURE_COMMAND ./autogen.sh && ./configure ',
        'set(CONFIGURE_COMMAND ./autogen.sh && ./configure CC=${CMAKE_C_COMPILER} AR=${CMAKE_AR} RANLIB=${CMAKE_RANLIB} ')
    cm.write_text(text)
    # Prevent SDK compiler settings from leaking into Codec2's host code generator.
    cm = src / 'codec2/src/CMakeLists.txt'
    cm.write_text(cm.read_text().replace('BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR}/codec2_native',
        'CMAKE_ARGS -DCMAKE_C_COMPILER=/usr/bin/cc -DCMAKE_CXX_COMPILER=/usr/bin/c++\n       BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR}/codec2_native'))
    for name in ('codec2', 'rade_c'):
        extra = ['-DUNITTEST=OFF', '-DLPCNET=OFF'] if name == 'codec2' else ['-DOPUS_URL=' + opus_zip.as_uri()]
        run('cmake', '-S', src / name, '-B', work / name, '-DCMAKE_BUILD_TYPE=RelWithDebInfo',
            '-DCMAKE_INSTALL_RPATH=$ORIGIN', '-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON', *cross, *extra)
        target = ['codec2', 'freedv_tx'] if name == 'codec2' else ['rade', 'rade_tx_wav']
        run('cmake', '--build', work / name, '--target', *target, '-j', a.jobs)
    opus = work / 'rade_c/build_opus-prefix/src/build_opus'
    model = opus / ('opus_data-' + PINS['opus']['model_sha256'] + '.tar.gz')
    if sha(model) != PINS['opus']['model_sha256']:
        raise ValueError('Opus model checksum mismatch')
    manifest['opus_model_sha256'] = sha(model)
    # Host transmitters generate portable fixtures, even for a target SDK build.
    host = work
    if a.sdk:
        host = work / 'host'
        for name, target in [('codec2', 'freedv_tx'), ('rade_c', 'rade_tx_wav')]:
            run('cmake', '-S', src / name, '-B', host / name, '-DCMAKE_BUILD_TYPE=Release',
                '-DUNITTEST=OFF', '-DLPCNET=OFF', '-DOPUS_URL=' + opus_zip.as_uri())
            run('cmake', '--build', host / name, '--target', target, '-j', a.jobs)
    fixtures = package / 'fixtures'
    shutil.copy2(src / 'codec2/raw/ve9qrp_10s.raw', fixtures / '700e-speech.raw')
    run(host / 'codec2/src/freedv_tx', '700E', fixtures / '700e-speech.raw', fixtures / '700e-modem.raw')
    run(host / 'rade_c/src/rade_tx_wav', src / 'rade_c/input_sample.wav', work / 'rade-modem.wav')
    for label, path, rate in [('rade-speech', src / 'rade_c/input_sample.wav', 16000), ('rade-modem', work / 'rade-modem.wav', 8000)]:
        with wave.open(str(path), 'rb') as f:
            if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, rate):
                raise ValueError('unexpected fixture WAV format')
            (fixtures / (label + '.raw')).write_bytes(f.readframes(f.getnframes()))
    (fixtures / 'silence.raw').write_bytes(bytes(16000))
    for name, source, includes, libs in [
        ('codec2-frame-bench', 'codec2_frame_bench.c', [src / 'codec2/src'], ['-L' + str(work / 'codec2/src'), '-lcodec2']),
        ('rade-frame-bench', 'rade_frame_bench.c', [src / 'rade_c/src', opus / 'dnn', opus / 'celt', opus / 'include', opus], ['-L' + str(work / 'rade_c/src'), '-lrade', str(opus / '.libs/libopus.a')])]:
        run(compiler, '-O2', '-g', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-error=cpp',
            *['-I' + str(i) for i in includes], code / 'tools/freedv' / source,
            *libs, '-lsamplerate', '-lm', '-Wl,-rpath,$ORIGIN/../lib', '-o', package / 'bin' / name)
    # Bundle codec libraries. The baseline supplies libc, libm and libsamplerate.
    for name, pattern in [('codec2', 'libcodec2.so*'), ('rade_c', 'librade.so*')]:
        for lib in (work / name / 'src').glob(pattern):
            shutil.copy2(lib, package / 'lib' / lib.name)
    for name in ('inventory.py', 'collect_benchmark.py', 'sources.json', 'run_benchmarks.py', 'THIRD-PARTY-NOTICES.md'):
        shutil.copy2(code / 'tools/freedv' / name, package / 'tools/freedv' / name)
    shutil.copy2(code / 'docs/freedv/benchmark-package.md', package / 'README.md')
    manifest['elf_dependencies'] = {}
    for binary in [*(package / 'bin').iterdir(), *(package / 'lib').iterdir()]:
        info = subprocess.check_output([str(readelf), '-d', str(binary)], text=True)
        manifest['elf_dependencies'][str(binary.relative_to(package))] = re.findall(r'Shared library: \[(.*?)\]', info)
    source_archive = work / 'freedv-benchmark-sources.tar.gz'
    with tarfile.open(source_archive, 'w:gz') as archive:
        for name in ('codec2', 'rade_c', 'opus'):
            archive.add(src / name, arcname=name, filter=lambda item: None if '/.git' in item.name else item)
        archive.add(code / 'tools/freedv', arcname='tools/freedv')
    manifest['corresponding_source_archive_sha256'] = sha(source_archive)
    (package / 'package-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    hashes = [sha(f) + '  ' + str(f.relative_to(package)) for f in sorted(package.rglob('*')) if f.is_file()]
    (package / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n')
    with tarfile.open(work / 'freedv-benchmark-package.tar.gz', 'w:gz') as archive:
        archive.add(package, arcname='freedv-benchmark')
    print('Package:', work / 'freedv-benchmark-package.tar.gz')


if __name__ == '__main__':
    main()
