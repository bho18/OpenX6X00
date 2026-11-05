#!/usr/bin/env python3
"""Build one development variant and record its actual software evidence.

Run in a prepared host environment with pinned LVGL, host DSP dependencies,
the matching Buildroot SDK and target codec libraries. This does not download,
install, flash, qualify hardware, or weaken image-assembly checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PIN = '0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_snapshot():
    names = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT
    ).decode().split('\0')
    files = {name: sha(ROOT / name) for name in sorted(set(names))
             if name and (ROOT / name).is_file()}
    digest = hashlib.sha256(''.join(value + '  ' + name + '\n'
                                  for name, value in files.items()).encode()).hexdigest()
    return files, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', required=True,
                        choices=('host', 'host-native-validation', 'x6200-diagnostic', 'x6200-normal', 'x6100'))
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--codec', type=Path)
    parser.add_argument('--codec-fixture', type=Path,
                        help='Pinned RADE modem fixture for host-native-validation codec checks')
    parser.add_argument('--control-headers', type=Path)
    parser.add_argument('--x6100-prefix', type=Path)
    parser.add_argument('--build-id', default='unqualified-development')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--usb-test', action='store_true', help='Separate GUI USB headset RF development mode')
    args = parser.parse_args()
    if args.usb_test and args.variant not in ('host-native-validation','x6200-diagnostic'):
        parser.error('--usb-test is restricted to X6200 diagnostic or host-native validation builds')
    work = args.work.resolve()
    if work == ROOT or ROOT in work.parents:
        parser.error('--work must be outside the source checkout')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    host = args.variant in ('host', 'host-native-validation')
    host_native = args.variant == 'host-native-validation'
    if not host and not args.sdk:
        parser.error('ARM variants require --sdk')
    if args.variant in ('host-native-validation', 'x6200-diagnostic', 'x6100') and not args.codec:
        parser.error('native/compatibility variants require --codec')
    if host_native and (not args.codec_fixture or not args.codec_fixture.is_file()):
        parser.error('host-native-validation requires --codec-fixture')
    if args.variant == 'x6100' and not args.x6100_prefix:
        parser.error('X6100 compatibility build requires --x6100-prefix')
    if not (ROOT / 'lvgl/CMakeLists.txt').is_file():
        parser.error('populate the pinned LVGL submodule first')
    work.mkdir(parents=True, exist_ok=True)
    before, tree = source_snapshot()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    record = {'schema_version': 1, 'status': 'RUNNING', 'variant': args.variant,
              'usb_test_enabled':args.usb_test,
              'source_commit': revision, 'source_tree_sha256': tree,
              'source_files': before, 'build_id': args.build_id, 'commands': [],
              'hardware_qualified': False, 'hardware_status': 'BLOCKED',
              'physical_audio_routes': 'UNQUALIFIED',
              'physical_hand_ptt': 'UNQUALIFIED; aggregate TX does not establish an independent hand PTT contract',
              'physical_rf_off_timing': 'UNASSESSED',
              'timing_qualifies_radio': False}
    env = os.environ.copy()
    options = ['-DCMAKE_BUILD_TYPE=RelWithDebInfo']
    options += ['-DENABLE_FREEDV_USB_TEST=' + ('ON' if args.usb_test else 'OFF')]
    if host:
        options += ['-DHOST_BUILD=ON', '-DENABLE_FREEDV_DIAGNOSTICS=ON',
                    '-DFREEDV_HOST_TEST_PROFILE=' + ('ON' if host_native else 'OFF')]
        options += ['-D' + name + '=' + ('ON' if host_native else 'OFF')
                    for name in ('ENABLE_FREEDV_NATIVE_CODEC', 'ENABLE_FREEDV_VOICE',
                                 'ENABLE_FREEDV_NATIVE_VALIDATION')]
        if args.control_headers:
            options += ['-DX6200_CONTROL_INCLUDE_DIR=' + str(args.control_headers.resolve())]
        if host_native:
            codec = args.codec.resolve()
            opus = codec / 'rade_c/build_opus-prefix/src/build_opus'
            dependencies = {
                'FREEDV_RADE_SOURCE': codec / 'sources/rade_c/src',
                'FREEDV_OPUS_BUILD': opus,
                'FREEDV_RADE_LIBRARY': codec / 'rade_c/src/librade.so.0.1',
                'FREEDV_OPUS_LIBRARY': opus / '.libs/libopus.a',
                'FREEDV_SAMPLERATE_LIBRARY': codec / 'samplerate/src/libsamplerate.a',
                'FREEDV_SAMPLERATE_INCLUDE_DIR': codec / 'sources/libsamplerate-0.2.2/include',
            }
            options += ['-D' + name + '=' + str(path) for name, path in dependencies.items()]
            options += ['-DFREEDV_BUILD_ID=' + args.build_id, '-DFREEDV_CONTROL_ID=' + CONTROL_PIN]
            record['codec_inputs'] = {name: {'path': str(path), 'sha256': sha(path)}
                                     for name, path in dependencies.items() if path.is_file()}
            record['opus_configuration_sha256'] = sha(opus / 'config.h')
            record['codec_fixture_sha256'] = sha(args.codec_fixture)
            record['host_simulated'] = True
            record['validation_entry_exercised'] = False
            env['LD_LIBRARY_PATH'] = str(codec / 'rade_c/src') + os.pathsep + env.get('LD_LIBRARY_PATH', '')
    else:
        sdk = args.sdk.resolve()
        for name in ('CPATH', 'LIBRARY_PATH', 'PKG_CONFIG_PATH', 'CMAKE_PREFIX_PATH'):
            env.pop(name, None)
        env['PATH'] = str(sdk / 'bin') + os.pathsep + env['PATH']
        compiler = sdk / 'bin/arm-buildroot-linux-gnueabihf-gcc'
        sysroot = Path(subprocess.check_output([str(compiler), '-print-sysroot'], text=True).strip())
        if sdk not in sysroot.resolve().parents:
            parser.error('compiler sysroot must belong to the supplied SDK')
        record['compiler'] = {'path': str(compiler), 'sha256': sha(compiler.resolve()),
                              'version': subprocess.check_output([str(compiler), '--version'], text=True).splitlines()[0],
                              'sysroot': str(sysroot)}
        options += ['-DHOST_BUILD=OFF', '-DCMAKE_TOOLCHAIN_FILE=' + str(sdk / 'share/buildroot/toolchainfile.cmake'),
                    '-DHW_BACKEND=' + ('x6100' if args.variant == 'x6100' else 'x6200'),
                    '-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON', '-DFREEDV_HOST_TEST_PROFILE=OFF']
        options += ['-DENABLE_FREEDV_NATIVE_VALIDATION=' +
                    ('ON' if args.variant == 'x6200-diagnostic' else 'OFF')]
        enabled = args.variant in ('x6200-diagnostic', 'x6100')
        options += ['-D' + name + '=' + ('ON' if enabled else 'OFF')
                    for name in ('ENABLE_FREEDV_DIAGNOSTICS', 'ENABLE_FREEDV_NATIVE_CODEC', 'ENABLE_FREEDV_VOICE')]
        if enabled:
            codec = args.codec.resolve()
            dependencies = {
                'FREEDV_RADE_SOURCE': codec / 'sources/rade_c/src',
                'FREEDV_OPUS_BUILD': codec / 'opus',
                'FREEDV_RADE_LIBRARY': codec / 'rade_c/src/librade.so.0.1',
                'FREEDV_OPUS_LIBRARY': codec / 'opus/.libs/libopus.a',
                'FREEDV_SAMPLERATE_LIBRARY': codec / 'samplerate/src/libsamplerate.a',
                'FREEDV_SAMPLERATE_INCLUDE_DIR': codec / 'sources/libsamplerate-0.2.2/include',
                'FREEDV_NE10_LIBRARY': sysroot / 'usr/lib/libNE10.so',
            }
            options += ['-D' + name + '=' + str(path) for name, path in dependencies.items()]
            options += ['-DFREEDV_BUILD_ID=' + args.build_id, '-DFREEDV_CONTROL_ID=' + CONTROL_PIN]
            record['codec_inputs'] = {name: {'path': str(path), 'sha256': sha(path)}
                                      for name, path in dependencies.items() if path.is_file()}
            record['opus_configuration_sha256'] = sha(codec / 'opus/config.h')
        if args.variant == 'x6100':
            prefix = args.x6100_prefix.resolve()
            flags = '-D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -O3 -g0 -D_FORTIFY_SOURCE=1'
            options += ['-DCMAKE_C_FLAGS=' + flags + ' -I' + str(prefix / 'include'),
                        '-DCMAKE_CXX_FLAGS=' + flags + ' -I' + str(prefix / 'include'),
                        '-DCMAKE_EXE_LINKER_FLAGS=-L' + str(prefix / 'lib')]

    def run(command, log_name):
        command = list(map(str, command))
        print('+ ' + ' '.join(command), flush=True)
        record['commands'].append(command)
        with (work / log_name).open('w') as log:
            subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

    try:
        run(['cmake', '-S', ROOT, '-B', work / 'build', '-G', 'Ninja', *options], 'configure.log')
        targets = [] if args.variant == 'host' else ['--target', 'x6100_gui']
        if host_native:
            targets += ['params_sqlite', 'freedv_voice_engine_test', 'freedv_native_stream_test',
                        'freedv_native_stream_fixture_test', 'freedv_native_audio_codec_adapter_test',
                        'freedv_voice_runner']
            if args.usb_test: targets += ['freedv_usb_audio_test']
        if args.variant == 'x6200-diagnostic':
            targets += ['freedv_voice_runner', 'freedv_voice_pulse_probe']
        run(['cmake', '--build', work / 'build', *targets, '-j', args.jobs], 'build.log')
        if args.variant == 'host':
            run(['ctest', '--test-dir', work / 'build', '-R', '^freedv_', '--no-tests=error',
                 '--output-on-failure', '--output-junit', work / 'ctest.xml'], 'ctest.log')
            tests = list(ET.parse(work / 'ctest.xml').getroot().iter('testcase'))
            incomplete = [case.attrib['name'] for case in tests
                          if any(case.find(tag) is not None for tag in ('skipped', 'failure', 'error'))]
            if incomplete:
                raise ValueError('host tests were skipped or failed: ' + ', '.join(incomplete))
            record['host_tests'] = [case.attrib['name'] for case in tests]
            if not record['host_tests']:
                raise ValueError('CTest executed no FreeDV tests')
        if host_native:
            checks = {
                'voice-engine': [work / 'build/freedv_voice_engine_test', '--turns', '3'],
                'native-stream': [work / 'build/freedv_native_stream_test', '--unpaced'],
                'stream-fixture': [work / 'build/freedv_native_stream_fixture_test', args.codec_fixture.resolve()],
                'codec-adapter': [work / 'build/freedv_native_audio_codec_adapter_test', args.codec_fixture.resolve()],
            }
            record['codec_checks'] = {}
            for name, command in checks.items():
                run(command, name + '.log')
                record['codec_checks'][name] = {'status': 'PASS', 'log_sha256': sha(work / (name + '.log'))}
            if args.usb_test:
                from array import array
                pcm=array('h',args.codec_fixture.read_bytes())
                converted=array('h')
                for i,x in enumerate(pcm):
                    following=pcm[i+1] if i+1<len(pcm) else x
                    converted.extend(round(x+(following-x)*fraction/6) for fraction in range(6))
                fixture=work/'usb-reference-48k.raw';fixture.write_bytes(converted.tobytes())
                env['USB_RF_REFERENCE_RUNNER']=str(work/'build/freedv_voice_runner')
                for name,extra in [('usb-controller',[]),('usb-disconnect',['--disconnect']),('usb-watchdog',['--stall'])]:
                    run([sys.executable,ROOT/'tools/freedv/test_usb_audio_integration.py',work/'build/freedv_usb_audio_test',
                         codec/'sources/rade_c/input_sample.wav',fixture,*extra],name+'.log')
                    record['codec_checks'][name]={'status':'PASS','log_sha256':sha(work/(name+'.log'))}
            record['scope'] = 'Full host native GUI compilation and targeted real-codec correctness; no physical audio, RF or timing qualification'
        gui = work / 'build/src/x6100_gui'
        record['binaries'] = {'gui': {'path': str(gui), 'sha256': sha(gui)}}
        if args.variant == 'x6200-diagnostic':
            for name in ('freedv_voice_runner', 'freedv_voice_pulse_probe'):
                binary = work / 'build' / name
                record['binaries'][name] = {'path': str(binary), 'sha256': sha(binary)}
        if not host:
            run([sdk / 'bin/arm-buildroot-linux-gnueabihf-readelf', '-h', '-A', '-d', gui], 'elf.txt')
        after, _ = source_snapshot()
        record['source_changed_during_build'] = sorted(name for name in set(before) | set(after)
                                                      if before.get(name) != after.get(name))
        if record['source_changed_during_build']:
            raise ValueError('source changed during build; repeat after edits finish')
        record['status'] = 'PASS'
    except (subprocess.CalledProcessError, ValueError) as error:
        record['status'] = 'FAIL'
        record['error'] = str(error)
        raise
    finally:
        (work / 'build-record.json').write_text(json.dumps(record, indent=2) + '\n')
    print('PASS: ' + args.variant + ' software build; hardware remains BLOCKED', flush=True)


if __name__ == '__main__':
    main()
