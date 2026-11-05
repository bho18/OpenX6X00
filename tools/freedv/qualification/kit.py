#!/usr/bin/env python3
"""FreeDV qualification: preflight -> run -> collect -> assess.

No RF control. Operator RF procedures retain local Arm/Run/Stop. PASS is scoped
by case; missing prerequisites are BLOCKED and absent assessments INCONCLUSIVE.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
CATALOG = Path(__file__).with_name('cases.json')
REFERENCE = Path(__file__).with_name('reference-sources.json')
STATUSES = {'PASS', 'FAIL', 'BLOCKED', 'INCONCLUSIVE'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')


def within(root, name):
    p = Path(name)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('artifact must be relative: ' + name)
    p = (root / p).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('artifact escapes bundle: ' + name)
    return p


def cases():
    rows = read(CATALOG)['cases']
    assert len({c['id'] for c in rows}) == len(rows)
    return {c['id']: c for c in rows}


def source_identity():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    files = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).split(b'\0')
    entries = {}
    for raw in files:
        if raw:
            p = ROOT / raw.decode()
            if p.is_file() and not p.is_symlink():
                entries[raw.decode()] = sha(p)
    return {'source_commit': commit, 'source_tree_sha256': canonical(entries), 'files': entries}


def build_manifest(build_id, binaries):
    result = source_identity()
    result.update(schema_version=1, build_id=build_id,
                  binaries={str(p.resolve()): sha(p) for p in binaries})
    return result


def identity(build, profile):
    if not build.get('build_id') or not build.get('source_commit') or not build.get('source_tree_sha256'):
        raise ValueError('build manifest needs build_id, source_commit and source_tree_sha256')
    if not build.get('binaries'):
        raise ValueError('build manifest needs binary hashes')
    if profile.get('local_mode') not in ('USB', 'USB-DIG') or profile.get('upper_sideband') is not True or not profile.get('id'):
        raise ValueError('explicit profile identity, USB/USB-DIG and upper-sideband orientation required')
    return {'build_sha256': canonical(build), 'profile_sha256': canonical(profile),
            'build_id': build['build_id'], 'profile_id': profile['id']}


def preflight(family, build, profile, inventory, tools_lock=None, selected=None):
    ident = identity(build, profile)
    catalog = [c for c in cases().values() if c['family'] == family]
    if not catalog:
        raise ValueError('unknown family: ' + family)
    if selected:
        names={c['id'] for c in catalog}
        if set(selected)-names:
            raise ValueError('selected case does not belong to preflight family')
        catalog=[c for c in catalog if c['id'] in selected]
    needed = sorted(set(x for c in catalog for x in c['equipment']))
    missing = [x for x in needed if inventory.get('equipment', {}).get(x, {}).get('verified') is not True]
    if any(c['scope'] not in ('development','tools') for c in catalog):
        fields=('hardware','build_id','baseband','control_version','capture_device','playback_device','mixer',
                'sample_rate','capture_channel','rx_route','tx_route','effective_settings','evidence_identity')
        for field in fields:
            if field not in profile or profile[field] is None or profile[field]=='':missing.append('profile field:'+field)
        if profile.get('build_id')!=build['build_id']:missing.append('profile/build compatibility')
        if not isinstance(profile.get('effective_settings'),dict) or not profile.get('effective_settings'):missing.append('effective settings')
    versions = {}
    lock = tools_lock or {}
    for tool in sorted(set(x for c in catalog for x in c['tools'])):
        executable = shutil.which(tool)
        if not executable:
            missing.append('tool:' + tool)
            continue
        flag = '-V' if tool in ('mke2fs','e2fsck','debugfs') else '-version' if tool in ('ffmpeg','ffprobe') else '--version'
        p = subprocess.run([executable, flag], capture_output=True, text=True, timeout=15)
        version = (p.stdout + p.stderr).strip()
        versions[tool] = {'path': executable, 'version': version, 'sha256': sha(executable)}
        if p.returncode or not version:
            missing.append('tool version:' + tool)
        if tool not in lock or lock[tool] != versions[tool]['sha256']:
            missing.append('unverified tool lock:' + tool)
    for name, expected in build['binaries'].items():
        p = Path(name)
        if not p.is_file() or sha(p) != expected:
            missing.append('binary identity:' + name)
    validation = inventory.get('tools_validation', {})
    validation_path = Path(validation.get('path', '/nonexistent'))
    if not validation_path.is_file() or sha(validation_path) != validation.get('sha256') or read(validation_path).get('status') != 'PASS':
        missing.append('test-tool validation report')
    verified_inputs=set()
    for item in inventory.get('inputs', []):
        p = Path(item['path'])
        if not p.is_file() or sha(p) != item['sha256']:
            missing.append('input identity:' + str(p))
        else:
            verified_inputs.add(item['sha256'])
    pins=read(REFERENCE)
    required_pins=set()
    if 'pinned_codecs_models' in needed:required_pins.update(('rade','opus','model'))
    if 'pinned_complete_gui' in needed:required_pins.update(pins)
    for name in sorted(required_pins):
        if pins[name]['sha256'] not in verified_inputs:missing.append('pinned source/model input:' + name)
    if inventory.get('recording_format_verified') is not True:
        missing.append('recording format verification')
    free = shutil.disk_usage(ROOT).free
    if free < max(1024**3, inventory.get('minimum_free_bytes', 0)):
        missing.append('storage')
    return {**ident, 'schema_version': 1, 'family': family, 'status': 'BLOCKED' if missing else 'PASS',
            'missing': missing, 'tools': versions, 'free_bytes': free, 'inventory_sha256': canonical(inventory),
            'catalog_sha256': sha(CATALOG), 'kit_sha256': sha(__file__), 'selected_cases':[c['id'] for c in catalog],
            'binaries': build['binaries'], 'created_unix': time.time()}


def artifact(path, role):
    result = {'path': path.name, 'role': role, 'sha256': sha(path), 'bytes': path.stat().st_size}
    if path.suffix.lower() == '.wav':
        with wave.open(str(path), 'rb') as wav:
            result['format'] = {'rate': wav.getframerate(), 'channels': wav.getnchannels(),
                                'sample_bytes': wav.getsampwidth(), 'frames': wav.getnframes()}
    return result


def run(case_id, pre, build_dir, out):
    case = cases()[case_id]
    out.mkdir(parents=True, exist_ok=False)
    if case['family'] != pre['family'] or pre.get('catalog_sha256') != sha(CATALOG) or pre.get('kit_sha256') != sha(__file__):
        raise ValueError('preflight family or tooling identity mismatch')
    if pre.get('selected_cases') is not None and case_id not in pre['selected_cases']:
        raise ValueError('case was not selected and verified at preflight')
    result = {k: pre[k] for k in ('build_sha256', 'profile_sha256', 'build_id', 'profile_id')}
    result.update(schema_version=1, case_id=case_id, complete=False, artifacts=[], measurements={},
                  preflight=pre, status='INCONCLUSIVE', operator_assessment=None)
    write(out / 'preflight.json', pre)
    if pre['status'] != 'PASS':
        result['status'] = 'BLOCKED'
    elif case.get('ctest'):
        for name, expected in pre['binaries'].items():
            binary = Path(name).resolve()
            if not binary.is_relative_to(build_dir.resolve()) or not binary.is_file() or sha(binary) != expected:
                raise ValueError('runner build/binary mismatch: ' + name)
        command = ['ctest', '--test-dir', str(build_dir.resolve()), '--no-tests=error', '--output-on-failure', '-R', case['ctest']]
        p = subprocess.run(command, capture_output=True, text=True)
        (out / 'stdout.txt').write_text(p.stdout)
        (out / 'stderr.txt').write_text(p.stderr)
        result['artifacts'] = [artifact(out / f, role) for f, role in [('stdout.txt', 'runner_stdout'), ('stderr.txt', 'runner_stderr')]]
        result.update(command=command, complete=True, automated_exit=p.returncode,
                      status='PASS' if p.returncode == 0 else 'FAIL')
    else:
        result['pending'] = case['procedure']
    write(out / 'run.json', result)
    return result


def check_artifacts(bundle, result):
    roles, errors = set(), []
    for item in result.get('artifacts', []):
        p = within(bundle, item['path'])
        if not p.is_file() or sha(p) != item.get('sha256') or p.stat().st_size != item.get('bytes'):
            errors.append('missing/mismatched artifact:' + item['path'])
        else:
            roles.add(item['role'])
            if item['role'] in ('original_speech', 'decoded_speech', 'modem_output', 'modem_input', 'raw_capture', 'local_playback', 'rf_demod') and 'format' not in item:
                errors.append('audio recording format undeclared:' + item['path'])
            if 'format' in item:
                with wave.open(str(p), 'rb') as wav:
                    actual = {'rate': wav.getframerate(), 'channels': wav.getnchannels(),
                              'sample_bytes': wav.getsampwidth(), 'frames': wav.getnframes()}
                if actual != item['format'] or actual['sample_bytes'] != 2 or not actual['frames']:
                    errors.append('recording format:' + item['path'])
    # IQ remains on the recording computer. Verify references there during
    # collection; hashes and storage URIs travel in the small evidence bundle.
    for item in result.get('external_recordings', []):
        p = Path(item['local_path'])
        if not item.get('storage_reference') or not p.is_file() or sha(p) != item.get('sha256'):
            errors.append('unverified external recording:' + str(p))
        else:
            roles.add(item['role'])
    return roles, errors


def timing_assessment(m):
    method = m.get('method')
    required = ('event_definition', 'recording_delays', 'resolution', 'uncertainty_basis',
                'setup_validation_sha256', 'event_artifact_sha256', 'rf_artifact_sha256')
    if method not in ('correlated_video_iq', 'synchronized_trigger_envelope') or any(not m.get(k) for k in required):
        return 'INCONCLUSIVE', 'Independent event/RF correlation and setup validation required'
    if m.get('physically_held_ptt') is not False:
        return 'INCONCLUSIVE', 'Held PTT must be measured separately'
    values = [m.get('interval_ms'), m.get('uncertainty_ms')]
    if any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in values):
        return 'INCONCLUSIVE', 'Finite conservative interval/uncertainty required'
    components = m.get('uncertainty_components_ms', {})
    if not components or not isinstance(components, dict) or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in components.values()):
        return 'INCONCLUSIVE', 'Measured uncertainty components required'
    if values[1] < sum(components.values()):
        return 'INCONCLUSIVE', 'Declared uncertainty is smaller than its conservative contributions'
    upper = sum(values)
    return ('PASS' if upper <= 500 else 'FAIL'), f'Conservative RF cessation upper bound: {upper:.3f} ms'


def assess(bundle, build, profile):
    result = read(bundle / 'run.json')
    case = cases()[result['case_id']]
    ident = identity(build, profile)
    if any(result.get(k) != v for k, v in ident.items()):
        raise ValueError('build/profile mismatch')
    pre = result.get('preflight', {})
    if any(pre.get(k) != v for k, v in ident.items()) or pre.get('catalog_sha256') != sha(CATALOG) or pre.get('kit_sha256') != sha(__file__):
        raise ValueError('missing or mismatched preflight')
    if pre.get('family')!=case['family'] or (pre.get('selected_cases') is not None and case['id'] not in pre['selected_cases']):
        raise ValueError('case not covered by preflight')
    stored_preflight=bundle/'preflight.json'
    if not stored_preflight.is_file() or canonical(read(stored_preflight))!=canonical(pre):
        raise ValueError('missing or inconsistent preflight file')
    roles, errors = check_artifacts(bundle, result)
    if pre.get('status') != 'PASS':
        status, reason = 'BLOCKED', 'Preflight prerequisites missing'
    elif errors:
        status, reason = 'INCONCLUSIVE', '; '.join(errors)
    elif set(case['recordings']) - roles:
        status, reason = 'INCONCLUSIVE', 'Missing recordings: ' + ', '.join(sorted(set(case['recordings']) - roles))
    elif result.get('complete') is not True:
        status, reason = 'INCONCLUSIVE', 'Incomplete run'
    elif case.get('ctest'):
        if result.get('automated_exit') is None or not result.get('command'):
            status, reason = 'INCONCLUSIVE', 'No executed runner'
        else:
            status, reason = ('PASS' if result['automated_exit'] == 0 else 'FAIL'), 'Automated development check only'
    else:
        m = result.get('measurements', {})
        required = case['measurements']
        assessment = result.get('operator_assessment') or {}
        if any(k not in m for k in required):
            status, reason = 'INCONCLUSIVE', 'Missing required measurements'
        elif not assessment.get('reviewer') or not assessment.get('date') or not assessment.get('findings'):
            status, reason = 'INCONCLUSIVE', 'Human assessment not recorded'
        elif case.get('listening') and case['id']!='engine-numbered-turns' and (not assessment.get('intelligible_phrases') or not assessment.get('first_words') or
                not assessment.get('last_words') or not assessment.get('speech_artifact_sha256') or
                assessment['speech_artifact_sha256'] not in [a['sha256'] for a in result['artifacts'] if a['role'] == 'decoded_speech']):
            status, reason = 'INCONCLUSIVE', 'Listening acceptance unassessed'
        elif assessment.get('result') not in ('PASS', 'FAIL'):
            status, reason = 'INCONCLUSIVE', 'No explicit assessed outcome'
        elif case['id']=='engine-numbered-turns':
            status,reason=numbered_assessment(result)
            if assessment['result']=='FAIL':status,reason='FAIL',assessment['findings']
        elif case.get('timing'):
            artifacts = result.get('artifacts', []) + result.get('external_recordings', [])
            setup = next((a for a in result.get('artifacts', []) if a['role'] == 'timing_setup_validation'), None)
            event_hashes = [a['sha256'] for a in artifacts if a['role'] == 'control_event_video']
            rf_hashes = [a['sha256'] for a in artifacts if a['role'] == 'rf_iq']
            setup_data = read(within(bundle, setup['path'])) if setup else {}
            if not setup or setup['sha256'] != m.get('setup_validation_sha256') or setup_data.get('status') != 'PASS' or setup_data.get('actual_equipment_verified') is not True or setup_data.get('method') != m.get('method') or m.get('event_artifact_sha256') not in event_hashes or m.get('rf_artifact_sha256') not in rf_hashes:
                status, reason = 'INCONCLUSIVE', 'Timing method/setup/event/RF evidence is not validated and bound to this case'
            else:
                status, reason = timing_assessment(m)
            if assessment['result'] == 'FAIL':
                status = 'FAIL'
        else:
            status, reason = assessment['result'], assessment['findings']
    return {**ident, 'case_id': case['id'], 'status': status, 'reason': reason,
            'scope': case['scope'], 'run_sha256': sha(bundle / 'run.json'),
            'hardware_qualified': False}


def numbered_assessment(result):
    m=result['measurements'];count=m.get('turn_count');identities=m.get('turn_identities')
    if type(count)!=int or not isinstance(identities,list) or any(type(v) not in (int,str) for v in identities):return 'INCONCLUSIVE','Numeric turn count and distinct turn identities required'
    if count<100:return 'FAIL','Fewer than 100 alternating speech turns'
    if len(identities)!=count or len(set(identities))!=count:return 'INCONCLUSIVE','Distinct identity required for every turn'
    review=result['operator_assessment'].get('turn_assessments',[])
    if not isinstance(review,list) or any(not isinstance(r,dict) for r in review):return 'INCONCLUSIVE','Numbered listening assessment must be a list of records'
    if len(review)!=count:return 'INCONCLUSIVE','Listening assessment required for every numbered turn'
    for turn in identities:
        artifacts={role:[a for a in result['artifacts'] if a['role']==role and a.get('turn_id')==turn]
                   for role in ('original_speech','modem_output','decoded_speech')}
        if any(len(a)!=1 for a in artifacts.values()):return 'INCONCLUSIVE','Retained original/modem/decoded artifacts required for every turn'
        rows=[r for r in review if r.get('turn_id')==turn]
        if len(rows)!=1 or any(not rows[0].get(k) for k in ('intelligible_phrase','first_words','last_words')):
            return 'INCONCLUSIVE','Missing numbered listening/content assessment'
        if rows[0].get('decoded_sha256')!=artifacts['decoded_speech'][0]['sha256']:
            return 'INCONCLUSIVE','Numbered listening assessment artifact mismatch'
    return 'PASS','100 or more distinct retained speech turns with per-turn listening assessment'


def collect(bundles, build, profile, destination):
    destination.mkdir(parents=True, exist_ok=False)
    results, seen = [], set()
    for bundle in bundles:
        report = assess(bundle, build, profile)  # never trust a supplied PASS label
        if report['case_id'] in seen:
            raise ValueError('duplicate case id: ' + report['case_id'])
        seen.add(report['case_id'])
        target = destination / report['case_id']
        target.mkdir()
        run_data = read(bundle / 'run.json')
        names = {'run.json', 'preflight.json'} | {a['path'] for a in run_data.get('artifacts', [])}
        for name in names:
            source = within(bundle, name)
            if source.is_file():
                dest = within(target, name); dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
        results.append(report)
    manifest = {str(p.relative_to(destination)): sha(p) for p in sorted(destination.rglob('*')) if p.is_file()}
    write(destination / 'collection.json', {'identity': identity(build, profile), 'results': results, 'artifacts': manifest})
    (destination / 'results.md').write_text('# FreeDV evidence collection\n\n| Case | Status | Scope | Finding |\n| --- | --- | --- | --- |\n' +
        ''.join(f"| {r['case_id']} | {r['status']} | {r['scope']} | {r['reason'].replace('|', '/')} |\n" for r in results) +
        '\nNo bidirectional native-mode qualification is inferred from this collection.\n')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build-identity'); b.add_argument('--build-id', required=True); b.add_argument('--binary', type=Path, action='append', required=True); b.add_argument('--out', type=Path, required=True)
    for cmd in ('preflight', 'run', 'collect', 'assess'):
        p = sub.add_parser(cmd)
        if cmd != 'run':
            p.add_argument('--build', type=Path, required=True); p.add_argument('--profile', type=Path, required=True)
        p.add_argument('--out', type=Path, required=True)
        if cmd == 'preflight':
            p.add_argument('--family', required=True); p.add_argument('--inventory', type=Path, required=True); p.add_argument('--tools-lock', type=Path, required=True)
            p.add_argument('--case',action='append',help='Preflight only these cases within the family (repeatable)')
        if cmd == 'run':
            p.add_argument('--case', required=True); p.add_argument('--preflight', type=Path, required=True); p.add_argument('--build-dir', type=Path, required=True)
        if cmd in ('collect', 'assess'):
            p.add_argument('--bundle', type=Path, required=True, action='append' if cmd == 'collect' else 'store')
    a = parser.parse_args()
    try:
        if a.cmd == 'build-identity':
            result = build_manifest(a.build_id, a.binary); write(a.out, result)
        elif a.cmd == 'preflight':
            result = preflight(a.family, read(a.build), read(a.profile), read(a.inventory), read(a.tools_lock), a.case); write(a.out, result)
        elif a.cmd == 'run':
            result = run(a.case, read(a.preflight), a.build_dir, a.out)
        elif a.cmd == 'assess':
            result = assess(a.bundle, read(a.build), read(a.profile)); write(a.out, result)
        else:
            result = collect(a.bundle, read(a.build), read(a.profile), a.out)
        print(json.dumps(result, indent=2))
        if isinstance(result, dict) and result.get('status') in ('BLOCKED', 'INCONCLUSIVE', 'FAIL'):
            return 1
        return 0
    except (ValueError, KeyError, OSError, wave.Error) as error:
        print('INCONCLUSIVE: ' + str(error), file=sys.stderr); return 2


if __name__ == '__main__':
    sys.exit(main())
