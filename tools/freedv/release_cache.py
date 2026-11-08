#!/usr/bin/env python3
"""Retire only reconstructable CMake objects after linking, preserving evidence."""
import hashlib
from pathlib import Path
import re


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def retire_intermediates(work, binaries):
    work = Path(work).resolve()
    build = work/'build'
    if not (build/'CMakeCache.txt').is_file() or not (build/'build.ninja').is_file():
        raise ValueError('Retirement requires a completed configured Ninja build')
    protected = {Path(entry['path']).resolve(): entry['sha256'] for entry in binaries.values()}
    for path, expected in protected.items():
        if sha(path) != expected:
            raise ValueError('Protected retained binary changed before retirement')
    eligible = []
    generated_archives = {name for name in re.findall(r'^build ([^\n:]+\.a):',
                          (build/'build.ninja').read_text(), re.MULTILINE) if '$' not in name}
    for path in build.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(build)
        generated_object = 'CMakeFiles' in relative.parts and (path.name.endswith('.o') or path.name.endswith('.o.d'))
        generated_archive = relative.as_posix() in generated_archives
        if not (generated_object or generated_archive) or path.resolve() in protected:
            continue
        eligible.append(path)
    removed = []
    for path in sorted(eligible):
        info = path.stat()
        entry = {'path': str(path), 'sha256': sha(path), 'bytes': info.st_size,
                 'allocated_bytes': info.st_blocks*512, 'reconstructable_by': 'cmake --build from frozen source and pinned inputs'}
        path.unlink()
        removed.append(entry)
    for path, expected in protected.items():
        if sha(path) != expected:
            raise ValueError('Protected retained binary changed during retirement')
    return {'schema': 'x6200.release-cache-retirement.v1', 'status': 'PASS',
            'scope': 'Generated CMake objects/static archives only; source, linked programs, logs and sealed evidence retained',
            'build_root': str(build), 'removed_generated_intermediates': removed,
            'protected_retained_binaries': {str(path): expected for path, expected in protected.items()},
            'unlinked_allocated_bytes': sum(entry['allocated_bytes'] for entry in removed)}
