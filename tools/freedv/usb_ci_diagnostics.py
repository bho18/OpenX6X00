#!/usr/bin/env python3
"""Show bounded CI failure logs and retain only failed public-fixture audio.

This tool operates on a disposable Actions runner's private-Pulse test output.
It never contacts a radio or collects operator recordings. Diagnostic failures
are reported without replacing the original build/campaign exit status.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

DEFAULT_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_FILE_BYTES = 16 * 1024 * 1024
MAX_FILES = 128
MAX_FAILED_CASES = 3
# The indexed capture interposer uses .pcm; other generated controller,
# monitor and FIFO recordings use .raw. Keep this extra name explicit so an
# arbitrary .pcm attachment cannot enter the public-fixture artifact.
INDEXED_CAPTURE_PCM = 'mic-observer.pcm'


def safe_file(path, root):
    path, root = Path(path), Path(root).resolve()
    return not path.is_symlink() and path.is_file() and root in path.resolve().parents


def read_record(path, errors):
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('record exceeds diagnostic limit')
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise ValueError('record is not an object')
        return record
    except (OSError, ValueError, TypeError) as error:
        errors.append(path.name + ': ' + str(error))
        return {}


def tail(path, root, limit=16000):
    if not safe_file(path, root):
        return
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - limit))
            payload = stream.read(limit)
        print('Retained log: ' + str(path.relative_to(root)))
        print(payload.decode('utf-8', errors='replace'))
    except OSError as error:
        print('Log unavailable: ' + path.name + ': ' + str(error))


def failed_cases(matrix):
    selected = []
    cases = matrix.get('cases', {})
    if not isinstance(cases, dict):
        cases = {}
    for name, case in cases.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', name):
            continue
        if not isinstance(case, dict):
            continue
        runs = case.get('runs', [])
        if not isinstance(runs, list):
            continue
        for run in runs:
            if not isinstance(run, dict):
                continue
            repeat = run.get('repeat')
            if run.get('status') != 'PASS' and type(repeat) is int and 1 <= repeat <= 100:
                selected.append((name, repeat))
    # A killed driver may have only its durable pre-execution progress record.
    started = {item for item in matrix.get('started', []) if isinstance(item, str)}
    executed = {item for item in matrix.get('executed', []) if isinstance(item, str)}
    for execution in started - executed:
        if isinstance(execution, str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}@[1-9][0-9]?', execution):
            name, repeat = execution.rsplit('@', 1)
            selected.append((name, int(repeat)))
    return sorted(set(selected))[:MAX_FAILED_CASES]


def diagnose(work_root, output=None, max_total=DEFAULT_TOTAL_BYTES, max_file=DEFAULT_FILE_BYTES):
    root = Path(work_root).resolve()
    matrix_root = root / 'usb-controller-matrix'
    build_root = root / 'usb-controller-build'
    report = {'schema': 'x6200.usb.ci-failure-fixtures.v1', 'scope': 'Public host-test fixture PCM only',
              'radio_accessed': False, 'copied': [], 'omitted': [], 'errors': [], 'copied_bytes': 0,
              'maximum_total_bytes': max_total, 'maximum_file_bytes': max_file}
    tail(root / 'usb-controller-build.log', root)
    tail(root / 'usb-controller-matrix.log', root)
    build_path = build_root / 'build-record.json'
    if build_path.exists():
        build = read_record(build_path, report['errors'])
        print('Controller build status: ' + str(build.get('status')) + '; ' + str(build.get('reason', '')))
        for command in build.get('commands', []):
            if isinstance(command, dict) and command.get('status') != 'PASS':
                name = command.get('log', '')
                if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', name):
                    tail(build_root / name, root)
    record_path = matrix_root / 'matrix-result.json'
    if not record_path.exists():
        record_path = matrix_root / 'progress.json'
    matrix = read_record(record_path, report['errors']) if record_path.exists() else {}
    print('Controller matrix status: ' + str(matrix.get('status')) + '; ' + str(matrix.get('reason', '')))
    selected = failed_cases(matrix)
    report['failed_cases'] = [{'case': name, 'repeat': repeat} for name, repeat in selected]
    destination = Path(output).resolve() if output else None
    if destination:
        destination.mkdir(parents=True, exist_ok=False)
    considered = 0
    for name, repeat in selected:
        case_root = matrix_root / ('repeat-%02d' % repeat) / name
        print('Controller failed case: ' + name + '@' + str(repeat))
        tail(case_root.with_suffix('.log'), root)
        tail(case_root / 'test.log', root)
        detail_path = case_root / 'case-result.json'
        if detail_path.exists():
            details = read_record(detail_path, report['errors'])
            print('Case reason: ' + str(details.get('error', details.get('reason', 'unavailable'))))
        if not destination or not case_root.is_dir() or case_root.is_symlink():
            continue
        candidates = sorted(set(case_root.rglob('*.raw')) |
                            set(case_root.rglob(INDEXED_CAPTURE_PCM)))
        for path in candidates:
            relative = path.relative_to(matrix_root)
            omitted = {'source': str(relative)}
            considered += 1
            if considered > MAX_FILES:
                report['omitted'].append({**omitted, 'reason': 'file_count_limit'})
                break
            if not safe_file(path, matrix_root):
                report['omitted'].append({**omitted, 'reason': 'unsafe_or_nonregular_path'})
                continue
            try:
                size = path.stat().st_size
                if size > max_file:
                    report['omitted'].append({**omitted, 'reason': 'per_file_byte_limit', 'bytes': size})
                    continue
                if size + report['copied_bytes'] > max_total:
                    report['omitted'].append({**omitted, 'reason': 'total_byte_limit', 'bytes': size})
                    continue
                with path.open('rb') as stream:
                    payload = stream.read(max_file + 1)
                if len(payload) > max_file or len(payload) + report['copied_bytes'] > max_total:
                    report['omitted'].append({**omitted, 'reason': 'file_grew_past_limit'})
                    continue
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                report['copied'].append({'source': str(relative), 'bytes': len(payload),
                                         'sha256': hashlib.sha256(payload).hexdigest()})
                report['copied_bytes'] += len(payload)
            except OSError as error:
                report['errors'].append(str(relative) + ': ' + str(error))
    if destination:
        (destination / 'failure-fixtures.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, help='Fresh destination for failed-case fixture PCM only')
    args = parser.parse_args()
    try:
        report = diagnose(args.work_root, args.output)
        print('CI diagnostics: ' + str(len(report['copied'])) + ' bounded fixture files, ' +
              str(report['copied_bytes']) + ' bytes, ' + str(len(report['errors'])) + ' diagnostic errors')
    except Exception as error:
        print('CI diagnostics incomplete: ' + str(error))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
