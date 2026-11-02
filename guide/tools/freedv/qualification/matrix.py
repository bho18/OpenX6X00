#!/usr/bin/env python3
"""Render the acceptance catalog; pending evidence never becomes PASS."""
import argparse
import json
from pathlib import Path


def render(cases):
    lines=['# Acceptance-to-tool matrix','',
        'Generated from `tools/freedv/qualification/cases.json`. These are pending qualification gates, not a relabeling of development tests. Current software results are indexed in [implementation-status.md](implementation-status.md).','',
        'For **every row**, `preflight.json` records the exact build ID, binary/source/tool SHA256, complete profile hash, equipment and selected case. `run.json` holds measurements, recording paths/hashes and reviewer assessment. `collection.json` hashes retained files. Evidence below is the required case directory; hashes remain pending until collection. Missing prerequisites are BLOCKED; missing evidence or listening is INCONCLUSIVE.','',
        '| Gate | Executable / procedure | Required equipment and tools | Measurements / recordings | Pass criteria | Evidence and hashes | Status |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    def cell(value):return str(value).replace('|',r'\|').replace('\n',' ')
    for c in cases:
        hardware=any(e in c['equipment'] for e in ('x6200','candidate_image','event_rf_correlation_setup',
            'installed_official_gui_release','numbered_speech'))
        values=[c['id']+' — '+c['title'],('ctest -R '+c['ctest']+'; ' if c.get('ctest') else '')+c['procedure'],
            ', '.join(c['equipment']+c['tools']),', '.join(c['measurements'])+' / '+', '.join(c['recordings']),c['criteria'],
            'evidence/'+c['id']+'/run.json + preflight.json; SHA256 pending','BLOCKED' if hardware else 'INCONCLUSIVE']
        lines.append('| '+' | '.join(cell(v) for v in values)+' |')
    lines+=['','Run only selected cases covered by the preflight. [Integration procedures](integration-cases.md), [matched profiles](matched-profiles.md), [GUI tracks](official-gui.md) and [RF timing](timing.md) supply operator detail. Keep large IQ on the recording computer with its verified hash and storage reference. Historical failures retain their exact images/routes and original identities.','']
    return '\n'.join(lines)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    cases=json.loads(Path(__file__).with_name('cases.json').read_text())['cases']
    args.output.write_text(render(cases))
