#!/usr/bin/env python3
"""Package file-only test helpers and operator instructions; never builds firmware."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FILES = {
    'START-HERE.md': 'docs/freedv/native-session-1.md',
    'SESSION-2.md': 'docs/freedv/native-session-2.md',
    'SDRPP-RECORDING.md': 'docs/freedv/sdrpp-recording.md',
    'ENGINEER-TOOLS.md': 'docs/freedv/native-evidence-tools.md',
    'native_session.py': 'tools/freedv/native_session.py',
    'inventory.py': 'tools/freedv/inventory.py',
    'Collect-NativeSession.ps1': 'tools/freedv/Collect-NativeSession.ps1',
}


def build(output):
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    payloads = {name: (ROOT / source).read_bytes() for name, source in FILES.items()}
    payloads['START-HERE.md'] = payloads['START-HERE.md'].replace(b'sdrpp-recording.md', b'SDRPP-RECORDING.md')
    payloads['ENGINEER-TOOLS.md'] = payloads['ENGINEER-TOOLS.md'].replace(b'native-session-1.md', b'START-HERE.md').replace(b'native-session-2.md', b'SESSION-2.md')
    payloads['SOURCE.txt'] = ('Repository: bho18/OpenX6X00\nHelper checkout commit: ' + revision +
        '\nUse per-file hashes to identify any uncommitted helper edits.\nInstalled radio image remains 2083e3e7.\n').encode()
    payloads['SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                   for name, data in sorted(payloads.items())).encode()
    # Stored ZIP avoids a gzip/zlib dependency and has deterministic metadata.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(payloads.items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 28, 0, 0, 0)), data)
    print(hashlib.sha256(output.read_bytes()).hexdigest() + '  ' + output.name)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    build(p.parse_args().output)
