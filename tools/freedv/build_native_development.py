#!/usr/bin/env python3
"""Build one development variant and record its actual software evidence.

Run in a prepared host environment with pinned LVGL, host DSP dependencies,
the matching Buildroot SDK and target codec libraries. This does not download,
install, flash, qualify hardware, or weaken image-assembly checks.
"""
import argparse
import shutil
import time
import uuid
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from usb_test_campaign import Campaign, atomic_json, immutable_json, validate_campaign, validate_history, host_resource_configuration
from usb_test_support import run_bounded

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PIN = '0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_snapshot():
    names = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT, timeout=30
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
    parser.add_argument('--runtime-inputs', type=Path,
                        help='Source-bound SDK FT8/XARA inputs from build_runtime_package.py inputs')
    parser.add_argument('--build-id', default='unqualified-development')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--retire-intermediates-before-checks', action='store_true',
                        help='After archiving linked binaries, retire only regenerable CMake objects before timed checks')
    parser.add_argument('--usb-resource-reclaim-root',type=Path,action='append',default=[],help='Task-owned cache roots passed explicitly to the USB qualification resource prerequisite')
    parser.add_argument('--usb-test', action='store_true', help='Separate GUI USB headset RF development mode')
    parser.add_argument('--usb-voice', action='store_true', help='Operational FreeDV USB headset mode in the normal waterfall UI')
    args = parser.parse_args()
    if args.usb_test and args.variant not in ('host-native-validation','x6200-diagnostic'):
        parser.error('--usb-test is restricted to X6200 diagnostic or host-native validation builds')
    if args.usb_voice and args.variant not in ('host-native-validation', 'x6200-diagnostic'):
        parser.error('--usb-voice is restricted to X6200 diagnostic or host-native validation builds')
    work = args.work.resolve()
    if work == ROOT or ROOT in work.parents:
        parser.error('--work must be outside the source checkout')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    host = args.variant in ('host', 'host-native-validation')
    host_native = args.variant == 'host-native-validation'
    if host and args.runtime_inputs:
        parser.error('--runtime-inputs contains ARM dependencies; do not use it for host builds')
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
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=10).strip()
    record = {'schema_version': 2, 'status': 'RUNNING', 'variant': args.variant,
              'usb_test_enabled':args.usb_test, 'usb_voice_enabled':args.usb_voice or args.usb_test,
              'retire_intermediates_before_checks': args.retire_intermediates_before_checks,
              'source_commit': revision, 'source_tree_sha256': tree,
              'source_files': before, 'build_id': args.build_id, 'commands': [],
              'hardware_qualified': False, 'hardware_status': 'BLOCKED',
              'physical_audio_routes': 'UNQUALIFIED',
              'physical_hand_ptt': 'UNQUALIFIED; aggregate TX does not establish an independent hand PTT contract',
              'physical_rf_off_timing': 'UNASSESSED',
              'timing_qualifies_radio': False}
    attempt_id = '%020d-%s' % (time.time_ns(), uuid.uuid4().hex[:12])
    attempt = work / 'attempts' / attempt_id
    attempt.mkdir(parents=True)
    record.update(evidence_attempt_path=str(attempt), build_attempt_id=attempt_id,
                  command_results=[], logs={})
    immutable_json(attempt/'start.json', {'source_commit': revision, 'source_tree_sha256': tree,
                   'variant': args.variant, 'build_id': args.build_id, 'build_attempt_id': attempt_id})
    # This is a derived latest pointer/copy. Immutable attempt records and logs
    # are authoritative; replacing this file never deletes an earlier failure.
    atomic_json(work/'build-record.json', record)
    history = None
    try:
        identity = _build_identity(args, revision, tree)
        plan = {'profile': 'build', 'variant': args.variant, 'usb_test_enabled': args.usb_test, 'usb_voice_enabled':args.usb_voice or args.usb_test,
                'retire_intermediates_before_checks': args.retire_intermediates_before_checks}
        record.update(build_identity=identity, build_plan=plan)
        history = Campaign(work/'build-campaigns', identity, plan, attempt, args.build_id)
        _perform_build(args, parser, work, attempt, record, before, tree, revision, host, host_native)
    except BaseException as error:
        record['status'] = 'INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'FAIL'
        record['error'] = str(error)
        raise
    finally:
        immutable_json(attempt/'build-record.json', record)
        latest = {**record, 'immutable_record_path': str(attempt/'build-record.json'),
                  'immutable_record_sha256': sha(attempt/'build-record.json'),
                  'build_promotion_eligible': False}
        if history:
            try:
                latest['build_history'] = history.finish(record)
                latest['build_promotion_eligible'] = latest['build_history']['promotion_eligible']
            except (OSError, ValueError, KeyError, TypeError) as error:
                latest['build_history_error'] = str(error)
            finally:
                history.close()
        atomic_json(work/'build-record.json', latest)
    validate_build_record(latest)
    print('PASS: ' + args.variant + ' software build; hardware remains BLOCKED', flush=True)


def _build_identity(args, revision, tree):
    """Prerequisite history identity excludes work paths and human labels."""
    inputs = {}
    def add(name, path):
        path = Path(path)
        inputs[name] = sha(path.resolve()) if path.is_file() else 'MISSING'
    if args.runtime_inputs:
        from runtime_package import validate_inputs
        runtime = validate_inputs(args.runtime_inputs, sdk=args.sdk)
        add('runtime-inputs', args.runtime_inputs)
        add('runtime-ft8', runtime['ft8']['library']['path'])
        add('runtime-xara', runtime['xara']['binary']['path'])
        add('runtime-pat', runtime['pat']['binary']['path'])
    if args.variant in ('host','host-native-validation'):
        for name, default in (('CC','cc'),('CXX','c++')):
            compiler = shutil.which(os.environ.get(name,default))
            inputs['host-'+name.lower()+'-compiler'] = sha(Path(compiler).resolve()) if compiler else 'MISSING'
    if args.sdk:
        sdk = args.sdk.resolve()
        add('sdk-compiler', sdk/'bin/arm-buildroot-linux-gnueabihf-gcc')
        add('sdk-ne10', sdk/'arm-buildroot-linux-gnueabihf/sysroot/usr/lib/libNE10.so')
    if args.codec:
        codec = args.codec.resolve()
        host_native = args.variant == 'host-native-validation'
        add('rade-library', codec/'rade_c/src/librade.so.0.1')
        opus = codec/('rade_c/build_opus-prefix/src/build_opus' if host_native else 'opus')
        add('opus-library', opus/'.libs/libopus.a')
        add('opus-configuration', opus/'config.h')
        add('samplerate-library', codec/'samplerate/src/libsamplerate.a')
    if args.codec_fixture:
        add('codec-fixture', args.codec_fixture)
    for label, directory in (('control-headers', args.control_headers), ('x6100-prefix', args.x6100_prefix)):
        if directory:
            directory = directory.resolve()
            files = sorted(p for p in directory.rglob('*') if p.is_file())
            if not files:
                inputs[label] = 'MISSING'
            for path in files:
                add(label+'/'+str(path.relative_to(directory)), path)
    return {'source_commit': revision, 'source_tree_sha256': tree,
            'variant': args.variant, 'build_id': args.build_id,
            'retire_intermediates_before_checks': getattr(args, 'retire_intermediates_before_checks', False),
            'usb_test_enabled': args.usb_test, 'usb_voice_enabled':args.usb_voice or args.usb_test, 'jobs': args.jobs,
            'control_pin': CONTROL_PIN, 'dependency_inputs_sha256': inputs,
            'usb_host_resource_configuration':host_resource_configuration('qualification',
                args.usb_resource_reclaim_root or (ROOT,args.work.resolve())) if args.usb_test and args.variant=='host-native-validation' else None}


def validate_build_record(record, verify_files=True):
    try:
        return _validate_build_record(record, verify_files)
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise ValueError('Build evidence is missing or malformed: '+str(error)) from error


def _validate_build_record(record, verify_files=True):
    """Require the sealed current build and every same-config prerequisite attempt."""
    if (record.get('schema_version') != 2 or record.get('status') != 'PASS' or
            not record.get('build_promotion_eligible') or record.get('source_changed_during_build')):
        raise ValueError('Build is failed, incomplete, source-changed or blocked by prerequisite history')
    identity = record.get('build_identity', {})
    if any(identity.get(key) != record.get(key) for key in ('source_commit','source_tree_sha256','variant','build_id','usb_test_enabled','usb_voice_enabled')):
        raise ValueError('Build history identity differs from the recorded source/configuration')
    if record.get('build_history', {}).get('plan') != record.get('build_plan') or record.get('build_plan', {}).get('profile') != 'build':
        raise ValueError('Build prerequisite plan is missing or inconsistent')
    derived = {'immutable_record_path','immutable_record_sha256','build_history',
               'build_promotion_eligible','build_history_error'}
    bare = {key: value for key, value in record.items() if key not in derived}
    validate_history(record['build_history'], identity, bare, verify_files=verify_files)
    if verify_files:
        path = Path(record['immutable_record_path'])
        if sha(path) != record.get('immutable_record_sha256') or json.loads(path.read_text()) != bare:
            raise ValueError('Immutable build record differs from its latest pointer/copy')
        for entry in [*record.get('logs', {}).values(), *record.get('binaries', {}).values()]:
            if sha(entry['path']) != entry['sha256']:
                raise ValueError('Retained build evidence changed: '+entry['path'])
    return record


def _perform_build(args, parser, work, attempt, record, before, tree, revision, host, host_native):
    env = os.environ.copy()
    options = ['-DCMAKE_BUILD_TYPE=RelWithDebInfo', '-DFREEDV_SOURCE_COMMIT=' + revision,
               '-DFREEDV_BUILD_ID=' + args.build_id]
    options += ['-DENABLE_FREEDV_USB_TEST=' + ('ON' if args.usb_test else 'OFF'),
                '-DENABLE_FREEDV_USB_VOICE=' + ('ON' if args.usb_voice or args.usb_test else 'OFF')]
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
            options += ['-DFREEDV_CONTROL_ID=' + CONTROL_PIN]
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
        sysroot = Path(subprocess.check_output([str(compiler), '-print-sysroot'], text=True, timeout=10).strip())
        if sdk not in sysroot.resolve().parents:
            parser.error('compiler sysroot must belong to the supplied SDK')
        record['compiler'] = {'path': str(compiler), 'sha256': sha(compiler.resolve()),
                              'version': subprocess.check_output([str(compiler), '--version'], text=True, timeout=10).splitlines()[0],
                              'sysroot': str(sysroot)}
        options += ['-DHOST_BUILD=OFF', '-DCMAKE_TOOLCHAIN_FILE=' + str(sdk / 'share/buildroot/toolchainfile.cmake'),
                    '-DHW_BACKEND=' + ('x6100' if args.variant == 'x6100' else 'x6200'),
                    '-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON', '-DFREEDV_HOST_TEST_PROFILE=OFF']
        if args.runtime_inputs:
            from runtime_package import validate_inputs
            runtime = validate_inputs(args.runtime_inputs, sdk=sdk)
            options += ['-DOPENX6X00_FT8_LIBRARY=' + runtime['ft8']['library']['path'],
                        '-DOPENX6X00_FT8_INCLUDE_DIR=' + runtime['ft8']['include_dir'],
                        '-DXARA_BINARY=' + runtime['xara']['binary']['path'],
                        '-DPAT_BINARY=' + runtime['pat']['binary']['path']]
            record.update(runtime_inputs_sha256=sha(args.runtime_inputs),
                          runtime_ft8_sha256=runtime['ft8']['library']['sha256'],
                          runtime_xara_sha256=runtime['xara']['binary']['sha256'],
                          runtime_pat_sha256=runtime['pat']['binary']['sha256'])
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
            options += ['-DFREEDV_CONTROL_ID=' + CONTROL_PIN]
            record['codec_inputs'] = {name: {'path': str(path), 'sha256': sha(path)}
                                      for name, path in dependencies.items() if path.is_file()}
            record['opus_configuration_sha256'] = sha(codec / 'opus/config.h')
        if args.variant == 'x6100':
            prefix = args.x6100_prefix.resolve()
            flags = '-D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -O3 -g0 -D_FORTIFY_SOURCE=1'
            options += ['-DCMAKE_C_FLAGS=' + flags + ' -I' + str(prefix / 'include'),
                        '-DCMAKE_CXX_FLAGS=' + flags + ' -I' + str(prefix / 'include'),
                        '-DCMAKE_EXE_LINKER_FLAGS=-L' + str(prefix / 'lib')]

    def run(command, log_name, timeout=3600):
        command = list(map(str, command))
        print('+ ' + ' '.join(command), flush=True)
        record['commands'].append(command)
        log_path = attempt / log_name
        try:
            outcome = run_bounded(command, log_path, timeout, cwd=ROOT, env=env, terminate_grace=24.0)
            record['command_results'].append(outcome)
        except BaseException as error:
            record['command_results'].append({'command':command,'error':str(error),'interrupted':True})
            raise
        finally:
            if log_path.exists(): record['logs'][log_name] = {'path': str(log_path), 'sha256': sha(log_path)}
        if outcome['timed_out'] or outcome['exit_code'] != 0:
            raise subprocess.CalledProcessError(outcome['exit_code'] or 124, command)

    run(['cmake', '-S', ROOT, '-B', work / 'build', '-G', 'Ninja', *options], 'configure.log')
    targets = [] if args.variant == 'host' else ['--target', 'x6100_gui']
    if host_native:
        targets += ['params_sqlite', 'freedv_voice_engine_test', 'freedv_native_stream_test',
                    'freedv_native_stream_fixture_test', 'freedv_native_audio_codec_adapter_test',
                    'freedv_voice_runner']
        if args.usb_test: targets += ['freedv_usb_audio_test']
    if args.variant == 'x6200-diagnostic':
        targets += ['freedv_voice_runner', 'freedv_voice_pulse_probe']
    if args.runtime_inputs:
        targets += ['x6x00-mercury', 'ardopcf', 'btcat', 'x6100-bootanim', 'params_sqlite']
    run(['cmake', '--build', work / 'build', *targets, '-j', args.jobs], 'build.log')
    gui = work / 'build/src/x6100_gui'
    archive = attempt / 'binaries'; archive.mkdir()
    def retained(binary):
        target = archive / binary.name
        shutil.copy2(binary, target); target.chmod(0o555)
        return {'path': str(target), 'sha256': sha(target)}
    record['binaries'] = {'gui': retained(gui)}
    if args.runtime_inputs:
        for relative in ('third-party/mercury/x6x00-mercury', 'third-party/ardopcf/ardopcf',
                         'btcat/btcat', 'bootanim/x6100-bootanim', 'params.db'):
            binary = work / 'build' / relative
            record['binaries'][binary.name] = retained(binary)
    if host_native:
        for name in ('params.db', 'freedv_voice_runner', 'freedv_usb_audio_test', 'freedv_voice_engine_test',
                     'freedv_native_stream_test', 'freedv_native_stream_fixture_test', 'freedv_native_audio_codec_adapter_test'):
            if (work/'build'/name).is_file(): record['binaries'][name] = retained(work/'build'/name)
    if args.variant == 'x6200-diagnostic':
        for name in ('freedv_voice_runner', 'freedv_voice_pulse_probe'):
            binary = work / 'build' / name
            record['binaries'][name] = retained(binary)
    if args.retire_intermediates_before_checks:
        from release_cache import retire_intermediates
        retirement = retire_intermediates(work, record['binaries'])
        immutable_json(attempt/'cache-retirement.json', retirement)
        record['cache_retirement'] = {'path': str(attempt/'cache-retirement.json'),
                                      'sha256': sha(attempt/'cache-retirement.json'), 'receipt': retirement}
    if args.variant == 'host':
        run(['ctest', '--test-dir', work / 'build', '-R', '^freedv_', '--no-tests=error',
             '--output-on-failure', '--output-junit', attempt / 'ctest.xml'], 'ctest.log')
        tests = list(ET.parse(attempt / 'ctest.xml').getroot().iter('testcase'))
        incomplete = [case.attrib['name'] for case in tests
                      if any(case.find(tag) is not None for tag in ('skipped', 'failure', 'error'))]
        if incomplete:
            raise ValueError('host tests were skipped or failed: ' + ', '.join(incomplete))
        record['host_tests'] = [case.attrib['name'] for case in tests]
        if not record['host_tests']:
            raise ValueError('CTest executed no FreeDV tests')
    if host_native:
        checks = {
            'voice-engine': [work / 'build/freedv_voice_engine_test', '--turns', '3', '--cycles', '2'],
            'native-stream': [work / 'build/freedv_native_stream_test', '--unpaced'],
            'stream-fixture': [work / 'build/freedv_native_stream_fixture_test', args.codec_fixture.resolve()],
            'codec-adapter': [work / 'build/freedv_native_audio_codec_adapter_test', args.codec_fixture.resolve()],
        }
        if args.usb_test:
            checks['usb-summary'] = [sys.executable, ROOT/'tools/freedv/test_usb_summary_capture.py',
                                     '--binary', record['binaries']['freedv_usb_audio_test']['path']]
        record['codec_checks'] = {}
        for name, command in checks.items():
            run(command, name + '.log')
            record['codec_checks'][name] = {'status': 'PASS', 'log_sha256': sha(attempt / (name + '.log'))}
        if args.usb_test:
            from array import array
            pcm=array('h',args.codec_fixture.read_bytes())
            converted=array('h')
            for i,x in enumerate(pcm):
                following=pcm[i+1] if i+1<len(pcm) else x
                converted.extend(round(x+(following-x)*fraction/6) for fraction in range(6))
            fixture=attempt/'usb-reference-48k.raw';fixture.write_bytes(converted.tobytes());fixture.chmod(0o444)
            env['USB_RF_REFERENCE_RUNNER']=record['binaries']['freedv_voice_runner']['path']
            matrix_dir=attempt/'usb-matrix'
            matrix_record=matrix_dir/'matrix-result.json'
            try:
                run([sys.executable,ROOT/'tools/freedv/test_usb_audio_matrix.py',
                     record['binaries']['freedv_usb_audio_test']['path'],codec/'sources/rade_c/input_sample.wav',fixture,
                     '--output',matrix_dir,'--runner',record['binaries']['freedv_voice_runner']['path'],
                     '--profile','qualification','--campaign-root',work/'usb-campaigns',
                     '--rade-library',codec/'rade_c/src/librade.so.0.1',
                     '--source-commit',revision,'--source-tree-sha256',tree,
                     '--build-id',args.build_id,
                     *[item for root in (args.usb_resource_reclaim_root or (ROOT,work))
                       for item in ('--resource-reclaim-root',str(Path(root).absolute()))]], 'usb-matrix.log', timeout=3600)
            finally:
                if matrix_record.exists():
                    record['usb_matrix']=json.loads(matrix_record.read_text())
            matrix=record['usb_matrix']
            from test_usb_audio_integration import CASES
            validate_campaign(matrix, expected_cases=CASES)
            record['codec_checks']['usb-matrix']={'status':'PASS',
                'log_sha256':sha(attempt/'usb-matrix.log'),
                'record_path':str(matrix_record),'record_sha256':sha(matrix_record)}
        record['scope'] = 'Full host native GUI compilation and targeted real-codec correctness; no physical audio, RF or timing qualification'
    if not host:
        run([sdk / 'bin/arm-buildroot-linux-gnueabihf-readelf', '-h', '-A', '-d', gui], 'elf.txt')
    after, _ = source_snapshot()
    record['source_changed_during_build'] = sorted(name for name in set(before) | set(after)
                                                  if before.get(name) != after.get(name))
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,timeout=10).strip() != revision:
        record['source_changed_during_build'].append('git HEAD')
    if record['source_changed_during_build']:
        raise ValueError('source changed during build; repeat after edits finish')
    record['status'] = 'PASS'


if __name__ == '__main__':
    main()
