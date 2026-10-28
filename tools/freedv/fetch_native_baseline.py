#!/usr/bin/env python3
"""Recover the verified X6200 baseline and matching SDK from this fork's Actions.

No credentials are read. gh uses the platform/Actions HTTPS authentication.
Artifact redirects can contain signed URLs; errors intentionally omit stderr.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
from zipfile import ZipFile

REPOSITORY = 'bho18/OpenX6X00'
BASELINE_RUN = 40417980169
ARTIFACT_ID = 12701486870
ARTIFACT_SHA256 = '0fdfdd2bc9f67cda6137a8adb8e60e829b2055db2506f1184cf8c3b924311b01'
FIRMWARE = 'fd7f0bba70c0c8f8854219b6422600cbd428a6dc'
BUILDROOT = '8b7ca9d6d4e82ade0fc7654c46b9f8688689a4a3'
BUILDROOT_SUBMODULE = 'cc34b5b2abf2588f0ef55e46a4fe85a60c19ba1f'
MEASURED_NE10_SHA256 = 'b82c9a9fa7540c199ca3781abf40f3ebce599d61493a5a8df8b1046b16626808'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def github_json(path):
    result = subprocess.run(['gh', 'api', 'repos/' + REPOSITORY + path], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('GitHub metadata request failed; status ' + str(result.returncode))
    return json.loads(result.stdout)


def safe_path(root, name):
    target = root / name
    if not name or Path(name).is_absolute() or '..' in Path(name).parts or root not in target.resolve().parents:
        raise ValueError('unsafe archive/checksum path: ' + name)
    return target


def verify_checksums(root):
    verified = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        if not line.strip():
            continue
        expected, name = line.split('  ', 1)
        path = safe_path(root, name)
        if not path.is_file() or sha(path) != expected:
            raise ValueError('baseline checksum mismatch: ' + name)
        verified[name] = expected
    return verified


def unpack_sdk(archive, destination):
    """Directories/files first and links last; never write through archive links."""
    destination.mkdir()
    normalized_links = []
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        seen = set()
        for member in members:
            safe_path(destination, member.name)
            if member.name in seen or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError('unexpected SDK archive member: ' + member.name)
            seen.add(member.name)
        for member in members:
            if not member.isdir():
                continue
            target = safe_path(destination, member.name)
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(member.mode & 0o777)
        for member in members:
            if not member.isfile():
                continue
            target = safe_path(destination, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('xb') as out:
                shutil.copyfileobj(source, out)
            target.chmod(member.mode & 0o777)
        pending = [m for m in members if m.issym() or m.islnk()]
        # Tar hardlink targets use archive-root paths. Resolve all hardlinks
        # before symlinks so their targets cannot redirect extraction.
        for member in [m for m in pending if m.islnk()]:
            target = safe_path(destination, member.name)
            source = safe_path(destination, member.linkname)
            if not source.is_file() or source.is_symlink():
                raise ValueError('unsupported SDK hardlink target: ' + member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.hardlink_to(source)
        for member in [m for m in pending if m.issym()]:
            target = safe_path(destination, member.name)
            link = Path(member.linkname)
            link_text = member.linkname
            if link.is_absolute():
                # Staging contains target-root aliases such as /sbin/udevadm
                # -> /bin/udevadm. They must resolve inside the target sysroot,
                # never to programs/libraries on this development machine.
                parts = Path(member.name).parts
                sdk_root = destination / parts[0]
                location_file = sdk_root / 'share/buildroot/sdk-location'
                old_prefix = location_file.read_text().strip() if location_file.is_file() else ''
                if old_prefix.startswith('/') and member.linkname.startswith(old_prefix.rstrip('/') + '/'):
                    # Native SDK aliases may still name the original build's
                    # host prefix. Relocate only that recorded prefix.
                    resolved = (sdk_root / member.linkname[len(old_prefix.rstrip('/'))+1:]).resolve()
                    target_root = sdk_root.resolve()
                elif 'sysroot' in parts:
                    index = parts.index('sysroot')
                    target_root = destination.joinpath(*parts[:index+1]).resolve()
                    resolved = (target_root / member.linkname.lstrip('/')).resolve()
                else:
                    raise ValueError('absolute host SDK symlink refused: ' + member.name)
                if resolved != target_root and target_root not in resolved.parents:
                    raise ValueError('SDK target symlink escapes its recorded root: ' + member.name)
                link_text = os.path.relpath(resolved, target.parent)
                normalized_links.append({'member':member.name,'original':member.linkname,'relative_target':link_text})
            resolved = (target.parent / link_text).resolve()
            if destination not in resolved.parents:
                raise ValueError('SDK symlink escapes extraction root: ' + member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link_text)
    return normalized_links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new directory outside the source checkout')
    parser.add_argument('--cached-zip', type=Path, help='optional existing archive, still required to match pinned artifact SHA256')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run = github_json('/actions/runs/' + str(BASELINE_RUN))
    if (run['conclusion'] != 'success' or run['status'] != 'completed' or
            run['head_branch'] != 'experiment/x6200-baseline-sdk' or run['path'] != '.github/workflows/main.yml'):
        raise ValueError('baseline run identity/status mismatch')
    artifact = github_json('/actions/artifacts/' + str(ARTIFACT_ID))
    if (artifact['expired'] or artifact['name'] != 'x6200-freedv-baseline' or
            artifact['workflow_run']['id'] != BASELINE_RUN or artifact['digest'] != 'sha256:' + ARTIFACT_SHA256):
        raise ValueError('baseline artifact identity mismatch')
    (output / 'baseline-run.json').write_text(json.dumps(run, indent=2) + '\n')
    (output / 'artifact.json').write_text(json.dumps(artifact, indent=2) + '\n')
    archive = output / 'baseline.zip'
    if args.cached_zip:
        if not args.cached_zip.is_file() or sha(args.cached_zip) != ARTIFACT_SHA256:
            raise ValueError('cached artifact ZIP digest mismatch')
        shutil.copyfile(args.cached_zip, archive)
    else:
        with archive.open('xb') as stream:
            result = subprocess.run(['gh', 'api', 'repos/' + REPOSITORY + '/actions/artifacts/' + str(ARTIFACT_ID) + '/zip'],
                                    stdout=stream, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError('Artifact download failed. Check access to the GitHub artifact storage destination; signed redirect details omitted.')
    if sha(archive) != ARTIFACT_SHA256:
        raise ValueError('GitHub artifact ZIP digest mismatch')
    baseline = output / 'baseline'; baseline.mkdir()
    with ZipFile(archive) as zipfile:
        seen = set()
        for member in zipfile.infolist():
            path = safe_path(baseline, member.filename)
            if member.filename in seen or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('duplicate/link artifact member')
            seen.add(member.filename)
            if member.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.open(member) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target)
    sums = verify_checksums(baseline)
    if ((baseline / 'buildroot-commit.txt').read_text().strip() != BUILDROOT or
            (baseline / 'firmware-commit.txt').read_text().strip() != FIRMWARE):
        raise ValueError('baseline source revisions differ')
    pins = json.loads((baseline / 'sources.json').read_text())
    if pins['x6200_buildroot']['commit'] != BUILDROOT or pins['x6200_buildroot']['buildroot_submodule'] != BUILDROOT_SUBMODULE:
        raise ValueError('baseline Buildroot/submodule manifest differs')
    sdcard = baseline / 'OpenX6X00-x6200-local-sdcard.img'
    sdk_archives = list(baseline.glob('*sdk*.tar.gz'))
    if len(sdk_archives) != 1 or sdcard.name not in sums or sdk_archives[0].name not in sums:
        raise ValueError('checksummed image and one SDK archive required')
    sdk_archive = sdk_archives[0]
    normalized_links = unpack_sdk(sdk_archive, output / 'sdk')
    relocators = list((output / 'sdk').rglob('relocate-sdk.sh'))
    if len(relocators) != 1:
        raise ValueError('one SDK relocation entry point required')
    subprocess.run([str(relocators[0])], check=True)
    sdk = relocators[0].parent
    compilers = sorted((sdk / 'bin').glob('arm*-gcc'), key=lambda p: ('buildroot' not in p.name, p.name))
    if not compilers:
        raise ValueError('SDK ARM compiler missing')
    compiler = compilers[0]
    version = subprocess.check_output([compiler, '--version'], text=True).splitlines()[0]
    if 'Buildroot -gcc34b5b2' not in version or '12.3.0' not in version:
        raise ValueError('matching measured GCC identity required: ' + version)
    sysroot = Path(subprocess.check_output([compiler, '-print-sysroot'], text=True).strip()).resolve()
    if sdk not in sysroot.parents:
        raise ValueError('SDK compiler sysroot escaped relocated SDK')
    ne10=sysroot/'usr/lib/libNE10.so'
    if not ne10.is_file() or sha(ne10.resolve())!=MEASURED_NE10_SHA256:
        raise ValueError('SDK NE10 differs from the measured package; investigate explicitly')
    record = {'schema_version': 1, 'repository': REPOSITORY, 'run_id': BASELINE_RUN,
              'artifact_id': ARTIFACT_ID, 'artifact_sha256': ARTIFACT_SHA256,
              'sdk_archive_sha256': sha(sdk_archive), 'symlink_normalizations': normalized_links, 'baseline_image_sha256': sha(sdcard),
              'buildroot_commit': BUILDROOT, 'buildroot_submodule': BUILDROOT_SUBMODULE,
              'baseline_firmware_commit': FIRMWARE, 'compiler_version': version,
              'compiler_sha256_after_relocation': sha(compiler.resolve()),
              'sdk_ne10_library_sha256': sha(ne10.resolve()),
              'sdk': str(sdk), 'sysroot': str(sysroot), 'baseline_image': str(sdcard)}
    (output / 'recovery.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
