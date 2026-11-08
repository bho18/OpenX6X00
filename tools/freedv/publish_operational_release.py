#!/usr/bin/env python3
"""Package or publish a verified candidate exclusively to the user's fork.

Publishing never overwrites an existing release. The downloaded release is
independently verified before this command reports delivery success.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from release_guide import render_guide, validate_guide_copies

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = 'bho18/OpenX6X00'
ASSETS = {'candidate-manifest.json', 'software-verification.json', 'candidate-source.bundle',
          'candidate-source.tar.xz', 'image-abi.json', 'START-HERE.md',
          'usb-rf-test-kit.zip', 'x6200-usb-rf-windows.zip', 'SHA256SUMS'}
CONTRACTS = {'usb_startup_contract_version': 4, 'usb_output_contract_version': 1,
             'usb_capture_open_contract_version': 1, 'usb_refinement_contract_version': 2}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def require_fork(repository):
    if repository != REPOSITORY:
        raise ValueError('Publication is restricted to '+REPOSITORY)


def simple_tag(tag):
    if not re.fullmatch(r'freedv-operational-beta-[0-9]{8}\.[1-9][0-9]*', tag):
        raise ValueError('Use a unique date-based operational beta tag')


def checksums(directory):
    lines = (directory/'SHA256SUMS').read_text().splitlines()
    expected = {}
    for line in lines:
        fields = line.split('  ', 1)
        if len(fields) != 2:
            raise ValueError('Malformed release checksum line')
        digest, name = fields
        if name not in ASSETS-{'SHA256SUMS'} or name in expected or re.fullmatch('[0-9a-f]{64}', digest) is None:
            raise ValueError('Unexpected/duplicate release checksum asset')
        if sha(directory/name) != digest:
            raise ValueError('Release checksum differs: '+name)
        expected[name] = digest
    if set(expected) != ASSETS-{'SHA256SUMS'}:
        raise ValueError('Incomplete release checksum set')
    return expected


def validate_source_archive(path, files):
    found = set()
    with tarfile.open(path, 'r:xz') as archive:
        for member in archive:
            if member.isdir():
                continue
            prefix = 'OpenX6X00/'
            if not member.isfile() or not member.name.startswith(prefix):
                raise ValueError('Corresponding-source archive contains an unexpected member')
            name = member.name[len(prefix):]
            if name not in files or name in found:
                raise ValueError('Unexpected/duplicate source file: '+name)
            stream = archive.extractfile(member)
            if hashlib.file_digest(stream, 'sha256').hexdigest() != files[name]:
                raise ValueError('Corresponding source differs: '+name)
            found.add(name)
    if found != set(files):
        raise ValueError('Corresponding-source archive is incomplete')


def validate_assets(directory, tag, expected_commit):
    from build_voice_candidate import verify_usb_builds, verify_usb_integration
    simple_tag(tag)
    directory = Path(directory).resolve()
    if {path.name for path in directory.iterdir()} != ASSETS or any(not (directory/name).is_file() or (directory/name).is_symlink() for name in ASSETS):
        raise ValueError('Publication requires exactly the nine regular release assets')
    hashes = checksums(directory)
    manifest = read(directory/'candidate-manifest.json')
    verification = read(directory/'software-verification.json')
    abi = read(directory/'image-abi.json')
    if (manifest.get('firmware_commit') != expected_commit or
            verification.get('source_commit') != expected_commit or
            manifest.get('build_id') != tag or verification.get('build_id') != tag or
            verification.get('status') != 'PASS' or abi.get('status') != 'PASS' or
            abi.get('gui_sha256') != manifest.get('gui_sha256') or
            manifest.get('source_tree_sha256') != verification.get('source_tree_sha256') or
            manifest.get('host_verification_sha256') != sha(directory/'software-verification.json') or
            not manifest.get('runtime_package_sha256') or
            manifest.get('runtime_package_sha256') != verification.get('runtime_package_sha256') or
            manifest.get('actual_image_abi') != 'PASS' or manifest.get('signature_key_preserved') is not True or
            manifest.get('operational_mode') is not True):
        raise ValueError('Release source, build, ABI or image identity is incomplete')
    for key, expected in CONTRACTS.items():
        if manifest.get(key) != expected:
            raise ValueError('Wrong image contract: '+key)
    # Serialized releases retain sealed campaign integrity even when validating
    # on another machine where the originating absolute paths do not exist.
    verify_usb_integration_serialized(verification)
    validate_operational_build_records(verification, tag, expected_commit)
    with zipfile.ZipFile(directory/'x6200-usb-rf-windows.zip') as archive:
        if set(archive.namelist()) != {'x6200-usb-rf.img', 'START-HERE.md', 'candidate-manifest.json'} or archive.testzip() is not None:
            raise ValueError('Image ZIP contents or CRC are invalid')
        with archive.open('x6200-usb-rf.img') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != manifest['image_sha256']:
                raise ValueError('Downloaded raw image differs from the manifest')
        if json.loads(archive.read('candidate-manifest.json')) != manifest:
            raise ValueError('Image ZIP embeds a different manifest')
        if archive.read('START-HERE.md') != (directory/'START-HERE.md').read_bytes():
            raise ValueError('Image ZIP embeds a different guide')
    with zipfile.ZipFile(directory/'usb-rf-test-kit.zip') as archive:
        if archive.testzip() is not None:
            raise ValueError('Helper kit CRC failed')
        kit = json.loads(archive.read('rf-test-manifest.json'))
        for name, digest in kit['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('Helper member differs: '+name)
        for key in ('image_sha256', 'gui_sha256', 'runner_sha256', 'rade_sha256', 'firmware_commit', 'build_id', *CONTRACTS):
            if kit.get(key) != manifest.get(key):
                raise ValueError('Image/helper binding differs: '+key)
    validate_guide_copies(directory)
    validate_source_archive(directory/'candidate-source.tar.xz', verification['source_files'])
    heads = subprocess.check_output(['git', 'bundle', 'list-heads', str(directory/'candidate-source.bundle')], text=True).splitlines()
    if heads != [expected_commit+' HEAD']:
        raise ValueError('Source bundle does not identify the tested product commit')
    with tempfile.TemporaryDirectory(prefix='freedv-public-source-') as temporary:
        subprocess.run(['git', 'init', '--bare', temporary], check=True, capture_output=True)
        subprocess.run(['git', '-C', temporary, 'bundle', 'verify', str(directory/'candidate-source.bundle')], check=True, capture_output=True)
        subprocess.run(['git', '-C', temporary, 'fetch', str(directory/'candidate-source.bundle'), 'HEAD'], check=True, capture_output=True)
        paths = subprocess.check_output(['git', '-C', temporary, 'log', '--all', '--format=', '--name-only', 'FETCH_HEAD'], text=True).splitlines()
        if any(name.startswith('docs/freedv/baseband-research/') for name in paths):
            raise ValueError('Private research history is reachable from the public source bundle')
    return {'status': 'PASS', 'source_commit': expected_commit, 'build_id': tag,
            'image_sha256': manifest['image_sha256'], 'asset_sha256': {**hashes, 'SHA256SUMS': sha(directory/'SHA256SUMS')}}


def validate_operational_build_records(verification, tag, expected_commit):
    from build_native_development import validate_build_record
    expected = {'host': 'host', 'arm': 'x6200-diagnostic', 'normal': 'x6200-normal',
                'x6100': 'x6100', 'host-native': 'host-native-validation'}
    if set(verification.get('build_records', {})) != set(expected):
        raise ValueError('All five sealed build variants are required')
    for name, record in verification['build_records'].items():
        validate_build_record(record, verify_files=False)
        if (record.get('variant') != expected[name] or record.get('source_commit') != expected_commit or
                record.get('source_tree_sha256') != verification.get('source_tree_sha256') or
                record.get('source_files') != verification.get('source_files') or record.get('build_id') != tag):
            raise ValueError('Serialized build differs from source: '+name)
        if name in ('arm', 'host-native') and record.get('usb_voice_enabled') is not True:
            raise ValueError('Operational USB voice feature was not enabled: '+name)


def verify_usb_integration_serialized(verification):
    from usb_test_campaign import validate_campaign
    from test_usb_audio_integration import CASES
    from build_voice_candidate import USB_REQUIRED_COVERAGE
    evidence = verification.get('usb_integration', {})
    matrix = evidence.get('matrix', {})
    if (evidence.get('status') != 'PASS' or matrix.get('status') != 'PASS' or
            matrix.get('complete_matrix') is not True or matrix.get('skipped') != [] or
            evidence.get('evidence_cli', {}).get('status') != 'PASS' or
            not USB_REQUIRED_COVERAGE <= set(matrix.get('coverage', [])) or
            evidence.get('helper_ci_result', {}).get('status') != 'PASS' or
            evidence.get('host_qualification_context', {}).get('status') != 'PASS' or
            evidence.get('offair_helper_smoke', {}).get('status') != 'PASS'):
        raise ValueError('Serialized release lacks complete software gates')
    for key, value in CONTRACTS.items():
        actual = evidence.get('contract_version') if key == 'usb_startup_contract_version' else evidence.get(key)
        if actual != value:
            raise ValueError('Serialized integration contract differs: '+key)
    for key in ('source_commit', 'source_tree_sha256', 'build_id'):
        if matrix.get(key) != verification.get(key):
            raise ValueError('Serialized campaign belongs to a different '+key)
    validate_campaign(matrix, expected_cases=CASES, verify_files=False)


def package_assets(candidate, output, tag, expected_commit, raw_image=None):
    candidate, output = Path(candidate).resolve(), Path(output).resolve()
    simple_tag(tag)
    manifest = read(candidate/'candidate-manifest.json')
    if manifest.get('firmware_commit') != expected_commit or manifest.get('build_id') != tag:
        raise ValueError('Candidate differs from requested source/build')
    image = Path(raw_image).resolve() if raw_image else candidate/'x6200-native-development.img'
    if not image.is_file() or sha(image) != manifest['image_sha256']:
        raise ValueError('The exact verified raw candidate image is required for packaging')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('candidate-manifest.json', 'software-verification.json', 'candidate-source.bundle', 'candidate-source.tar.xz'):
        shutil.copy2(candidate/name, output/name)
    shutil.copy2(candidate/'x6200-native-development.abi.json', output/'image-abi.json')
    guide = render_guide((ROOT/'docs/freedv/usb-rf-end-to-end.md').read_bytes(), tag)
    (output/'START-HERE.md').write_bytes(guide)
    subprocess.run([sys.executable, str(ROOT/'tools/freedv/build_usb_rf_kit.py'), '--image-manifest', str(candidate/'candidate-manifest.json'),
                    '--output', str(output/'usb-rf-test-kit.zip'), '--guide', str(output/'START-HERE.md')], check=True)
    with zipfile.ZipFile(output/'x6200-usb-rf-windows.zip', 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(image, 'x6200-usb-rf.img')
        archive.write(output/'START-HERE.md', 'START-HERE.md')
        archive.write(output/'candidate-manifest.json', 'candidate-manifest.json')
    (output/'SHA256SUMS').write_text(''.join(sha(output/name)+'  '+name+'\n' for name in sorted(ASSETS-{'SHA256SUMS'})))
    return validate_assets(output, tag, expected_commit)


def publish(directory, repository, tag, expected_commit, notes, download):
    require_fork(repository)
    if not Path(notes).is_file() or not Path(notes).read_text().strip():
        raise ValueError('Nonempty release notes are required before publication')
    download = Path(download).resolve()
    if download.exists() or (download.parent/'published-verification.json').exists():
        raise ValueError('Select fresh download/evidence paths before creating a release')
    validation = validate_assets(directory, tag, expected_commit)
    exists = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository], capture_output=True)
    if exists.returncode == 0:
        raise ValueError('Refusing to overwrite an existing release')
    command = ['gh', 'release', 'create', tag, '--repo', repository, '--target', expected_commit, '--prerelease',
               '--title', 'X6200 FreeDV operational beta '+tag, '--notes-file', str(notes),
               *[str(Path(directory)/name) for name in sorted(ASSETS)]]
    subprocess.run(command, check=True)
    return verify_published(directory, repository, tag, expected_commit, download, validation)


def verify_published(directory, repository, tag, expected_commit, download, validation=None):
    require_fork(repository)
    if validation is None:
        validation = validate_assets(directory, tag, expected_commit)
    download = Path(download).resolve()
    if download.exists() or (download.parent/'published-verification.json').exists():
        raise ValueError('Select fresh downloaded evidence paths; existing files are retained')
    download.mkdir(parents=True, exist_ok=False)
    subprocess.run(['gh', 'release', 'download', tag, '--repo', repository, '--dir', str(download)], check=True)
    remote = json.loads(subprocess.check_output(['gh', 'api', 'repos/'+repository+'/releases/tags/'+tag], text=True))
    if (remote.get('tag_name') != tag or remote.get('target_commitish') != expected_commit or remote.get('prerelease') is not True or
            {entry['name'] for entry in remote.get('assets', [])} != ASSETS):
        raise ValueError('Published release metadata differs from the tested product')
    downloaded = validate_assets(download, tag, expected_commit)
    for entry in remote['assets']:
        if entry.get('digest') != 'sha256:'+sha(download/entry['name']):
            raise ValueError('Published API asset digest differs: '+entry['name'])
    tag_sha = subprocess.check_output(['git', 'ls-remote', 'https://github.com/'+repository+'.git', 'refs/tags/'+tag], text=True).split()
    if not tag_sha or tag_sha[0] != expected_commit:
        raise ValueError('Published tag does not point to the tested product commit')
    if downloaded['asset_sha256'] != validation['asset_sha256']:
        raise ValueError('Published assets differ from locally verified assets')
    receipt = {**downloaded, 'release_url': remote['html_url'], 'repository': repository,
               'tag_commit': tag_sha[0], 'all_github_api_asset_digests': 'PASS'}
    with (download.parent/'published-verification.json').open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    return receipt


def complete_existing(directory, repository, tag, expected_commit, download):
    """Add only missing verified assets after an interrupted initial upload."""
    require_fork(repository)
    validation = validate_assets(directory, tag, expected_commit)
    download = Path(download).resolve()
    if download.exists() or (download.parent/'published-verification.json').exists():
        raise ValueError('Select fresh downloaded evidence paths before resuming upload')
    remote = json.loads(subprocess.check_output(['gh', 'api', 'repos/'+repository+'/releases/tags/'+tag], text=True))
    if (remote.get('tag_name') != tag or remote.get('target_commitish') != expected_commit or remote.get('prerelease') is not True):
        raise ValueError('Existing release is not the exact requested product prerelease')
    names = set()
    for entry in remote.get('assets', []):
        name = entry.get('name')
        if name not in ASSETS or name in names or entry.get('digest') != 'sha256:'+sha(Path(directory)/name):
            raise ValueError('Existing release asset differs; it will not be overwritten')
        names.add(name)
    missing = sorted(ASSETS-names)
    if missing:
        subprocess.run(['gh', 'release', 'upload', tag, '--repo', repository,
                        *[str(Path(directory)/name) for name in missing]], check=True)
    return verify_published(directory, repository, tag, expected_commit, download, validation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default=REPOSITORY)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--candidate', type=Path, help='Package this completed candidate before validation/publication')
    parser.add_argument('--raw-image', type=Path)
    parser.add_argument('--publish', action='store_true', help='Explicitly create a new prerelease and verify fresh downloads')
    parser.add_argument('--verify-existing', action='store_true', help='Verify an already-created release without creating or overwriting it')
    parser.add_argument('--complete-existing', action='store_true', help='After an interrupted upload, add only missing assets to the exact existing release')
    parser.add_argument('--notes', type=Path)
    parser.add_argument('--download', type=Path)
    args = parser.parse_args()
    require_fork(args.repository)
    if re.fullmatch('[0-9a-f]{40}', args.expected_commit) is None:
        parser.error('--expected-commit must be the full product commit')
    if args.candidate:
        package_assets(args.candidate, args.assets, args.tag, args.expected_commit, args.raw_image)
    if sum((args.publish, args.verify_existing, args.complete_existing)) > 1:
        parser.error('Choose publication, missing-asset completion, or verification of an existing release')
    if args.publish:
        if not args.notes or not args.download:
            parser.error('--publish requires --notes and a fresh --download directory')
        result = publish(args.assets, args.repository, args.tag, args.expected_commit, args.notes, args.download)
    elif args.verify_existing:
        if not args.download:
            parser.error('--verify-existing requires a fresh --download directory')
        result = verify_published(args.assets, args.repository, args.tag, args.expected_commit, args.download)
    elif args.complete_existing:
        if not args.download:
            parser.error('--complete-existing requires a fresh --download directory')
        result = complete_existing(args.assets, args.repository, args.tag, args.expected_commit, args.download)
    else:
        result = validate_assets(args.assets, args.tag, args.expected_commit)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    if sys.flags.optimize:
        raise RuntimeError('Optimized Python is forbidden for release verification')
    main()
