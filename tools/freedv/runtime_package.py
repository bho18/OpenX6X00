#!/usr/bin/env python3
"""Validate reviewed application-only runtime inputs without weakening platform gates."""
import json
from pathlib import Path
import re
import struct
import subprocess

from native_image import require_arm_elf, require_regular, sha

ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = 'x6200.runtime.inputs.v1'
PACKAGE_SCHEMA = 'x6200.runtime.package.v1'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def application_version(root=ROOT):
    match = re.search(r'^#define VERSION\s+"[^"\n]* v([0-9]+(?:\.[0-9]+)+)"',
                      (Path(root) / 'src/main.h').read_text(), re.M)
    if not match:
        raise ValueError('application VERSION must contain an explicit numeric version')
    return match[1]


def compatibility(root=ROOT):
    root = Path(root)
    path = root / 'tools/freedv/runtime-compatibility.json'
    record = json.loads(path.read_text())
    if record.get('schema') != 'x6200.runtime.compatibility.v1':
        raise ValueError('reviewed runtime compatibility record required')
    baseline = record['baseline_source']
    changed = set(git(root, 'diff', '--name-only', baseline, '--', 'buildroot').splitlines())
    changed.update(git(root, 'ls-files', '--others', '--exclude-standard', '--', 'buildroot').splitlines())
    allowed = record['reviewed_buildroot_changes']
    for name in changed:
        if name not in allowed:
            raise ValueError('unreviewed image/kernel/package change requires platform review: ' + name)
        candidate = root / name
        expected = allowed[name]
        if expected is None:
            if candidate.exists():
                raise ValueError('reviewed removed platform file exists: ' + name)
        elif not candidate.is_file() or candidate.is_symlink() or sha(candidate) != expected:
            raise ValueError('reviewed platform input changed: ' + name)
    level = next(line.split()[1] for line in (root / 'rootfs/usr/share/x6100/image-required').read_text().splitlines()
                 if line.startswith('x6200 '))
    old = git(root, 'show', baseline + ':rootfs/usr/share/x6100/image-required')
    old_level = next(line.split()[1] for line in old.splitlines() if line.startswith('x6200 '))
    if level != old_level or level != record['x6200_image_level']:
        raise ValueError('X6200 image level changed; full baseline review required')
    if sha(root / 'webserver/pyproject.toml') != record['web_project_sha256']:
        raise ValueError('webserver runtime dependencies changed; explicit platform review required')
    return {**record, 'record_sha256': sha(path)}


def checked_path(entry):
    path = Path(entry['path']).resolve()
    require_regular(path)
    if sha(path) != entry.get('sha256'):
        raise ValueError('runtime input bytes changed: ' + str(path))
    return path


def require_static_arm_program(path):
    """CGO-free Go uses static ARM ELF, without the dynamic hard-float tag."""
    path = Path(path)
    require_regular(path)
    with path.open('rb') as stream:
        header = stream.read(52)
        if (len(header) != 52 or header[:6] != b'\x7fELF\x01\x01' or
                struct.unpack_from('<H', header, 16)[0] != 2 or
                struct.unpack_from('<H', header, 18)[0] != 40):
            raise ValueError('static little-endian ARM32 executable required')
        offset = struct.unpack_from('<I', header, 28)[0]
        size, count = struct.unpack_from('<HH', header, 42)
        if size < 32 or not count or offset + size * count > path.stat().st_size:
            raise ValueError('invalid static ARM program header table')
        types = []
        for index in range(count):
            stream.seek(offset + index * size)
            types.append(struct.unpack('<I', stream.read(4))[0])
        if 1 not in types or 2 in types or 3 in types:
            raise ValueError('static ARM program unexpectedly uses a dynamic loader')


def validate_inputs(path, root=ROOT, sdk=None):
    path, root = Path(path), Path(root)
    record = json.loads(path.read_text())
    if record.get('schema') != INPUT_SCHEMA or record.get('status') != 'PASS':
        raise ValueError('passing runtime input record required')
    # Dependency evidence retains its actual build revision. Unrelated GUI
    # commits can reuse identical checked tool/patch/SDK inputs without claiming
    # that these dependencies were rebuilt for the newer application commit.
    original = record.get('source_commit', '')
    if not isinstance(original, str) or re.fullmatch('[0-9a-f]{40}', original) is None:
        raise ValueError('runtime dependency build source revision missing')
    subprocess.run(['git', 'merge-base', '--is-ancestor', original, 'HEAD'], cwd=root,
                   check=True, capture_output=True)
    review = compatibility(root)
    if record.get('compatibility_sha256') != review['record_sha256']:
        raise ValueError('runtime inputs do not match reviewed platform inputs')
    compiler = checked_path(record['compiler'])
    if sdk is not None and compiler != (Path(sdk).resolve() / 'bin/arm-buildroot-linux-gnueabihf-gcc').resolve():
        raise ValueError('runtime inputs use a different SDK compiler')
    ft8 = record['ft8']
    if ft8.get('commit') != review['ft8_commit'] or ft8.get('archive_sha256') != review['ft8_archive_sha256']:
        raise ValueError('runtime FT8 source is not the reviewed pin')
    require_arm_elf(checked_path(ft8['library']))
    header_root = Path(ft8['include_dir'])
    actual = {p.relative_to(header_root).as_posix(): sha(p) for p in sorted(header_root.rglob('*')) if p.is_file()}
    if not actual or actual != ft8['headers_sha256']:
        raise ValueError('runtime FT8 headers changed or are incomplete')
    smoke = ft8.get('smoke', {})
    if smoke.get('status') != 'PASS' or smoke.get('source_sha256') != sha(root / 'tools/freedv/ft8_runtime_smoke.c'):
        raise ValueError('current executed FT8 compound-callsign check required')
    checked_path(smoke['binary'])
    checked_path(smoke['log'])
    require_arm_elf(checked_path(record['xara']['binary']))
    for name in ('LICENSE', 'THIRD-PARTY', 'rust-std-COPYRIGHT.html.gz'):
        checked_path(record['xara']['notices'][name])
    expected = record['xara']['build_sources_sha256']
    actual = {p.relative_to(root).as_posix(): sha(p) for p in sorted((root / 'tools/xara').rglob('*')) if p.is_file()}
    if actual != expected:
        raise ValueError('XARA build inputs changed')
    require_static_arm_program(checked_path(record['pat']['binary']))
    for name, entry in record['pat']['notices'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('invalid Pat notice path')
        checked_path(entry)
    for name in ('LICENSE', 'GO-LICENSE', 'MODULES'):
        if name not in record['pat']['notices']:
            raise ValueError('Pat/toolchain notices missing')
    actual = {p.relative_to(root).as_posix(): sha(p) for p in sorted((root / 'tools/pat').rglob('*')) if p.is_file()}
    if actual != record['pat']['build_sources_sha256']:
        raise ValueError('Pat build inputs changed')
    if record['pat'].get('smoke', {}).get('status') != 'PASS':
        raise ValueError('executed static Pat version check required')
    checked_path(record['pat']['smoke']['log'])
    return record


def validate_package(package, root=ROOT, sdk=None):
    package, root = Path(package).resolve(), Path(root)
    record = json.loads((package / 'runtime-manifest.json').read_text())
    if record.get('schema') != PACKAGE_SCHEMA or record.get('status') != 'PASS':
        raise ValueError('passing runtime package required')
    if record.get('source_commit') != git(root, 'rev-parse', 'HEAD'):
        raise ValueError('runtime package does not match source revision')
    review = compatibility(root)
    if record.get('compatibility_sha256') != review['record_sha256']:
        raise ValueError('runtime package platform review differs')
    input_path = package / 'runtime-inputs.json'
    if sha(input_path) != record.get('runtime_inputs_sha256'):
        raise ValueError('runtime package input receipt changed')
    inputs = validate_inputs(input_path, root=root, sdk=sdk)
    if sdk is not None:
        compiler = (Path(sdk).resolve() / 'bin/arm-buildroot-linux-gnueabihf-gcc').resolve()
        if sha(compiler) != record.get('compiler_sha256'):
            raise ValueError('runtime package uses a different SDK compiler')
    overlay = package / 'overlay'
    actual = {}
    for path in sorted(overlay.rglob('*')):
        if path.is_symlink():
            raise ValueError('runtime package may not contain symlinks')
        if path.is_file():
            actual['/' + path.relative_to(overlay).as_posix()] = {
                'sha256': sha(path), 'mode': path.stat().st_mode & 0o777}
    if actual != record.get('files'):
        raise ValueError('runtime package bytes/modes differ from manifest')
    required = {'/usr/lib/libft8.so', '/usr/bin/pat', '/usr/bin/xara', '/usr/bin/x6x00-mercury', '/usr/bin/ardopcf',
                '/usr/sbin/btcat', '/usr/sbin/x6100-bootanim', '/usr/share/x6100/params.default.db'}
    if not required <= set(actual):
        raise ValueError('runtime package lacks a required upstream component')
    if (actual['/usr/lib/libft8.so']['sha256'] != inputs['ft8']['library']['sha256'] or
            actual['/usr/bin/xara']['sha256'] != inputs['xara']['binary']['sha256'] or
            actual['/usr/bin/pat']['sha256'] != inputs['pat']['binary']['sha256']):
        raise ValueError('packaged dependency differs from executed SDK inputs')
    notices = {'/usr/share/x6100/licenses/ft8/LICENSE',
               '/usr/share/x6100/licenses/mercury/LICENSE',
               '/usr/share/x6100/licenses/mercury/LICENSE-freedv',
               '/usr/share/x6100/licenses/xara/LICENSE',
               '/usr/share/x6100/licenses/xara/THIRD-PARTY',
               '/usr/share/x6100/licenses/xara/rust-std-COPYRIGHT.html.gz'}
    notices.update('/usr/share/x6100/licenses/pat/' + name for name in ('LICENSE', 'GO-LICENSE', 'MODULES'))
    if not notices <= set(actual):
        raise ValueError('runtime package lacks required component notices')
    for name in record.get('elf_programs', []):
        if name not in required or name not in actual:
            raise ValueError('unexpected runtime ELF program')
        require_arm_elf(overlay / name.lstrip('/'))
    expected_programs = required - {'/usr/lib/libft8.so', '/usr/share/x6100/params.default.db', '/usr/bin/pat'}
    if set(record.get('elf_programs', [])) != expected_programs:
        raise ValueError('every runtime helper must receive an ELF loader check')
    if record.get('elf_libraries') != ['/usr/lib/libft8.so']:
        raise ValueError('only the reviewed SDK-built FT8 library may replace a baseline library')
    if record.get('static_programs') != ['/usr/bin/pat']:
        raise ValueError('the pinned CGO-free Pat is the only reviewed static helper')
    require_static_arm_program(overlay / 'usr/bin/pat')
    if record.get('ft8_commit') != review['ft8_commit']:
        raise ValueError('runtime package FT8 pin differs')
    return record


def overlay_removals(root=ROOT):
    """Only delete application-owned files removed since the reviewed baseline."""
    root = Path(root)
    baseline = compatibility(root)['baseline_source']
    names = git(root, 'diff', '--name-only', '--diff-filter=D', baseline, '--', 'rootfs', 'images').splitlines()
    result = []
    for name in names:
        if name.startswith('rootfs/'):
            destination = '/' + name[len('rootfs/'):]
        else:
            destination = '/usr/share/x6100/' + name
        if destination.startswith(('/mnt/', '/lib/', '/boot/')) or destination.endswith('/update.pub'):
            raise ValueError('removed source requests a protected runtime deletion')
        result.append(destination)
    return sorted(result)
