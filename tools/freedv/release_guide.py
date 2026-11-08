#!/usr/bin/env python3
"""One canonical build-specific operator guide for every release container."""
import hashlib
from pathlib import Path
import re
import zipfile

REPOSITORY = 'bho18/OpenX6X00'


def render_guide(template, build_id):
    """Render release identifiers without changing fallback-release references."""
    if not isinstance(build_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', build_id):
        raise ValueError('A simple exact build identity is required for the guide')
    text = template.decode('utf-8')
    current = re.search(r'Use the `(freedv-operational-beta-[0-9]{8}\.[0-9]+)`', text)
    if not current:
        raise ValueError('The operator guide current-release template is missing')
    text = text.replace(current.group(1), build_id)
    text = text.replace('(usb-test-strategy.md)',
                        '(https://github.com/'+REPOSITORY+'/blob/'+build_id+'/docs/freedv/usb-test-strategy.md)')
    return text.encode('utf-8')


def validate_guide_copies(directory):
    """Reject independently valid ZIPs that contain different instructions."""
    directory = Path(directory)
    expected = (directory/'START-HERE.md').read_bytes()
    for filename in ('x6200-usb-rf-windows.zip', 'usb-rf-test-kit.zip'):
        with zipfile.ZipFile(directory/filename) as archive:
            if archive.namelist().count('START-HERE.md') != 1 or archive.read('START-HERE.md') != expected:
                raise ValueError('Distributed operator guide differs: '+filename)
    return hashlib.sha256(expected).hexdigest()
