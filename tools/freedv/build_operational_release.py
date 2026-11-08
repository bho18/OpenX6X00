#!/usr/bin/env python3
"""Build, verify and package one committed USB FreeDV candidate, without radio access.

Stages are independently resumable: builds, verify (requires actual CI), and
package. A build-only run is not release qualification. Dependencies and work
directories are explicit; historical workspace evidence is never a prerequisite.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import wave
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = 'bho18/OpenX6X00'
BASELINE_SHA = 'a11d8fa49156a8a9ada151e2472c1da1fde2115c22563b331270035f6b6285ab'
JOBS = {'windows-runner', 'linux-inventory', 'usb-controller-regression'}
VARIANTS = {'host': 'host', 'arm': 'x6200-diagnostic', 'normal': 'x6200-normal',
            'x6100': 'x6100', 'host-native': 'host-native-validation'}
OBSERVER_VERSION = '2.3.5'
OBSERVER_WHEEL_SHA = '11e06aa0af8c0f05104d56450d6093ee639e15f24ecf62d417329d06e522e017'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
    os.replace(temporary, path)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def source_identity(expected):
    from build_native_development import source_snapshot
    from build_voice_candidate import verify_source_manifest
    revision = git('rev-parse', 'HEAD')
    if re.fullmatch('[0-9a-f]{40}', expected) is None or revision != expected:
        raise ValueError('The source is not the full expected frozen commit')
    if git('status', '--porcelain'):
        raise ValueError('Commit all source changes before release work')
    files, tree = source_snapshot()
    verify_source_manifest(ROOT, files, tree)
    return {'source_commit': revision, 'source_tree_sha256': tree,
            'source_files': files, 'source_file_count': len(files)}


def matching_identity(value, identity, build_id):
    for name in ('source_commit', 'source_tree_sha256'):
        if value.get(name) != identity[name]:
            raise ValueError('Evidence differs from frozen '+name)
    if value.get('build_id') != build_id:
        raise ValueError('Evidence differs from frozen build ID')


def run(command, log, timeout=7200, env=None):
    from usb_test_support import run_bounded
    log = Path(log)
    if log.exists():
        raise ValueError('Preserve the previous stage log; select a fresh work directory: '+str(log))
    result = run_bounded(list(map(str, command)), log, timeout, cwd=ROOT, env=env)
    if result.get('timed_out') or result.get('exit_code') != 0:
        raise ValueError('Stage failed; evidence retained in '+str(log))
    return result


def build_commands(args):
    commands = {}
    for name, variant in VARIANTS.items():
        command = [sys.executable, ROOT/'tools/freedv/build_native_development.py',
                   '--variant', variant, '--work', args.work/name,
                   '--build-id', args.build_id, '--jobs', args.jobs, '--retire-intermediates-before-checks']
        if name == 'host':
            command += ['--control-headers', args.control_headers]
        elif name == 'host-native':
            command += ['--usb-test', '--codec', args.host_codec,
                        '--codec-fixture', args.fixtures/'rade-modem.raw',
                        '--usb-resource-reclaim-root', args.work]
        else:
            command += ['--sdk', args.sdk]
            if args.runtime_inputs:
                command += ['--runtime-inputs', args.runtime_inputs]
            if name != 'normal':
                command += ['--codec', args.codec]
            if name == 'arm':
                command += ['--usb-voice']
            if name == 'x6100':
                command += ['--x6100-prefix', args.x6100_prefix]
        commands[name] = list(map(str, command))
    return commands


def builds(args, identity):
    validate_observer(args.observer_provenance)
    commands = build_commands(args)
    for name in ('host', 'arm', 'normal', 'x6100'):
        run(commands[name], args.work/(name+'-build.log'))
    # Reserve the final CPU of a three-CPU parent context for an independent
    # observer. The existing campaign applies its unchanged 3/4 policy to the
    # first three CPUs, selecting two. No machine-wide affinity is changed.
    affinity = sorted(os.sched_getaffinity(0))[:3]
    if len(affinity) != 3:
        raise ValueError('Three CPUs are required for two-CPU qualification plus its independent observer')
    area = args.work/'host-isolation'
    area.mkdir(exist_ok=False)
    ready, stop, output = (area/name for name in ('ready.json', 'ended.json', 'clock.json'))
    probe_command = [sys.executable, ROOT/'tools/freedv/operational_clock_probe.py',
                     '--ready', ready, '--stop', stop, '--output', output, '--cpu', affinity[-1],
                     '--source-commit', identity['source_commit'],
                     '--source-tree-sha256', identity['source_tree_sha256'], '--build-id', args.build_id]
    with (area/'probe.log').open('x') as log:
        probe = subprocess.Popen(list(map(str, probe_command)), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            until = time.monotonic()+10
            while not ready.exists() and probe.poll() is None and time.monotonic() < until:
                time.sleep(.02)
            if not ready.exists():
                raise ValueError('Independent observer did not become ready')
            try:
                run(['taskset', '-c', ','.join(map(str, affinity)), *commands['host-native']],
                    args.work/'host-native-build.log', timeout=7200)
            finally:
                save(stop, {**identity, 'build_id': args.build_id, 'ended_monotonic_ns': time.monotonic_ns()})
            if probe.wait(timeout=10) != 0:
                raise ValueError('Independent observer failed')
        finally:
            if probe.poll() is None:
                probe.terminate()
                probe.wait(timeout=10)
    source_identity(args.expected_commit)


def validate_ci(value, identity):
    run_record = value.get('run', {})
    workflow_commit = run_record.get('headSha', run_record.get('head_sha'))
    url = run_record.get('url', run_record.get('html_url', ''))
    if (run_record.get('conclusion') != 'success' or
            not url.startswith('https://github.com/'+REPOSITORY+'/actions/runs/') or
            {job.get('name') for job in value.get('jobs', [])} != JOBS or
            any(job.get('conclusion') != 'success' for job in value['jobs'])):
        raise ValueError('The three required fork CI jobs did not pass')
    receipts = value.get('tested_source_receipts', {})
    if set(receipts) != JOBS:
        raise ValueError('CI requires downloaded actual-source receipts from all three jobs')
    for job, entry in receipts.items():
        path = Path(entry['path'])
        receipt = read(path)
        if sha(path) != entry['sha256'] or receipt != entry['receipt']:
            raise ValueError('CI receipt changed: '+job)
        if (receipt.get('schema') != 'x6200.usb.ci-tested-source.v1' or
                receipt.get('source_commit') != identity['source_commit'] or
                receipt.get('expected_source_commit') != identity['source_commit'] or
                receipt.get('source_tree_sha256') != identity['source_tree_sha256'] or
                receipt.get('source_files') != identity['source_files'] or
                receipt.get('source_file_count') != len(identity['source_files']) or
                receipt.get('source_worktree_dirty') is not False or
                receipt.get('workflow_commit') != workflow_commit or receipt.get('job') != job):
            raise ValueError('CI did not test the exact clean source: '+job)
    model = value.get('model_acquisition_receipt', {})
    path = Path(model.get('path', ''))
    pins = read(ROOT/'tools/freedv/sources.json')
    if not path.is_file() or sha(path) != model.get('sha256'):
        raise ValueError('Actual CI model acquisition receipt is absent or changed')
    receipt = read(path)
    if (receipt.get('sha256') != pins['opus']['model_sha256'] or
            receipt.get('expected_sha256') != pins['opus']['model_sha256'] or
            receipt.get('tested_source_commit') != identity['source_commit'] or
            receipt.get('workflow_commit') != workflow_commit or
            receipt.get('product_source_modified') is not False or
            receipt.get('unchanged_opus_self_verification') is not True):
        raise ValueError('CI did not acquire the pinned unchanged Opus model')
    return {'status': 'PASS', 'run_url': url, 'workflow_commit': workflow_commit,
            'tested_source_commit': identity['source_commit'], 'evidence': value}


def validate_dependencies(args, records):
    provenance = read(args.dependency_provenance)
    if sha(args.baseline) != BASELINE_SHA:
        raise ValueError('The verified Linux baseline changed')
    for name, expected in provenance['host_codec_build']['hashes'].items():
        if sha(args.host_codec/name) != expected:
            raise ValueError('The reused host codec dependency changed: '+name)
    for name, expected in provenance['rade_candidate_source_files'].items():
        if sha(args.codec/'sources/rade_c'/name) != expected:
            raise ValueError('The reused ARM codec source changed: '+name)
    pins = read(ROOT/'tools/freedv/sources.json')
    if provenance['opus_model_sha256'] != pins['opus']['model_sha256']:
        raise ValueError('Codec model provenance differs from source pins')
    checked = {}
    for record in records.values():
        for entry in [*record.get('binaries', {}).values(), *record.get('codec_inputs', {}).values()]:
            if sha(entry['path']) != entry['sha256']:
                raise ValueError('A recorded build input changed: '+entry['path'])
            checked[entry['path']] = entry['sha256']
        if 'compiler' in record and sha(Path(record['compiler']['path']).resolve()) != record['compiler']['sha256']:
            raise ValueError('Recorded compiler identity changed')
    return {'status': 'PASS', 'scope': 'Unchanged dependencies retain their original provenance; no new hardware measurement',
            'baseline_sha256': BASELINE_SHA, 'original_provenance_sha256': sha(args.dependency_provenance),
            'original_provenance': provenance, 'rechecked_artifact_sha256': checked}


def validate_observer(provenance_path):
    if not provenance_path:
        raise ValueError('Qualification requires pinned numerical observer provenance')
    import numpy
    value = read(provenance_path)
    wheel = Path(value['wheel']['path'])
    if (numpy.__version__ != OBSERVER_VERSION or value.get('pin') != 'numpy=='+OBSERVER_VERSION or
            sha(wheel) != OBSERVER_WHEEL_SHA or value['wheel'].get('sha256') != OBSERVER_WHEEL_SHA or
            value['wheel'].get('official_sha256') != OBSERVER_WHEEL_SHA or
            str(Path(numpy.__file__).resolve()) != value['import_proof']['numpy_module']):
        raise ValueError('Qualification numerical observer differs from its pinned wheel identity')
    installed = Path(numpy.__file__).resolve().parent.parent
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir() or member.filename.endswith('.dist-info/RECORD'):
                continue
            relative = Path(member.filename)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Observer wheel member escapes install directory')
            path = installed/relative
            if not path.is_file() or sha(path) != hashlib.sha256(archive.read(member)).hexdigest():
                raise ValueError('Installed observer differs from pinned wheel: '+str(relative))
    return {'status': 'PASS', 'numpy_version': OBSERVER_VERSION, 'wheel_sha256': OBSERVER_WHEEL_SHA,
            'provenance_sha256': sha(provenance_path), 'provenance': value}


def validate_probe_context(clock, matrix, identity, build_id):
    matching_identity(clock, identity, build_id)
    matching_identity(clock.get('stop', {}), identity, build_id)
    scheduling = matrix['identity']['host_resource_configuration']['cpu_scheduling']
    selected = scheduling['selected_affinity']
    reserved = sorted(set(scheduling['inherited_affinity'])-set(selected))
    quota = scheduling['quota_us']
    expected_quota = ('max' if quota is None else str(quota))+' '+str(scheduling['period_us'])
    if (not reserved or clock.get('cpu_affinity') != [reserved[-1]] or
            clock.get('cpu_max') != expected_quota or
            clock.get('probe_script_sha256') != identity['source_files'].get('tools/freedv/operational_clock_probe.py') or
            type(clock['stop'].get('ended_monotonic_ns')) is not int or
            clock['stop']['ended_monotonic_ns'] < matrix['host_resources']['end']['after']['observed_monotonic_ns']):
        raise ValueError('Independent observer source, CPU context or completion differs from qualification')


def collect_smokes(args, records, checks):
    from validate_native_host import smoke_gui
    host = args.work/'host-native/build'
    data = host/'data'
    if data.exists():
        raise ValueError('Host GUI smoke data already exists; preserve it and use a fresh verification directory')
    data.mkdir()
    shutil.copy2(host/'params.db', data/'params.db')
    old_library = os.environ.get('LD_LIBRARY_PATH', '')
    os.environ['LD_LIBRARY_PATH'] = str(args.host_codec/'rade_c/src')+os.pathsep+old_library
    simulator = checks/'host-gui'
    simulator.mkdir()
    gui = smoke_gui(host/'src/x6100_gui', simulator, False)
    cli_log = checks/'evidence-cli.log'
    command = [sys.executable, ROOT/'tools/freedv/test_usb_rf_evidence.py', '-v']
    run(command, cli_log)
    tests = re.findall(r'^(test_[A-Za-z0-9_]+).*? \.\.\. ok$', cli_log.read_text(), re.MULTILINE)
    if not any('cli' in name for name in tests):
        raise ValueError('No actual evidence CLI subprocess test was observed')
    cli = {'status': 'PASS', 'command': list(map(str, command)), 'tests': tests,
           'log_sha256': sha(cli_log), 'output': cli_log.read_text()}
    runner = Path(records['arm']['binaries']['freedv_voice_runner']['path'])
    smoke = checks/'arm-runner'
    smoke.mkdir()
    sysroot = args.sdk/'arm-buildroot-linux-gnueabihf/sysroot'
    library = args.codec/'rade_c/src/librade.so.0.1'
    command = ['qemu-arm', '-cpu', 'cortex-a7', '-L', sysroot, '-E',
               'LD_LIBRARY_PATH='+':'.join(map(str, [library.parent, sysroot/'usr/lib', sysroot/'lib'])),
               runner, '--rx', args.fixtures/'rade-continuous.raw', '--output', smoke,
               '--rate', '8000', '--seconds', '4', '--unpaced']
    run(command, smoke/'run.log')
    stats = read(smoke/'engine-stats.json')
    if stats.get('modem_blocks', 0) < 1 or stats.get('failures', 1):
        raise ValueError('ARM runner did not execute actual modem processing successfully')
    arm = {'status': 'PASS', 'runner_sha256': sha(runner), 'rade_sha256': sha(library),
           'fixture_sha256': sha(args.fixtures/'rade-continuous.raw'), 'stats': stats,
           'command': list(map(str, command)), 'hardware_qualified': False,
           'scope': 'ARM emulated processing; no physical audio/RF or radio timing qualification'}
    return gui, cli, arm


def offair_smoke(args, record, checks):
    import usb_rf_evidence as subject
    case = Path(record['usb_matrix']['cases']['operational-hardening-radio-recover']['runs'][0]['case_record_path']).parent
    runtime = case/'result'
    runner = Path(record['binaries']['freedv_voice_runner']['path'])
    binary = Path(record['binaries']['freedv_usb_audio_test']['path'])
    library = args.host_codec/'rade_c/src/librade.so.0.1'
    root = checks/'offair-helper'
    root.mkdir()
    (root/'empty-proc').mkdir()
    (root/'result').mkdir()
    with wave.open(str(root/'public-modem-fixture.wav'), 'wb') as out:
        out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        out.writeframes((args.fixtures/'rade-modem.raw').read_bytes())
    manifest = {'gui_sha256': sha(binary), 'usb_startup_contract_version': 4,
                'usb_capture_open_contract_version': 1, 'usb_output_contract_version': 1,
                'usb_refinement_contract_version': 2, 'on_device_readiness_required': True,
                'firmware_commit': record['source_commit'], 'build_id': args.build_id,
                'runner_sha256': sha(runner), 'rade_sha256': sha(library)}
    mapping = {'/tmp': runtime, '/proc': root/'empty-proc', '/mnt/freedv-usb-incidents': runtime/'incidents',
               '/opt/freedv-benchmark/bin/freedv_voice_runner': runner,
               '/opt/freedv-benchmark/lib/librade.so.0.1': library}
    def inventory(**kwargs):
        if kwargs.get('expected_gui') != sha(binary):
            raise ValueError('Controller inventory binding differs')
        return {'status': 'USB_DUPLEX_CANDIDATE', 'scope': 'Host inventory adapter; no radio'}
    def diagnostic(command, **kwargs):
        return {'command': list(command), 'exit_code': 127, 'stdout': '', 'stderr': 'Host adapter; no radio',
                'timed_out': False, 'output_truncated': False}
    with patch.object(subject, 'Path', side_effect=lambda value: mapping.get(str(value), Path(value))), \
            patch.object(subject, 'collect', side_effect=inventory), patch.object(subject, 'command', side_effect=diagnostic):
        result = subject.collect_evidence(root/'result', manifest, root/'public-modem-fixture.wav')
    if (result.get('status') != 'RF_RECORDING_DECODED_LISTENING_REQUIRED' or
            result['stages']['startup']['status'] != 'PASS' or
            result['stages']['qualification']['status'] != 'NOT_REQUESTED' or
            result['stages']['session']['status'] != 'COMPLETE' or result.get('rf_key_commands_sent') is not False):
        raise ValueError('Production collector/controller/actual-codec smoke failed')
    with wave.open(str(root/'result/offair-decode/offair-decoded.wav'), 'rb') as decoded:
        if decoded.getframerate() != 8000 or decoded.getnframes() < 1:
            raise ValueError('Production collector created no decoded speech')
    receipt = {'status': 'PASS', 'scope': 'Real host controller closure and codec, simulated inventory, public fixture; no radio',
               'controller_sha256': sha(binary), 'runner_sha256': sha(runner), 'rade_sha256': sha(library),
               'case_sha256': sha(case/'case-result.json'), 'codec_stats': result['offair_decoder_stats']}
    save(root/'record.json', receipt)
    return receipt


def verify(args, identity):
    from build_native_development import validate_build_record
    from build_voice_candidate import verify_usb_builds, verify_usb_integration
    from usb_test_campaign import validate_campaign
    from test_usb_audio_integration import CASES
    from operational_clock_probe import grade_window
    if not args.ci_result or not args.dependency_provenance:
        raise ValueError('Verification requires current CI evidence and pinned dependency provenance')
    records = {name: validate_build_record(read(args.work/name/'build-record.json')) for name in VARIANTS}
    for name, record in records.items():
        matching_identity(record, identity, args.build_id)
        if record['variant'] != VARIANTS[name] or record['source_files'] != identity['source_files']:
            raise ValueError('Build source/variant differs: '+name)
    matrix = records['host-native'].get('usb_matrix', {})
    validate_campaign(matrix, expected_cases=CASES)
    matching_identity(matrix, identity, args.build_id)
    if (set(records['host-native'].get('codec_checks', {})) !=
            {'voice-engine', 'native-stream', 'stream-fixture', 'codec-adapter', 'usb-matrix'}):
        raise ValueError('Real codec and complete integration checks are required')
    clock_path = args.work/'host-isolation/clock.json'
    clock = read(clock_path)
    validate_probe_context(clock, matrix, identity, args.build_id)
    resources = matrix['host_resources']
    window = grade_window(clock, resources['preflight']['before']['observed_monotonic_ns'],
                          resources['end']['after']['observed_monotonic_ns'])
    ci = validate_ci(read(args.ci_result), identity)
    dependencies = validate_dependencies(args, records)
    dependencies['host_numerical_observer'] = validate_observer(args.observer_provenance)
    checks = args.work/('verification-'+str(time.time_ns()))
    checks.mkdir()
    gui, cli, arm = collect_smokes(args, records, checks)
    offair = offair_smoke(args, records['host-native'], checks)
    dependency_path = checks/'dependency-provenance.json'
    save(dependency_path, dependencies)
    host_summary = {key: value for key, value in records['host-native'].items() if key != 'source_files'}
    host_summary['gui_smoke'] = gui
    verification = {'schema_version': 1, 'status': 'PASS', **identity, 'build_id': args.build_id,
        'scope': 'Source-bound software builds, complete simulated controller campaign, CI and emulated ARM; no physical qualification',
        'host_tests': records['host']['host_tests'], 'host_test_count': len(records['host']['host_tests']),
        'target_builds': {'x6200_diagnostic': 'PASS', 'x6200_normal': 'PASS', 'x6100_full_application': 'PASS', 'host_native_validation': 'PASS'},
        'host_native_validation': host_summary, 'arm_runner': arm, 'build_records': records,
        'build_records_sha256': {name: sha(args.work/name/'build-record.json') for name in records},
        'binaries': {'sdk': records['arm']['binaries']['gui']['sha256'],
                     'runner': records['arm']['binaries']['freedv_voice_runner']['sha256'],
                     'pulse_probe': records['arm']['binaries']['freedv_voice_pulse_probe']['sha256'],
                     'rade': arm['rade_sha256']},
        'dependency_provenance_sha256': sha(dependency_path), 'dependency_provenance': dependencies,
        'hardware_qualified': False, 'hardware_status': 'BETA_FIELD_VALIDATION_PENDING',
        'radio_accessed': False, 'baseband_modified': False,
        'usb_integration': {'status': 'PASS', 'contract_version': 4, 'usb_output_contract_version': 1,
            'usb_capture_open_contract_version': 1, 'usb_refinement_contract_version': 2,
            'matrix': matrix, 'evidence_cli': cli, 'helper_ci': ci['run_url'], 'helper_ci_result': ci,
            'helper_ci_result_sha256': sha(args.ci_result), 'offair_helper_smoke': offair,
            'host_qualification_context': {'status': 'PASS', 'neutral_clock': window,
                'neutral_clock_sha256': sha(clock_path), 'resource_configuration': resources['configuration']},
            'scope': 'Actual host codec/Pulse streams and simulated RF only'}}
    if args.runtime_package:
        from runtime_package import validate_package
        manifest_path = args.runtime_package/'runtime-manifest.json'
        runtime = validate_package(args.runtime_package, ROOT, args.sdk)
        if runtime.get('source_commit', runtime.get('firmware_commit')) != identity['source_commit']:
            raise ValueError('Runtime package belongs to another source revision')
        verification['runtime_package'] = {'path': str(args.runtime_package), 'manifest_sha256': sha(manifest_path), 'manifest': runtime}
        verification['runtime_package_sha256'] = sha(manifest_path)
        for name in ('arm', 'normal', 'x6100'):
            if records[name].get('runtime_inputs_sha256') != runtime['runtime_inputs_sha256']:
                raise ValueError('The current runtime input set was not used by ARM build: '+name)
    source_identity(args.expected_commit)
    verify_usb_integration(verification, 4)
    verify_usb_builds(verification, operational=True)
    save(checks/'software-verification.json', verification)
    save(args.work/'software-verification.json', verification)
    return verification


def package(args):
    from publish_operational_release import package_assets
    if not args.runtime_package:
        raise ValueError('The reviewed current runtime package is required')
    verification = read(args.work/'software-verification.json')
    matching_identity(verification, source_identity(args.expected_commit), args.build_id)
    runtime = verification.get('runtime_package', {})
    if runtime.get('manifest_sha256') != sha(args.runtime_package/'runtime-manifest.json'):
        raise ValueError('Runtime package changed after final verification')
    candidate = args.work/'candidate'
    command = [sys.executable, ROOT/'tools/freedv/build_voice_candidate.py', '--usb-voice',
               '--baseline', args.baseline, '--sdk', args.sdk,
               '--gui', args.work/'arm/build/src/x6100_gui',
               '--runner', args.work/'arm/build/freedv_voice_runner',
               '--pulse-probe', args.work/'arm/build/freedv_voice_pulse_probe',
               '--rade', args.codec/'rade_c/src/librade.so.0.1', '--fixtures', args.fixtures,
               '--web-wheel', args.web_wheel, '--runtime-package', args.runtime_package,
               '--verification', args.work/'software-verification.json',
               '--work', args.work/'image', '--output', candidate]
    if not args.web_wheel:
        raise ValueError('Build a current source-bound web wheel before packaging')
    run(command, args.work/'image-assembly.log')
    package_assets(candidate, args.output, args.build_id, args.expected_commit,
                   args.work/'image/validated-candidate.img')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('builds', 'verify', 'package', 'all'), default='all')
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--build-id', required=True)
    for name in ('work', 'output', 'sdk', 'codec', 'host-codec', 'fixtures', 'baseline', 'control-headers', 'x6100-prefix'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('ci-result', 'dependency-provenance', 'observer-provenance', 'runtime-inputs', 'runtime-package', 'web-wheel'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', args.build_id) or args.jobs < 1:
        parser.error('Use a simple unique build ID and a positive job count')
    for path in (args.work, args.output):
        if path == ROOT or ROOT in path.parents:
            parser.error('Work and output directories must be outside the source checkout')
    args.work.mkdir(parents=True, exist_ok=True)
    identity = source_identity(args.expected_commit)
    freeze_path = args.work/'frozen-source.json'
    frozen = {**identity, 'build_id': args.build_id}
    if freeze_path.exists() and read(freeze_path) != frozen:
        raise ValueError('Work directory belongs to another source/build; preserve it')
    save(freeze_path, frozen)
    if args.stage in ('builds', 'all'):
        builds(args, identity)
    if args.stage in ('verify', 'all'):
        verify(args, identity)
    if args.stage in ('package', 'all'):
        package(args)
    print('PASS: '+args.stage+' completed for '+args.expected_commit+'; physical radio qualification unchanged')


if __name__ == '__main__':
    if sys.flags.optimize:
        raise RuntimeError('Optimized Python is forbidden for release verification')
    main()
