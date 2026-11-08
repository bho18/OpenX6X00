#!/usr/bin/env python3
"""Build SDK-matched application dependencies, then collect the verified runtime.

The inputs stage runs before ARM GUI builds; package runs after the source-bound
ARM build. Neither stage updates a kernel, SDK, radio, Git ref or remote service.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import stat

from native_image import require_arm_elf, sha
from runtime_package import (ROOT, INPUT_SCHEMA, PACKAGE_SCHEMA, checked_path,
                             compatibility, git, require_static_arm_program, validate_inputs, validate_package)


def clean_environment():
    env = os.environ.copy()
    for name in ('CPATH', 'LIBRARY_PATH', 'PKG_CONFIG_PATH', 'CMAKE_PREFIX_PATH'):
        env.pop(name, None)
    return env


def artifact(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path)}


def require_clean():
    if git(ROOT, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('commit runtime build sources before creating dependency evidence')


def run(command, log, env=None, cwd=None):
    command = list(map(str, command))
    with Path(log).open('x') as output:
        subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                       check=True, timeout=7200)


def retire_build_caches(work):
    """Retire only this attempt's reconstructed toolchains/object caches."""
    cache = work / 'cache'
    protected = {p: sha(p) for directory in (cache / 'xara/dl', cache / 'pat/dl', cache / 'gopath/pkg/mod/cache')
                 for p in directory.rglob('*') if p.is_file() and not p.is_symlink()}
    inputs = json.loads((work / 'runtime-inputs.json').read_text())
    for component in ('pat', 'xara'):
        for entry in [inputs[component]['binary'], *inputs[component]['notices'].values()]:
            path = Path(entry['path'])
            protected[path] = entry['sha256']
    if not any(p.name.startswith('go1.') and p.name.endswith('.tar.gz') for p in protected):
        raise ValueError('retain the verified Go archive before cache retirement')
    if len([p for p in protected if p.name.startswith('rust') and p.name.endswith('.tar.xz')]) < 3:
        raise ValueError('retain the verified Rust archives before cache retirement')
    candidates = [p for pattern in ('rust-*', 'go-*', 'gocache') for p in cache.glob(pattern) if p.is_dir()]
    modules = cache / 'gopath/pkg/mod'
    if modules.is_dir():
        candidates += [p for p in modules.iterdir() if p.name != 'cache' and p.is_dir()]
    retired = []
    for candidate in candidates:
        if candidate.is_symlink() or cache.resolve() not in candidate.resolve().parents:
            raise ValueError('unsafe dependency-cache retirement candidate')
        # Official archives live under xara/dl and pat/dl; module ZIPs under
        # gopath/pkg/mod/cache. No executable, notice or retained input belongs
        # to a retired directory.
        if any(candidate == path or candidate in path.parents for path in protected):
            raise ValueError('cache candidate includes a protected archive or runtime artifact')
        files = {p.relative_to(candidate).as_posix(): sha(p) for p in candidate.rglob('*')
                 if p.is_file() and not p.is_symlink()}
        retired.append({'path': str(candidate), 'files_sha256': files,
                        'allocated_bytes': sum(p.stat().st_blocks * 512 for p in candidate.rglob('*')
                                               if p.is_file() and not p.is_symlink())})
        # Go module extraction marks files read-only. These paths were created
        # exclusively below this fresh attempt's cache, never shared sources.
        for path in candidate.rglob('*'):
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)
        shutil.rmtree(candidate)
    if any(not path.is_file() or sha(path) != digest for path, digest in protected.items()):
        raise ValueError('protected dependency bytes changed during retirement')
    receipt = {'schema': 'x6200.runtime.cache-retirement.v1', 'status': 'PASS',
               'retired': retired, 'protected_files_sha256': {str(p): digest for p, digest in protected.items()}}
    (work / 'cache-retirement.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def build_inputs(args):
    require_clean()
    source_commit = git(ROOT, 'rev-parse', 'HEAD')
    source_paths = [ROOT / name for name in ('tools/freedv/build_runtime_package.py', 'tools/freedv/runtime_package.py',
                                            'tools/freedv/runtime-compatibility.json', 'tools/freedv/ft8_runtime_smoke.c')]
    source_paths += [p for name in ('tools/pat', 'tools/xara') for p in (ROOT / name).rglob('*') if p.is_file()]
    before_sources = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(source_paths)}
    review = compatibility()
    sdk, work = args.sdk.resolve(), args.work.resolve()
    if ROOT == work or ROOT in work.parents:
        raise ValueError('runtime work directory must be outside the checkout')
    compiler = sdk / 'bin/arm-buildroot-linux-gnueabihf-gcc'
    target = subprocess.check_output([str(compiler), '-dumpmachine'], text=True).strip()
    sysroot = Path(subprocess.check_output([str(compiler), '-print-sysroot'], text=True).strip()).resolve()
    version = subprocess.check_output([str(compiler), '--version'], text=True).splitlines()[0]
    if target != 'arm-buildroot-linux-gnueabihf' or sdk not in sysroot.parents or '12.3.0' not in version:
        raise ValueError('matching relocated Buildroot GCC 12.3 SDK required')
    if sha(args.ft8_source_archive) != review['ft8_archive_sha256']:
        raise ValueError('FT8 source archive differs from reviewed pin')
    work.mkdir(parents=True, exist_ok=False)
    sources = work / 'sources'
    sources.mkdir()
    prefix = 'ft8_lib-' + review['ft8_commit']
    with tarfile.open(args.ft8_source_archive, 'r:gz') as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != prefix:
                raise ValueError('invalid FT8 source archive path')
            if not (member.isfile() or member.isdir()):
                raise ValueError('FT8 source archive contains a non-regular member')
        archive.extractall(sources, filter='data')
    source = sources / prefix
    install = work / 'ft8'
    include = install / 'include/ft8lib'
    include.mkdir(parents=True)
    library = install / 'lib/libft8.so'
    library.parent.mkdir()
    headers = sorted((source / 'ft8').glob('*.h'))
    if not headers:
        raise ValueError('FT8 source headers missing')
    for header in headers:
        shutil.copy2(header, include / header.name)
    env = clean_environment()
    run([compiler, '-O3', '-fPIC', '-std=c11', '-D_GNU_SOURCE', '-shared', '-I' + str(source),
         '-Wl,-soname,libft8.so', '-o', library, *sorted((source / 'ft8').glob('*.c')), '-lm'],
        work / 'ft8-build.log', env=env)
    require_arm_elf(library)
    smoke = work / 'ft8-runtime-smoke'
    run([compiler, '-O2', '-I' + str(include), ROOT / 'tools/freedv/ft8_runtime_smoke.c',
         library, '-Wl,-rpath,' + str(library.parent), '-o', smoke], work / 'ft8-smoke-build.log', env=env)
    run(['qemu-arm', '-L', sysroot, '-E', 'LD_LIBRARY_PATH=' + str(library.parent), smoke],
        work / 'ft8-smoke.log', env=env)
    # The upstream script downloads exact checked source/toolchain archives and
    # builds with this SDK's linker. Cache location is explicit and task-owned.
    env.update(CC=str(compiler), OPENX6X00_CACHE=str(work / 'cache'))
    xara_log = work / 'xara-build.log'
    with xara_log.open('x') as output:
        result = subprocess.run(['bash', str(ROOT / 'tools/xara/build.sh')], cwd=ROOT,
                                env=env, stdout=subprocess.PIPE, stderr=output,
                                text=True, check=True, timeout=7200)
    xara = Path(result.stdout.strip()).resolve()
    require_arm_elf(xara)
    notices = {name: artifact(xara.parent / 'doc' / name)
               for name in ('LICENSE', 'THIRD-PARTY', 'rust-std-COPYRIGHT.html.gz')}
    pat_log = work / 'pat-build.log'
    with pat_log.open('x') as output:
        result = subprocess.run(['bash', str(ROOT / 'tools/pat/build.sh')], cwd=ROOT,
                                env=env, stdout=subprocess.PIPE, stderr=output,
                                text=True, check=True, timeout=7200)
    pat = Path(result.stdout.strip()).resolve()
    require_static_arm_program(pat)
    run(['qemu-arm', '-L', sysroot, pat, 'version'], work / 'pat-smoke.log', env=env)
    pat_notices = {p.relative_to(pat.parent / 'doc').as_posix(): artifact(p)
                   for p in sorted((pat.parent / 'doc').rglob('*')) if p.is_file()}
    record = {'schema': INPUT_SCHEMA, 'status': 'PASS',
              'source_commit': source_commit,
              'dependency_build_sources_sha256': before_sources,
              'compatibility_sha256': review['record_sha256'],
              'compiler': {**artifact(compiler), 'target': target, 'version': version},
              'ft8': {'commit': review['ft8_commit'], 'archive_sha256': sha(args.ft8_source_archive),
                      'compile_flags': ['-O3', '-fPIC', '-std=c11', '-D_GNU_SOURCE', '-shared', '-Wl,-soname,libft8.so'],
                      'library': artifact(library), 'include_dir': str(include),
                      'headers_sha256': {p.name: sha(p) for p in sorted(include.iterdir())},
                      'source_files_sha256': {p.relative_to(source).as_posix(): sha(p)
                                              for p in sorted(source.rglob('*')) if p.is_file()},
                      'smoke': {'status': 'PASS', 'scope': 'QEMU correctness only; no RF or radio timing',
                                'binary': artifact(smoke), 'log': artifact(work / 'ft8-smoke.log'),
                                'source_sha256': sha(ROOT / 'tools/freedv/ft8_runtime_smoke.c')}},
              'xara': {'binary': artifact(xara), 'notices': notices,
                       'build_sources_sha256': {p.relative_to(ROOT).as_posix(): sha(p)
                                                for p in sorted((ROOT / 'tools/xara').rglob('*')) if p.is_file()}},
              'pat': {'binary': artifact(pat), 'notices': pat_notices,
                      'build_sources_sha256': {p.relative_to(ROOT).as_posix(): sha(p)
                                               for p in sorted((ROOT / 'tools/pat').rglob('*')) if p.is_file()},
                      'smoke': {'status': 'PASS', 'log': artifact(work / 'pat-smoke.log')}},
              'archives': {p.relative_to(work).as_posix(): artifact(p) for p in sorted((work / 'cache').rglob('*'))
                           if p.is_file() and p.name.endswith(('.tar.xz', '.tar.gz'))},
              'toolchain_versions': {
                  'go': subprocess.check_output([str(work / 'cache/go-1.27.3/bin/go'), 'version'], text=True).strip(),
                  'rustc': subprocess.check_output([str(work / 'cache/rust-1.99.1/bin/rustc'), '--version'], text=True).strip()},
              'toolchain_binaries_sha256': {name: sha(work / 'cache' / name) for name in
                                           ('go-1.27.3/bin/go', 'rust-1.99.1/bin/rustc', 'rust-1.99.1/bin/cargo')},
              'logs': {'ft8': artifact(work / 'ft8-build.log'), 'xara': artifact(xara_log), 'pat': artifact(pat_log)}}
    if before_sources != {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(source_paths)}:
        raise ValueError('dependency build sources changed while the build ran')
    (work / 'runtime-inputs.json').write_text(json.dumps(record, indent=2) + '\n')
    validate_inputs(work / 'runtime-inputs.json', sdk=sdk)
    if args.retire_build_caches:
        retire_build_caches(work)
        validate_inputs(work / 'runtime-inputs.json', sdk=sdk)
    print(work / 'runtime-inputs.json')


def collect_package(args):
    require_clean()
    inputs = validate_inputs(args.inputs, sdk=args.sdk)
    review = compatibility()
    from build_native_development import validate_build_record
    build_record = json.loads((args.gui_build.parent / 'build-record.json').read_text())
    validate_build_record(build_record)
    if build_record['source_commit'] != git(ROOT, 'rev-parse', 'HEAD') or build_record['variant'] != 'x6200-diagnostic':
        raise ValueError('runtime package requires the current source-bound X6200 build')
    if build_record.get('runtime_inputs_sha256') != sha(args.inputs):
        raise ValueError('GUI build did not use these exact runtime inputs')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.inputs, output / 'runtime-inputs.json')
    overlay = output / 'overlay'
    overlay.mkdir()
    origins = {}

    def copy(source, destination, mode=None):
        source = Path(source)
        target = overlay / destination.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if mode is not None:
            target.chmod(mode)
        origins[destination] = artifact(source)

    programs = {
        '/usr/bin/x6x00-mercury': args.gui_build / 'third-party/mercury/x6x00-mercury',
        '/usr/bin/ardopcf': args.gui_build / 'third-party/ardopcf/ardopcf',
        '/usr/sbin/btcat': args.gui_build / 'btcat/btcat',
        '/usr/sbin/x6100-bootanim': args.gui_build / 'bootanim/x6100-bootanim',
    }
    for destination, binary in programs.items():
        require_arm_elf(binary)
        retained = build_record['binaries'].get(binary.name)
        if not retained or sha(binary) != retained['sha256']:
            raise ValueError('runtime helper not retained by matching GUI build: ' + binary.name)
        copy(binary, destination, 0o755)
    copy(checked_path(inputs['ft8']['library']), '/usr/lib/libft8.so', 0o755)
    copy(checked_path(inputs['xara']['binary']), '/usr/bin/xara', 0o755)
    copy(checked_path(inputs['pat']['binary']), '/usr/bin/pat', 0o755)
    params = args.gui_build / 'params.db'
    retained = build_record['binaries'].get('params.db')
    if not retained or sha(params) != retained['sha256']:
        raise ValueError('default settings database not retained by matching GUI build')
    copy(params, '/usr/share/x6100/params.default.db', 0o644)
    for name, entry in inputs['xara']['notices'].items():
        copy(checked_path(entry), '/usr/share/x6100/licenses/xara/' + name, 0o644)
    for name, entry in inputs['pat']['notices'].items():
        copy(checked_path(entry), '/usr/share/x6100/licenses/pat/' + name, 0o644)
    source = Path(inputs['ft8']['include_dir']).parents[2] / 'sources' / ('ft8_lib-' + review['ft8_commit'])
    copy(source / 'LICENSE', '/usr/share/x6100/licenses/ft8/LICENSE', 0o644)
    for name in ('LICENSE', 'LICENSE-freedv', 'UPSTREAM.md', 'common/iniparser/LICENSE',
                 'datalink_broadcast/raptorq/LICENSE', 'datalink_broadcast/raptorq/deps/obl/LICENSE'):
        copy(ROOT / 'third-party/mercury' / name, '/usr/share/x6100/licenses/mercury/' + name, 0o644)
    for path in sorted((ROOT / 'src/fonts').glob('LICENSE*')):
        copy(path, '/usr/share/x6100/licenses/fonts/' + path.name, 0o644)
    record = {'schema': PACKAGE_SCHEMA, 'status': 'PASS',
              'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
              'compatibility_sha256': review['record_sha256'],
              'compiler_sha256': inputs['compiler']['sha256'], 'ft8_commit': review['ft8_commit'],
              'runtime_inputs_sha256': sha(args.inputs), 'gui_build_record_sha256': sha(args.gui_build.parent / 'build-record.json'),
              'elf_programs': sorted([*programs, '/usr/bin/xara']),
              'static_programs': ['/usr/bin/pat'], 'dependency_source_commit': inputs['source_commit'],
              'elf_libraries': ['/usr/lib/libft8.so'], 'origins': origins,
              'files': {'/' + p.relative_to(overlay).as_posix(): {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777}
                        for p in sorted(overlay.rglob('*')) if p.is_file()}}
    (output / 'runtime-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    validate_package(output, sdk=args.sdk)
    print(output / 'runtime-manifest.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='stage', required=True)
    inputs = subparsers.add_parser('inputs', help='Build the reviewed FT8 and XARA dependencies before the GUI')
    inputs.add_argument('--sdk', type=Path, required=True)
    inputs.add_argument('--ft8-source-archive', type=Path, required=True)
    inputs.add_argument('--work', type=Path, required=True)
    inputs.add_argument('--retire-build-caches', action='store_true',
                        help='Retire reconstructable extracted Go/Rust/object caches after retaining checked inputs')
    package = subparsers.add_parser('package', help='Collect current source-bound upstream runtime helpers')
    package.add_argument('--sdk', type=Path, required=True)
    package.add_argument('--inputs', type=Path, required=True)
    package.add_argument('--gui-build', type=Path, required=True)
    package.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    (build_inputs if args.stage == 'inputs' else collect_package)(args)


if __name__ == '__main__':
    main()
