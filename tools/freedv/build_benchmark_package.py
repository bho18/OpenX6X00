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
import urllib.request
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
    p.add_argument('--rade-acq-neon', action='store_true', help='experimental SDK-only acquisition kernel; requires QEMU correctness gates')
    p.add_argument('--radae-reference', type=Path, help='clean pinned RADAE checkout for upstream V1 loss validation')
    p.add_argument('--validation-python', type=Path, help='Python with upstream RADAE loss dependencies')
    p.add_argument('--cache-root', type=Path, help='optional local Git object cache')
    p.add_argument('--jobs', type=int, default=2)
    a = p.parse_args()
    if bool(a.sdk) == a.host_validation or not 1 <= a.jobs <= 8:
        p.error('choose exactly one of --sdk or --host-validation; jobs must be 1..8')
    if a.rade_acq_neon and (not a.sdk or not a.radae_reference or not a.validation_python):
        p.error('--rade-acq-neon requires --sdk, --radae-reference and --validation-python')
    if a.rade_acq_neon and not shutil.which('qemu-arm'):
        p.error('--rade-acq-neon requires qemu-arm; validation cannot be skipped')
    work = a.work.resolve()
    if work == ROOT or ROOT in work.parents:
        p.error('work directory must be outside the repository')
    if a.sdk:
        if not a.sdk_manifest or a.sdk_manifest.read_text().strip() != PINS['x6200_buildroot']['commit']:
            p.error('matching baseline buildroot-commit.txt is required')
        sdk = a.sdk.resolve()
        tc = sdk / 'share/buildroot/toolchainfile.cmake'
        compilers = sorted((sdk / 'bin').glob('arm*-gcc'), key=lambda path: ('buildroot' not in path.name, path.name))
        if not tc.is_file() or not compilers:
            p.error(f'expected relocated ARM Buildroot SDK: toolchain={tc.is_file()}, compilers={[p.name for p in compilers]}')
        compiler = compilers[0]
        triplet = subprocess.check_output([str(compiler), '-dumpmachine'], text=True).strip()
        if not triplet.startswith('arm') or 'buildroot' not in triplet:
            p.error('compiler does not identify as an ARM Buildroot toolchain')
        for alias in compilers[1:]:
            if subprocess.check_output([str(alias), '-dumpmachine'], text=True).strip() != triplet:
                p.error('SDK compiler aliases disagree on target')
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
    if a.rade_acq_neon:
        # Only this exact pinned input may receive the experimental patch.
        acq_source = src / 'rade_c/src/rade_acq.c'
        expected_acq = 'e3f22479ed8cbfa477f65b52968072fc3ab1cd1aa9c70a5b3ab23d2c8fd4fa5a'
        if sha(acq_source) != expected_acq:
            raise ValueError('acquisition source does not match inspected baseline')
        shutil.copy2(acq_source, work / 'rade-acq-reference.c')
        patch = code / 'tools/freedv/patches/rade-acq-neon.patch'
        run('git', '-C', src / 'rade_c', 'apply', '--check', patch)
        run('git', '-C', src / 'rade_c', 'apply', patch)
        acq_cmake = src / 'rade_c/src/CMakeLists.txt'
        # Source-local options keep Opus, the rest of RADE and host fixture
        # generation on their existing build configuration.
        acq_cmake.write_text(acq_cmake.read_text() + '''
option(RADE_ACQ_NEON "Experimental four-time-lane ARM acquisition" OFF)
if(RADE_ACQ_NEON)
  if(NOT CMAKE_CROSSCOMPILING)
    message(FATAL_ERROR "RADE_ACQ_NEON is enabled only for the matching ARM SDK")
  endif()
  set_source_files_properties(rade_acq.c PROPERTIES
    COMPILE_DEFINITIONS RADE_ACQ_NEON=1
    COMPILE_OPTIONS "-marm;-mfpu=neon-vfpv4;-ffp-contract=fast")
endif()
''')
        manifest['rade_acquisition_experiment'] = {
            'enabled': True, 'production_qualified': False,
            'patch_sha256': sha(patch), 'original_source_sha256': expected_acq,
            'patched_source_sha256': sha(acq_source), 'source_cmake_sha256': sha(acq_cmake),
            'source_local_definitions': ['RADE_ACQ_NEON=1'],
            'source_local_options': ['-marm', '-mfpu=neon-vfpv4', '-ffp-contract=fast'],
            'differential_harness_sha256': sha(code / 'tools/freedv/rade_acq_differential.c')}
    for name, fixture in [('codec2', 'raw/ve9qrp_10s.raw'), ('rade_c', 'input_sample.wav')]:
        if sha(src / name / fixture) != PINS[name]['speech_fixture_sha256']:
            raise ValueError('fixture hash mismatch: ' + name)
    samplerate_tar = work / 'libsamplerate.tar.xz'
    with urllib.request.urlopen('https://github.com/libsndfile/libsamplerate/releases/download/0.2.2/libsamplerate-0.2.2.tar.xz', timeout=60) as response, samplerate_tar.open('wb') as target:
        shutil.copyfileobj(response, target)
    if sha(samplerate_tar) != PINS['x6200_libsamplerate']['source_sha256']:
        raise ValueError('libsamplerate source checksum mismatch')
    with tarfile.open(samplerate_tar) as archive:
        for member in archive.getmembers():
            if Path(member.name).is_absolute() or '..' in Path(member.name).parts or member.issym() or member.islnk():
                raise ValueError('unexpected libsamplerate archive path')
        archive.extractall(src)
    samplerate_source = src / 'libsamplerate-0.2.2'
    shutil.copy2(samplerate_source / 'COPYING', package / 'licenses/libsamplerate-COPYING')
    run('cmake', '-S', samplerate_source, '-B', work / 'samplerate', '-DCMAKE_BUILD_TYPE=RelWithDebInfo',
        '-DBUILD_SHARED_LIBS=OFF', '-DBUILD_TESTING=OFF', '-DLIBSAMPLERATE_EXAMPLES=OFF', *cross)
    run('cmake', '--build', work / 'samplerate', '--target', 'samplerate', '-j', a.jobs)
    samplerate_library = work / 'samplerate/src/libsamplerate.a'
    manifest['libsamplerate_static_sha256'] = sha(samplerate_library)
    # Opus source is generated from the pinned Git object, not an unversioned download.
    opus_zip = work / 'opus.zip'
    run('git', '-C', src / 'opus', 'archive', '--format=zip', '--prefix=opus/', '-o', opus_zip, PINS['opus']['commit'])
    manifest['opus_archive_sha256'] = sha(opus_zip)
    # Changes below apply only to these disposable source copies.
    cm = src / 'rade_c/cmake/BuildOpus.cmake'
    text = cm.read_text().replace('set(CONFIGURE_COMMAND ./autogen.sh && ./configure ',
        'set(CONFIGURE_COMMAND ./autogen.sh && ./configure CC=${CMAKE_C_COMPILER} AR=${CMAKE_AR} RANLIB=${CMAKE_RANLIB} ')
    cm.write_text(text)
    cm = src / 'rade_c/CMakeLists.txt'
    cm.write_text(cm.read_text().replace('include(cmake/BuildOpus.cmake)', '''include(cmake/BuildOpus.cmake)
# Imported Opus otherwise drops its optional SDK NE10 link dependency.
find_library(FREEDV_NE10_LIBRARY NAMES NE10)
if(FREEDV_NE10_LIBRARY)
  set_property(TARGET opus APPEND PROPERTY INTERFACE_LINK_LIBRARIES "${FREEDV_NE10_LIBRARY}")
endif()'''))
    # Prevent SDK compiler settings from leaking into Codec2's host code generator.
    cm = src / 'codec2/src/CMakeLists.txt'
    cm.write_text(cm.read_text().replace('BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR}/codec2_native',
        'CMAKE_ARGS -DCMAKE_C_COMPILER=/usr/bin/cc -DCMAKE_CXX_COMPILER=/usr/bin/c++\n       BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR}/codec2_native'))
    for name in ('codec2', 'rade_c'):
        extra = ['-DUNITTEST=OFF', '-DLPCNET=OFF'] if name == 'codec2' else ['-DOPUS_URL=' + opus_zip.as_uri()]
        if name == 'rade_c' and a.rade_acq_neon:
            extra += ['-DRADE_ACQ_NEON=ON']
        run('cmake', '-S', src / name, '-B', work / name, '-DCMAKE_BUILD_TYPE=RelWithDebInfo',
            '-DCMAKE_INSTALL_RPATH=$ORIGIN', '-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON', *cross, *extra)
        target = ['codec2', 'freedv_tx'] if name == 'codec2' else ['rade', 'rade_tx_wav']
        if name == 'rade_c' and a.rade_acq_neon:
            target += ['rade_rx_wav', 'rade_v1_text_test']
        run('cmake', '--build', work / name, '--target', *target, '-j', a.jobs)
    opus = work / 'rade_c/build_opus-prefix/src/build_opus'
    model = opus / ('opus_data-' + PINS['opus']['model_sha256'] + '.tar.gz')
    if sha(model) != PINS['opus']['model_sha256']:
        raise ValueError('Opus model checksum mismatch')
    manifest['opus_model_sha256'] = sha(model)
    manifest['opus_config_sha256'] = sha(opus / 'config.h')
    ne10 = re.search(r'^FREEDV_NE10_LIBRARY:FILEPATH=(.*)$', (work / 'rade_c/CMakeCache.txt').read_text(), re.M).group(1)
    ne10 = None if ne10.endswith('-NOTFOUND') else Path(ne10)
    manifest['sdk_ne10_library_sha256'] = sha(ne10) if ne10 else None
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
        ('rade-frame-bench', 'rade_frame_bench.c', [src / 'rade_c/src', opus / 'dnn', opus / 'celt', opus / 'include', opus], ['-L' + str(work / 'rade_c/src'), '-lrade', str(opus / '.libs/libopus.a')] + ([str(ne10)] if ne10 else []))]:
        run(compiler, '-O2', '-g', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-error=cpp',
            *(['-include', str(opus / 'config.h')] if name == 'rade-frame-bench' else []),
            *['-I' + str(i) for i in [*includes, samplerate_source / 'include']], code / 'tools/freedv' / source,
            *libs, samplerate_library, '-lm', '-Wl,-rpath,$ORIGIN/../lib', '-o', package / 'bin' / name)
    # Bundle codec libraries. The baseline supplies libc/libm; resampling is static.
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
    if a.rade_acq_neon:
        # No deployable archive is produced until matching-SDK execution checks
        # have passed. Emulation establishes correctness, never radio timing.
        run('python3', code / 'tools/freedv/validate_rade_acq_sdk.py', '--work', work,
            '--compiler', compiler, '--sysroot', sysroot,
            '--radae-reference', a.radae_reference.resolve(),
            '--validation-python', a.validation_python.absolute())
        validation = work / 'sdk-validation/summary.json'
        manifest['rade_acquisition_experiment']['sdk_validation_summary_sha256'] = sha(validation)
        manifest['rade_acquisition_experiment']['sdk_validation'] = json.loads(validation.read_text())
        manifest['rade_acquisition_experiment']['flags_make'] = (work / 'rade_c/src/CMakeFiles/rade.dir/flags.make').read_text()
    source_archive = work / 'freedv-benchmark-sources.tar.gz'
    with tarfile.open(source_archive, 'w:gz') as archive:
        for name in ('codec2', 'rade_c', 'opus'):
            archive.add(src / name, arcname=name, filter=lambda item: None if '/.git' in item.name else item)
        archive.add(samplerate_source, arcname='libsamplerate-0.2.2')
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
