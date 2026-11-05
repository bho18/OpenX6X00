#!/usr/bin/env python3
"""Package file-only test helpers and operator instructions; never builds firmware."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FILES = {'START-HERE.md':'docs/freedv/candidate-test.md',
    'candidate-test.md':'docs/freedv/candidate-test.md',
    'native-half-duplex-qualification.md':'docs/freedv/native-half-duplex-qualification.md',
    **{n:'tools/freedv/'+n for n in
    ['Invoke-FreeDVAgent.ps1','route_agent.py','radio_retest.py','capture_source_watch.py',
     'check_capture_continuity.py','analyze_sdr_offset.py','assess_native_conversation.py']}}


def build(output):
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    payloads = {name: (ROOT / source).read_bytes() for name, source in FILES.items()}
    payloads['SOURCE.txt'] = ('Repository: bho18/OpenX6X00\nHelper checkout commit: ' + revision +
        '\nUse candidate-manifest.json for the image identity and per-file hashes for helpers.'
        '\nCurrent hardware scope: receive-only endpoint/PTT availability mapping.'
        '\nLegacy Stage is withdrawn. Inventory success grants no TX.'
        '\nNo production profile is qualified.\n').encode()
    payloads['DEPENDENCIES.txt'] = (
        'Current inventory/collection/conversation assessor: Python 3.9+ standard library; Windows runner uses PowerShell and existing SSH/SCP.\n'
        'Optional historical SDR offset analysis only: NumPy and SciPy. This tool is not a prerequisite for receive-only inventory.\n'
        'Host C simulation/regression builds run from the full source checkout, not from this operator kit.\n').encode()
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
