#!/usr/bin/env python3
"""Download source/model receipts from one completed fork-only CI run."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from build_operational_release import CI_ARTIFACTS, JOBS, REPOSITORY, source_identity, validate_ci


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect(run_id, expected_commit, output):
    if type(run_id) is not int or run_id < 1:
        raise ValueError('CI run ID must be a positive integer')
    identity = source_identity(expected_commit)
    base = 'repos/'+REPOSITORY+'/actions/runs/'+str(run_id)
    run = api(base)
    if run.get('conclusion') != 'success' or run.get('status') != 'completed':
        raise ValueError('The actual CI run has not completed successfully')
    jobs_api = api(base+'/jobs?per_page=100')
    jobs = jobs_api['jobs']
    if {job['name'] for job in jobs} != JOBS:
        raise ValueError('Unexpected CI job set')
    expected = CI_ARTIFACTS
    artifacts_api = api(base+'/artifacts?per_page=100')
    artifacts = artifacts_api['artifacts']
    if any(sum(item['name'] == name for item in artifacts) != 1 for name in expected):
        raise ValueError('Required actual CI receipt artifact names are missing or ambiguous')
    selected = {item['name']: item for item in artifacts if item['name'] in expected}
    if len(selected) != len(expected) or any(item.get('expired') for item in selected.values()):
        raise ValueError('Required actual CI receipts are missing or expired')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {'run': run, 'jobs': jobs, 'tested_source_receipts': {}, 'artifact_downloads': {}, 'api_evidence': {}}
    for name, receipt in (('run', run), ('jobs', jobs_api), ('artifacts', artifacts_api)):
        path = output/(name+'-api.json')
        path.write_text(json.dumps(receipt, indent=2)+'\n')
        result['api_evidence'][name] = {'path': str(path), 'sha256': sha(path), 'receipt': receipt}
    for name, artifact in selected.items():
        archive_path = output/(name+'.zip')
        with archive_path.open('xb') as stream:
            subprocess.run(['gh', 'api', 'repos/'+REPOSITORY+'/actions/artifacts/'+str(artifact['id'])+'/zip'],
                           stdout=stream, check=True)
        if artifact.get('digest') != 'sha256:'+sha(archive_path):
            raise ValueError('Actual CI artifact API digest differs: '+name)
        filename, job = expected[name]
        with zipfile.ZipFile(archive_path) as archive:
            if archive.namelist() != [filename] or archive.testzip() is not None:
                raise ValueError('Unexpected CI receipt artifact contents: '+name)
            receipt_path = output/filename
            receipt_path.write_bytes(archive.read(filename))
        entry = {'path': str(receipt_path), 'sha256': sha(receipt_path),
                 'receipt': json.loads(receipt_path.read_text())}
        result['artifact_downloads'][name] = {'path': str(archive_path), 'sha256': sha(archive_path),
                                             'github_api_digest': artifact['digest'], 'artifact_id': artifact['id']}
        if job:
            result['tested_source_receipts'][job] = entry
        else:
            result['model_acquisition_receipt'] = entry
    validate_ci(result, identity)
    (output/'ci-result.json').write_text(json.dumps(result, indent=2)+'\n')
    return output/'ci-result.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(collect(args.run_id, args.expected_commit, args.output))


if __name__ == '__main__':
    main()
