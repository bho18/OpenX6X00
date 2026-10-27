#!/usr/bin/env python3
"""Collect a selected read-only radio inventory locally. Never records audio or changes radio settings."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path, help='new result directory on a writable data partition')
    a = p.parse_args()
    output = a.output.resolve()
    archive = output.with_name(output.name + '.tar.gz')
    if output.exists() or archive.exists():
        p.error('result directory/archive already exists; choose a new name')
    here = Path(__file__).resolve().parent
    package = here / 'PACKAGE.json'
    if package.exists():
        metadata = json.loads(package.read_text())
        for name, expected in metadata['files'].items():
            path = (here / name).resolve()
            if here not in path.parents or digest(path) != expected:
                raise ValueError('inventory package checksum mismatch: ' + name)
    else:
        metadata = {'source_identity': 'unpackaged checkout; no package provenance'}
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, str(here / 'inventory.py'), '--output', str(output / 'inventory.json')], check=True)
    try:
        gui = subprocess.run(['pidof', 'x6100_gui'], capture_output=True, text=True, timeout=3)
        gui_observation = {'command_available': True, 'running': gui.returncode == 0}
    except (OSError, subprocess.TimeoutExpired):
        gui_observation = {'command_available': False, 'running': None}
    (output / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'kind': 'baseline-inventory',
        'collected_utc': datetime.now(timezone.utc).isoformat(), 'package': metadata,
        'gui_process_observation': gui_observation, 'audio_recorded': False, 'radio_settings_changed': False,
        'routing_tested': False, 'performance_tested': False, 'automatic_upload': False}, indent=2) + '\n')
    (output / 'OPERATOR.md').write_text('Record with returned results:\n\n'
        '- Application version and Xiegu baseband version from Info:\n'
        '- Hand microphone model; built-in mic available:\n'
        '- Working/spare card and settings backup status:\n'
        '- Power supply; receive frequency/mode; waterfall settings:\n'
        '- Ordinary receive/speaker/hand-mic behavior and known issues:\n'
        '- Any missing endpoint/mixer observations:\n\n'
        'No microphone, RF or routing experiment was performed by this inventory.\n')
    (output / 'SHA256SUMS').write_text(''.join(digest(f) + '  ' + f.name + '\n' for f in sorted(output.iterdir()) if f.is_file()))
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(output, arcname=output.name)
    print('Inventory archive saved locally:', archive)
    print('Return the archive with application/baseband versions and operator observations. Nothing was uploaded.')


if __name__ == '__main__':
    main()
