#!/usr/bin/env python3
"""Stage verified release assets for a fork-only GitHub Actions uploader.

The temporary transport branch contains <=48 MiB chunks. The tag still points
to the tested application commit, and GitHub rechecks/reconstructs every byte.
No branch, tag or release is sent by this staging command itself.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from publish_operational_release import ASSETS, REPOSITORY, simple_tag, validate_assets

CHUNK_BYTES = 48*1024*1024

ASSEMBLE = '''#!/usr/bin/env python3
import hashlib,json,re,sys
from pathlib import Path
if sys.flags.optimize: raise RuntimeError('Optimized Python is forbidden for transport verification')
root=Path(__file__).resolve().parent
m=json.loads((root/'transport-manifest.json').read_text())
assert m['repository']=='bho18/OpenX6X00'
assert re.fullmatch('[0-9a-f]{40}',m['source_commit'])
assert re.fullmatch(r'freedv-operational-beta-[0-9]{8}\\.[1-9][0-9]*',m['tag'])
expected={'candidate-manifest.json','software-verification.json','candidate-source.bundle','candidate-source.tar.xz','image-abi.json','START-HERE.md','usb-rf-test-kit.zip','x6200-usb-rf-windows.zip','SHA256SUMS'}
assert set(m['assets'])==expected
output=Path(__import__('sys').argv[1]);output.mkdir(parents=True,exist_ok=False)
for name,entry in m['assets'].items():
    h=hashlib.sha256();count=0
    with (output/name).open('xb') as target:
        for part in entry['parts']:
            assert re.fullmatch(r'part-[0-9]{4}',part['name'])
            path=root/'assets'/part['name'];data=path.read_bytes()
            assert len(data)<=48*1024*1024 and len(data)==part['bytes']
            assert hashlib.sha256(data).hexdigest()==part['sha256']
            target.write(data);h.update(data);count+=len(data)
    assert count==entry['bytes'] and h.hexdigest()==entry['sha256']
notes=(root/'release-notes.md').read_bytes()
assert hashlib.sha256(notes).hexdigest()==m['notes_sha256']
print('Reconstructed all nine exact verified assets')
'''

WORKFLOW = '''name: Publish verified FreeDV beta
on:
  push:
    branches: ['publish/freedv-operational-beta-*']
permissions:
  contents: write
jobs:
  publish:
    if: github.repository == 'bho18/OpenX6X00'
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          path: transport
      - name: Read immutable product identity
        id: product
        run: |
          python3 - <<'PY'
          import json,os,re
          from pathlib import Path
          m=json.loads(Path('transport/transport-manifest.json').read_text())
          assert m['repository']=='bho18/OpenX6X00'
          assert re.fullmatch('[0-9a-f]{40}',m['source_commit'])
          assert re.fullmatch(r'freedv-operational-beta-[0-9]{8}\\.[1-9][0-9]*',m['tag'])
          with open(os.environ['GITHUB_OUTPUT'],'a') as f:
              f.write('source_commit='+m['source_commit']+'\\n')
              f.write('tag='+m['tag']+'\\n')
          PY
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          repository: bho18/OpenX6X00
          ref: ${{ steps.product.outputs.source_commit }}
          path: source
          fetch-depth: 0
      - name: Reconstruct, verify, upload and verify fresh downloads
        env:
          GH_TOKEN: ${{ github.token }}
          PRODUCT: ${{ steps.product.outputs.source_commit }}
          TAG: ${{ steps.product.outputs.tag }}
        run: |
          set -euo pipefail
          python3 transport/assemble.py "$RUNNER_TEMP/assets"
          mode=--publish
          if gh release view "$TAG" --repo bho18/OpenX6X00 >/dev/null 2>&1; then mode=--complete-existing; fi
          python3 source/tools/freedv/publish_operational_release.py \\
            --assets "$RUNNER_TEMP/assets" --tag "$TAG" --expected-commit "$PRODUCT" \\
            "$mode" --notes transport/release-notes.md --download "$RUNNER_TEMP/downloads"
      - name: Preserve publication verification
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: published-operational-verification
          path: ${{ runner.temp }}/published-verification.json
          if-no-files-found: error
'''


def stage(assets, tag, expected_commit, notes, output):
    simple_tag(tag)
    validation = validate_assets(assets, tag, expected_commit)
    notes = Path(notes)
    if not notes.is_file() or not notes.read_text().strip():
        raise ValueError('Nonempty verified release notes are required')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    parts = output/'assets'
    parts.mkdir()
    manifest = {'schema': 'x6200.operational-release-transport.v1', 'repository': REPOSITORY,
                'tag': tag, 'source_commit': expected_commit, 'assets': {},
                'notes_sha256': hashlib.sha256(notes.read_bytes()).hexdigest()}
    index = 0
    for name in sorted(ASSETS):
        path = Path(assets)/name
        entry = {'sha256': validation['asset_sha256'][name], 'bytes': path.stat().st_size, 'parts': []}
        with path.open('rb') as stream:
            while data := stream.read(CHUNK_BYTES):
                part = 'part-%04d' % index
                index += 1
                (parts/part).write_bytes(data)
                entry['parts'].append({'name': part, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        manifest['assets'][name] = entry
    shutil.copy2(notes, output/'release-notes.md')
    (output/'transport-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (output/'assemble.py').write_text(ASSEMBLE)
    workflows = output/'.github/workflows'
    workflows.mkdir(parents=True)
    (workflows/'publish.yml').write_text(WORKFLOW)
    (output/'README.md').write_text('Temporary verified asset transport. Product source: '+expected_commit+'. Download full images from the GitHub release.\n')
    return {'status': 'STAGED', 'repository': REPOSITORY, 'branch': 'publish/'+tag,
            'source_commit': expected_commit, 'parts': index, 'directory': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--notes', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.assets, args.tag, args.expected_commit, args.notes, args.output), indent=2))


if __name__ == '__main__':
    main()
