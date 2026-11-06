#!/usr/bin/env python3
"""Use Ubuntu's canonical HTTPS archive for Azure Ubuntu mirror entries only.

This changes URI tokens in APT source files. Suites, components, options,
Signed-By and other repositories remain byte-for-byte unchanged. It does not
install packages, edit trust settings, or disable signature verification.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MIRROR = 'https://archive.ubuntu.com/ubuntu'
AZURE_URI = re.compile(r'https?://azure\.archive\.ubuntu\.com/ubuntu(/?)', re.IGNORECASE)
LEGACY = re.compile(r'^(\s*deb(?:-src)?\s+(?:\[[^\]\r\n]*\]\s+)?)(\S+)(.*)$')
FIELD = re.compile(r'^([A-Za-z][A-Za-z0-9-]*):')


def rewrite_source(text, deb822=False):
    count = 0

    def uri(value):
        nonlocal count
        match = AZURE_URI.fullmatch(value)
        if match:
            count += 1
            return MIRROR + match[1]
        return value

    output, inside_uris = [], False
    for line in text.splitlines(keepends=True):
        if not deb822:
            match = LEGACY.match(line.rstrip('\r\n'))
            if match:
                line = match[1] + uri(match[2]) + match[3] + line[len(line.rstrip('\r\n')):]
        else:
            match = FIELD.match(line)
            if match:
                inside_uris = match[1].lower() == 'uris'
            elif not line.strip():
                inside_uris = False
            elif not line.startswith((' ', '\t', '#')):
                inside_uris = False
            if inside_uris and not line.lstrip().startswith('#'):
                # A comment is retained literally rather than scanned as URIs.
                active, marker, comment = line.partition('#')
                line = re.sub(r'\S+', lambda token: uri(token[0]), active) + marker + comment
        output.append(line)
    return ''.join(output), count


def normalize_sources(root=Path('/etc/apt'), check=False):
    root = Path(root)
    paths = [root/'sources.list'] if (root/'sources.list').exists() else []
    directory = root/'sources.list.d'
    if directory.is_symlink():
        raise ValueError('APT source directory must not be a symbolic link')
    if directory.is_dir():
        paths += sorted([*directory.glob('*.list'), *directory.glob('*.sources')])
    plans = []
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
            raise ValueError('APT source entry is nonregular, linked, or unexpectedly large')
        original = path.read_bytes()
        rewritten, count = rewrite_source(original.decode('utf-8'), path.suffix == '.sources')
        if count:
            plans.append((path, rewritten.encode('utf-8'), path.stat(), count))
    if not check:
        for path, payload, mode, _ in plans:
            descriptor, temporary = tempfile.mkstemp(prefix='.usb-ci-source-', dir=path.parent)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(payload)
                    os.fchmod(stream.fileno(), stat.S_IMODE(mode.st_mode))
                    if os.geteuid() == 0:
                        os.fchown(stream.fileno(), mode.st_uid, mode.st_gid)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return {'mirror': MIRROR, 'source_files_checked': len(paths), 'source_files_changed': len(plans),
            'uri_tokens_changed': sum(plan[3] for plan in plans), 'check_only': check}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources-root', type=Path, default=Path('/etc/apt'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.sources_root == Path('/etc/apt'):
        release = Path('/etc/os-release').read_text().splitlines()
        if not any(line in ('ID=ubuntu', 'ID="ubuntu"') for line in release):
            parser.error('Default source normalization is restricted to Ubuntu CI runners')
    print(json.dumps(normalize_sources(args.sources_root, args.check), sort_keys=True))


if __name__ == '__main__':
    main()
