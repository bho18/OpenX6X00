#!/usr/bin/env python3
"""Install a pinned 132-second prefix of the already validated continuous RADE over.

This avoids looping an end-of-over waveform in the two-minute speaker case.
Download/authentication uses gh; signed redirect errors are never printed.
"""
import argparse
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
from zipfile import ZipFile
from fetch_native_baseline import REPOSITORY, safe_path, sha, verify_checksums

ARTIFACT = 12782732680
ZIP_SHA = '47ee4e99cd6b057ad6f8d52c862a8b38383cf61027be305ea84bba9f93a34e71'
# These are the uploaded CI waveform used in the recorded radio qualification
# (docs/freedv/rade-long-sync-radio-2026-10-27.md), not a local regeneration.
# Opus host dispatch can change waveform bytes across hosts; the artifact ZIP,
# full waveform and extracted prefix are independently pinned here.
SOURCE_SHA = 'c58f6ba9ed88c785712f4591e42b042ff71e81e376653c277ca8d9d6c7c95e37'
PREFIX_SHA = '0a5b202f2637ecd4ca78f2c2d2c4e17aff7d6ba12a07f9c612d8df9cbd26e034'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-work',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--source-directory',type=Path,help='optional existing canonical fixture artifact')
    a=parser.parse_args(); work=a.work.resolve(); work.mkdir(parents=True,exist_ok=False)
    source=a.source_directory.resolve() if a.source_directory else work/'artifact'
    if not a.source_directory:
        archive=work/'fixture.zip'
        with archive.open('xb') as out:
            result=subprocess.run(['gh','api',f'repos/{REPOSITORY}/actions/artifacts/{ARTIFACT}/zip'],stdout=out,stderr=subprocess.PIPE)
        if result.returncode: raise RuntimeError('continuous fixture artifact download failed; signed redirect omitted')
        if sha(archive)!=ZIP_SHA: raise ValueError('fixture artifact ZIP hash differs')
        source.mkdir()
        with ZipFile(archive) as z:
            seen=set()
            for m in z.infolist():
                target=safe_path(source,m.filename)
                if m.filename in seen or stat.S_ISLNK(m.external_attr>>16): raise ValueError('duplicate/link fixture archive entry')
                seen.add(m.filename)
                if m.is_dir(): target.mkdir(parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(m) as src,target.open('xb') as dst: shutil.copyfileobj(src,dst)
    verify_checksums(source)
    manifest=json.loads((source/'fixture-manifest.json').read_text())
    if sha(source/'fixture.raw')!=SOURCE_SHA:
        raise ValueError('canonical continuous waveform differs from the pinned radio fixture')
    if manifest['fixture']['sha256']!=SOURCE_SHA:
        raise ValueError('canonical continuous fixture manifest differs from the pinned waveform')
    if manifest['fixture']['first_eoo_sample']<=132*8000:
        raise ValueError('canonical continuous fixture reaches end-of-over inside the 132-second prefix')
    package=a.package_work.resolve()/'package'
    metadata_path=package/'package-manifest.json'; metadata=json.loads(metadata_path.read_text())
    if metadata['target']!='x6200-buildroot': raise ValueError('matching-SDK package required')
    for key,pin in [('rade_c_commit','rade_c'),('opus_commit','opus')]:
        if manifest['sources'][key]!=metadata['sources'][pin]['commit']: raise ValueError('continuous fixture source pin differs')
    if manifest['sources']['opus_model_sha256']!=metadata['sources']['opus']['model_sha256']: raise ValueError('continuous fixture model differs')
    with (source/'fixture.raw').open('rb') as src:
        payload=src.read(132*8000*2)
    prefix=package/'fixtures/rade-continuous.raw'; prefix.write_bytes(payload)
    if sha(prefix)!=PREFIX_SHA: raise ValueError('continuous fixture prefix differs')
    record={'schema_version':1,'kind':'rade-v1-continuous-diagnostic-prefix','sample_rate_hz':8000,'channels':1,
            'sample_format':'signed-16-bit-little-endian','seconds':132,'bytes':len(payload),'sha256':PREFIX_SHA,
            'source_artifact_id':ARTIFACT,'source_artifact_zip_sha256':ZIP_SHA,'source_fixture_sha256':SOURCE_SHA,
            'source_offset_samples':0,'contains_eoo':False,'hardware_qualified':False}
    (package/'fixtures/rade-continuous.json').write_text(json.dumps(record,indent=2)+'\n')
    shutil.copy2(source/'fixture-manifest.json',package/'fixtures/rade-continuous-source.json')
    licenses=package/'licenses/continuous-fixture'; licenses.mkdir()
    for item in (source/'licenses').iterdir():
        if item.is_file(): shutil.copy2(item,licenses/item.name)
    # This release's handoff is the consolidated short native session. Keep old
    # operator batches in corresponding source history, not as the runtime README.
    legacy=package/'rade-acquisition-parallel-radio-test.md'
    if legacy.exists(): legacy.unlink()
    guide=Path(__file__).resolve().parents[2]/'docs/freedv/native-radio-test.md'
    shutil.copy2(guide,package/'NATIVE-RADIO-TEST.md')
    (package/'README.md').write_text("""# X6200 native diagnostic candidate package

Use the diagnostic image's Settings → Info → FreeDV Diagnostics controls and
NATIVE-RADIO-TEST.md for the consolidated short hardware session. The normal image
keeps diagnostics disabled. Preserve the working card and use the spare card.

The completed file-only codec measurements remain the baseline. Do not rerun old
qualification/endurance batches routinely. This delivery calls for two minutes
of audible decode, brief locally armed routing/PTT trials, and a 5–10-minute
mixed-turn exercise with one consolidated export.

The tools/freedv benchmark helpers process files only. They cannot arm or key
routing experiments, access physical audio devices, or qualify RF interoperability.
No results or recordings upload automatically. Source/model/package identities
and the continuous 132-second fixture hash accompany this package.
""")
    metadata['native_diagnostic_fixture']=record
    metadata_path.write_text(json.dumps(metadata,indent=2)+'\n')
    hashes=[sha(p)+'  '+str(p.relative_to(package)) for p in sorted(package.rglob('*')) if p.is_file() and p!=package/'SHA256SUMS']
    (package/'SHA256SUMS').write_text('\n'.join(hashes)+'\n')
    package_archive=a.package_work/'freedv-benchmark-package.tar.gz'
    with tarfile.open(package_archive,'w:gz') as archive: archive.add(package,arcname='freedv-benchmark')
    print('PASS: pinned 132-second continuous fixture installed; hardware routing is unqualified')


if __name__=='__main__': main()
