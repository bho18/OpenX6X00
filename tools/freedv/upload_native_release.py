#!/usr/bin/env python3
"""Deliver immutable tested assets to one fork draft; never publish a release.

The delivery branch is separate from the pinned firmware source. No compilation,
radio access, upstream write, credential extraction or release publication occurs.
All downloads and local records are checked before the first asset upload.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile

REPOSITORY = 'bho18/OpenX6X00'
SOURCE = '2083e3e7f819e2b3fc4feaf27e5c9a007c996308'
RUN = 40744291361
RELEASE = 427060153
TAG = 'freedv-native-diagnostics-20261028.1'
BRANCH = 'refs/heads/delivery/x6200-native-release'
MODEL = '4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453'
FIXTURE = '0a5b202f2637ecd4ca78f2c2d2c4e17aff7d6ba12a07f9c612d8df9cbd26e034'
SDK = 'bc253744032e1acfbb219fb22d07d3ec8024269da84dc02404048fc365a0b528'
BUILDROOT = '8b7ca9d6d4e82ade0fc7654c46b9f8688689a4a3'
SUBMODULE = 'cc34b5b2abf2588f0ef55e46a4fe85a60c19ba1f'
# name, artifact ID, ZIP SHA256, owning run, owning source commit
ARTIFACTS = (
    ('x6200-native-diagnostic-images', 12841249525, '44d3e9e0b39fe2b1f25774d2da35dd2b31484947d1d9c1a1061200a09509a7ad', RUN, SOURCE),
    ('x6200-native-codec-package-and-sources', 12841579075, 'a55e40c14b4186b34a3ab304de1b3da3a5428ddb4d1d74687eb253aa1f2b8e26', RUN, SOURCE),
    ('x6200-native-matching-sdk', 12842597598, '2279c39f2906e16172a4ba45212d4c02139851946cdfc6acca9b71a5a7da01ec', RUN, SOURCE),
    ('x6200-freedv-baseline', 12701486870, '0fdfdd2bc9f67cda6137a8adb8e60e829b2055db2506f1184cf8c3b924311b01', 40417980169, 'd72591212f258eb1572c1a98226c5e12b6d145ca'),
    ('x6200-freedv-long-sync-fixture', 12782732680, '47ee4e99cd6b057ad6f8d52c862a8b38383cf61027be305ea84bba9f93a34e71', 40610916169, '1cec1046c873d0376d4215954a44b6ff0b49ed2a'),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def api(path):
    result = subprocess.run(['gh', 'api', 'repos/' + REPOSITORY + '/' + path], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('GitHub metadata request failed (credentials/redirects omitted): ' + path)
    return json.loads(result.stdout)


def safe_name(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and '..' not in path.parts and '\\' not in name
            and ':' not in name and path.as_posix() == name.rstrip('/') and path.as_posix() != '.',
            'unsafe archive/checksum path')
    return path


def extract_zip(archive, destination, max_bytes=8 * 1024**3):
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        require(len(members) <= 4096 and sum(m.file_size for m in members) <= max_bytes,
                'ZIP size/member limit exceeded')
        seen = set()
        for member in members:
            relative = safe_name(member.filename)
            require(relative.as_posix() not in seen, 'duplicate ZIP entry')
            seen.add(relative.as_posix())
            mode = stat.S_IFMT(member.external_attr >> 16)
            require(mode in (0, stat.S_IFREG, stat.S_IFDIR), 'ZIP link/special entry refused')
            target = destination / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as original, target.open('xb') as output:
                    shutil.copyfileobj(original, output)


def checksum_lines(text):
    found = {}
    for line in text.splitlines():
        expected, name = line.split('  ', 1)
        safe_name(name)
        require(re.fullmatch('[0-9a-f]{64}', expected) and name not in found, 'invalid/duplicate checksum record')
        found[name] = expected
    require(bool(found), 'empty checksum list')
    return found


def verify_checksums(directory, complete=True, subset=False):
    listed = checksum_lines((directory / 'SHA256SUMS').read_text())
    files = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()}
    if complete:
        require(files == set(listed) | {'SHA256SUMS'}, 'unlisted/missing artifact files')
    for name, expected in listed.items():
        path = directory / name
        if subset and not path.exists():
            continue
        require(path.is_file() and sha(path) == expected, 'artifact checksum differs: ' + name)
    return listed


def json_file(path):
    return json.loads(path.read_text())


def metadata_matches(metadata, identity):
    name, identifier, digest, run, source = identity
    require(metadata.get('id') == identifier and metadata.get('name') == name
            and metadata.get('expired') is False and metadata.get('digest') == 'sha256:' + digest,
            'immutable artifact identity/digest differs: ' + name)
    owner = metadata.get('workflow_run', {})
    require(owner.get('id') == run and owner.get('head_sha') == source, 'artifact owning run/source differs: ' + name)


def fetch_artifact(identity, work, cache=None):
    name, identifier, expected, _, _ = identity
    metadata = api('actions/artifacts/' + str(identifier))
    metadata_matches(metadata, identity)
    target = work / (name + '.zip')
    cached = cache / target.name if cache else None
    if cached and cached.is_file() and not cached.is_symlink():
        require(sha(cached) == expected, 'cached artifact checksum differs: ' + name)
        shutil.copyfile(cached, target)
    else:
        with target.open('xb') as stream:
            result = subprocess.run(['gh', 'api', 'repos/' + REPOSITORY + '/actions/artifacts/' + str(identifier) + '/zip'],
                                    stdout=stream, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError('Artifact download failed; signed redirect details omitted: ' + name)
    require(sha(target) == expected, 'downloaded artifact ZIP digest differs: ' + name)
    destination = work / 'extracted' / name
    extract_zip(target, destination)
    return target, destination, metadata


def check_recovery(record):
    require(record.get('sdk_archive_sha256') == SDK and record.get('buildroot_commit') == BUILDROOT
            and record.get('buildroot_submodule') == SUBMODULE, 'matching SDK identity differs')


def verify_images(directory):
    verify_checksums(directory)
    record = json_file(directory / 'release-manifest.json')
    require(record.get('firmware_commit') == SOURCE and record.get('software_gates_passed') is True
            and record.get('hardware_qualified') is False and record.get('model_sha256') == MODEL,
            'image release identity/gates differ')
    check_recovery(record['baseline_recovery'])
    require(sha(directory / 'sources.json') == record['source_manifest_sha256'], 'source manifest differs')
    reports = {}
    for filename, key in [('host-gates.json', 'host_gates_sha256'), ('host-codec-gates.json', 'host_codec_gates_sha256'),
                          ('codec-gates.json', 'codec_gates_sha256')]:
        path = directory / filename
        require(sha(path) == record[key], 'gate report hash differs: ' + filename)
        reports[filename] = json_file(path)
        require(reports[filename].get('status') == 'PASS' and reports[filename].get('firmware_commit') == SOURCE,
                'gate source/status differs: ' + filename)
    host = reports['host-gates.json']
    require(host['gui_smoke']['diagnostic_panel']['status'] == 'PASS', 'local diagnostic entry gate missing')
    required = {'freedv_native_radio_backend', 'freedv_native_audio_adapter', 'freedv_session_controller',
                'freedv_capture_dispatch', 'freedv_audio_playback', 'freedv_writer_preferences',
                'freedv_input_fence', 'freedv_front_input', 'freedv_remote_input_fence', 'freedv_native_coordinator'}
    require(required <= set(host['executed_diagnostic_tests']), 'mandatory integration tests missing')
    require({v['variant'] for v in record['variants']} == {'normal', 'diagnostic'} and len(record['variants']) == 2,
            'paired image list differs')
    for variant in ('normal', 'diagnostic'):
        path = directory / ('x6200-' + variant + '.img')
        image = json_file(path.with_suffix('.json'))
        require(image.get('firmware_commit') == SOURCE and image.get('variant') == variant
                and image.get('hardware_qualified') is False and image.get('signature_key_preserved') is True
                and image.get('ready_for_guided_hardware_tests') is (variant == 'diagnostic')
                and image['abi'].get('status') == 'PASS' and image['abi'].get('hardware_qualified') is False,
                'image identity/signature/ABI markers differ: ' + variant)
        require(image['image_sha256'] == sha(path), 'image bytes differ: ' + variant)
        require(json_file(path.with_suffix('.abi.json')) == image['abi'], 'ABI sidecar differs: ' + variant)
        selected = next(v for v in record['variants'] if v['variant'] == variant)
        require(selected['path'] == path.name and selected['image_sha256'] == image['image_sha256'], 'variant manifest differs')
        flag = 'ON' if variant == 'diagnostic' else 'OFF'
        cache = (directory / ('cmake-' + variant + '.txt')).read_text()
        require('ENABLE_FREEDV_DIAGNOSTICS:BOOL=' + flag in cache and 'ENABLE_FREEDV_NATIVE_CODEC:BOOL=' + flag in cache,
                'image compile options differ')
    return reports['codec-gates.json'], record


def verify_codec(directory, images, stream_gate):
    require({p.name for p in directory.iterdir()} == {'freedv-benchmark-package.tar.gz', 'freedv-benchmark-sources.tar.gz'},
            'codec artifact files differ')
    with tarfile.open(directory / 'freedv-benchmark-package.tar.gz', 'r:gz') as archive:
        members = archive.getmembers()
        require(len(members) < 4096 and sum(m.size for m in members) < 2 * 1024**3, 'codec package size exceeded')
        files = {}
        for member in members:
            safe_name(member.name)
            require(member.isdir() or member.isfile(), 'codec package link/special entry refused')
            require(member.name not in files, 'duplicate codec package entry')
            if member.isfile():
                files[member.name] = member
        prefix = 'freedv-benchmark/'
        manifest_bytes = archive.extractfile(files[prefix + 'package-manifest.json']).read()
        codec = json.loads(manifest_bytes)
        sums = checksum_lines(archive.extractfile(files[prefix + 'SHA256SUMS']).read().decode())
        require(set(files) == {prefix + name for name in sums} | {prefix + 'SHA256SUMS'}, 'codec package listing differs')
        for name, expected in sums.items():
            digest = hashlib.sha256()
            with archive.extractfile(files[prefix + name]) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            require(digest.hexdigest() == expected, 'codec package checksum differs: ' + name)
    require(codec.get('target') == 'x6200-buildroot' and codec.get('firmware_commit') == SOURCE
            and codec.get('radio_control') is False and codec.get('hardware_qualified') is False
            and codec.get('opus_model_sha256') == MODEL and codec['native_diagnostic_fixture']['sha256'] == FIXTURE,
            'codec source/model/fixture identity differs')
    require(stream_gate.get('dependency_package_firmware_commit') == SOURCE
            and stream_gate.get('dependency_package_manifest_sha256') == hashlib.sha256(manifest_bytes).hexdigest(),
            'SDK stream dependency package differs')
    require(codec['sources'] == json_file(images / 'sources.json'), 'codec/image source pins differ')
    require(sha(directory / 'freedv-benchmark-sources.tar.gz') == codec['corresponding_source_archive_sha256'],
            'corresponding codec sources archive differs')
    for key in ('rade_acquisition_experiment', 'rade_pipeline_experiment', 'rade_acquisition_parallel_experiment'):
        require(codec[key].get('enabled') is True and bool(codec[key].get('sdk_validation')), 'SDK correctness gate absent: ' + key)


def verify_sdk(directory, release):
    archive = list(directory.rglob('*sdk*.tar.gz'))
    require(len(archive) == 1 and sha(archive[0]) == SDK, 'matching SDK archive differs')
    sums = verify_checksums(archive[0].parent, complete=False, subset=True)
    require(sums.get(archive[0].name) == SDK, 'SDK checksum-list record differs')
    require((archive[0].parent / 'buildroot-commit.txt').read_text().strip() == BUILDROOT, 'SDK Buildroot commit differs')
    recovery = json_file(directory / 'recovery.json')
    check_recovery(recovery)
    require(recovery == release['baseline_recovery'], 'SDK recovery report differs from image release')


def verify_reference(directory, identity):
    verify_checksums(directory, complete=identity[1] != 12701486870)
    if identity[1] == 12701486870:
        archives = list(directory.glob('*sdk*.tar.gz'))
        require(len(archives) == 1 and sha(archives[0]) == SDK, 'baseline reference SDK differs')
        require((directory / 'buildroot-commit.txt').read_text().strip() == BUILDROOT, 'baseline reference Buildroot differs')
    else:
        manifest = json_file(directory / 'fixture-manifest.json')
        source = directory / 'fixture.raw'
        require(sha(source) == 'c58f6ba9ed88c785712f4591e42b042ff71e81e376653c277ca8d9d6c7c95e37'
                and manifest['fixture']['sha256'] == sha(source) and manifest['fixture']['first_eoo_sample'] > 132 * 8000,
                'continuous fixture reference differs')
        with source.open('rb') as stream:
            require(hashlib.sha256(stream.read(132 * 8000 * 2)).hexdigest() == FIXTURE, 'continuous reference prefix differs')


def release_guard(record):
    require(record.get('id') == RELEASE and record.get('tag_name') == TAG and record.get('target_commitish') == SOURCE
            and record.get('draft') is True and record.get('prerelease') is True, 'release is not the exact expected draft')


def existing_assets_match(existing, files):
    desired = {path.name: path for path in files}
    found = set()
    for asset in existing:
        name = asset.get('name')
        require(name in desired and name not in found, 'unexpected/duplicate existing release asset')
        found.add(name)
        path = desired[name]
        require(asset.get('state') == 'uploaded' and asset.get('size') == path.stat().st_size
                and asset.get('digest') == 'sha256:' + sha(path), 'existing release asset differs: ' + name)
    return found


def prepare(work, cache=None):
    run = api('actions/runs/' + str(RUN))
    require(run.get('id') == RUN and run.get('head_sha') == SOURCE and run.get('status') == 'completed'
            and run.get('conclusion') == 'success' and run.get('head_branch') == 'experiment/x6200-native-diagnostics'
            and run.get('path') == '.github/workflows/main.yml', 'image run source/status differs')
    fetched = [fetch_artifact(identity, work, cache) for identity in ARTIFACTS]
    images, codec_dir, sdk_dir, baseline, fixture = [item[1] for item in fetched]
    stream_gate, image_record = verify_images(images)
    verify_codec(codec_dir, images, stream_gate)
    verify_sdk(sdk_dir, image_record)
    verify_reference(baseline, ARTIFACTS[3])
    verify_reference(fixture, ARTIFACTS[4])
    for source, name in [(images / 'RADIO-TEST.md', 'RADIO-TEST.md'), (images / 'BUILD.md', 'BUILD.md'),
                         (images / 'SHA256SUMS', 'IMAGE-SHA256SUMS'), (images / 'release-manifest.json', 'release-manifest.json')]:
        shutil.copyfile(source, work / name)
    manifest = {'schema_version': 1, 'firmware_commit': SOURCE, 'image_workflow_run_id': RUN,
                'release_id': RELEASE, 'tag_name': TAG, 'hardware_qualified': False, 'model_sha256': MODEL,
                'fixture_sha256': FIXTURE, 'sdk_archive_sha256': SDK,
                'artifacts': [{'id': metadata['id'], 'name': metadata['name'], 'path': archive.name,
                               'zip_sha256': sha(archive), 'bytes': archive.stat().st_size}
                              for archive, _, metadata in fetched]}
    manifest_path = work / 'delivery-manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    files = [item[0] for item in fetched] + [work / name for name in
            ('RADIO-TEST.md', 'BUILD.md', 'IMAGE-SHA256SUMS', 'release-manifest.json', 'delivery-manifest.json')]
    sums = work / 'DOWNLOAD-SHA256SUMS'
    sums.write_text(''.join(sha(path) + '  ' + path.name + '\n' for path in files))
    files.append(sums)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True, help='new local staging directory')
    parser.add_argument('--cache-dir', type=Path, help='optional verified ZIP cache; API metadata and hashes remain mandatory')
    parser.add_argument('--validate-only', action='store_true', help='check everything locally without uploading')
    args = parser.parse_args()
    if not args.validate_only:
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
                and os.environ.get('GITHUB_REF') == BRANCH and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch',
                'uploads require the fork manual delivery workflow')
    release_guard(api('releases/' + str(RELEASE)))
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    files = prepare(work, args.cache_dir.resolve() if args.cache_dir else None)
    release_guard(api('releases/' + str(RELEASE)))
    existing = existing_assets_match(api('releases/' + str(RELEASE) + '/assets?per_page=100'), files)
    if not args.validate_only:
        for path in files:
            if path.name in existing:
                continue
            release_guard(api('releases/' + str(RELEASE)))
            result = subprocess.run(['gh', 'release', 'upload', TAG, str(path), '--repo', REPOSITORY],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError('Release asset upload failed; credentials/redirect details omitted: ' + path.name)
        require(existing_assets_match(api('releases/' + str(RELEASE) + '/assets?per_page=100'), files)
                == {path.name for path in files}, 'uploaded release asset set is incomplete')
    release_guard(api('releases/' + str(RELEASE)))
    verification = {'status': 'PASS', 'firmware_commit': SOURCE, 'workflow_run_id': RUN,
                    'release_id': RELEASE, 'uploaded': not args.validate_only, 'draft_retained': True,
                    'assets': [{'name': path.name, 'sha256': sha(path), 'bytes': path.stat().st_size} for path in files]}
    (work / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    print('PASS: verified', len(files), 'delivery assets;', 'draft uploads complete' if not args.validate_only else 'validation only; no uploads')


if __name__ == '__main__':
    main()
