#!/usr/bin/env python3
"""Compile and execute the new streaming engine with the matching SDK under QEMU.

The test uses unpaced bounded producer/consumer handshakes; QEMU speed is never
assessed as radio throughput. Missing or failed engine assertions fail the gate.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from native_image import run, sha

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('sdk','benchmark-work','output'): parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args()
    for name in ('CPATH','LIBRARY_PATH','PKG_CONFIG_PATH','CMAKE_PREFIX_PATH'):
        os.environ.pop(name,None)
    sdk=a.sdk.resolve(); work=a.benchmark_work.resolve(); out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    metadata=json.loads((work/'package/package-manifest.json').read_text())
    if metadata['target']!='x6200-buildroot' or not metadata.get('rade_acquisition_parallel_experiment',{}).get('enabled'):
        raise ValueError('validated matching-SDK parallel acquisition package required')
    compilers=sorted((sdk/'bin').glob('arm*-gcc'),key=lambda p:('buildroot' not in p.name,p.name))
    if not compilers: raise ValueError('matching SDK compiler missing')
    cc=compilers[0]; sysroot=Path(run(cc,'-print-sysroot').strip())
    opus=work/'rade_c/build_opus-prefix/src/build_opus'; rade=work/'sources/rade_c/src'
    command=[cc,'-O2','-g','-std=c11','-Wall','-Wextra','-Werror','-Wno-error=cpp','-UNDEBUG',
             '-DRADE_ACQ_PARALLEL=1','-include',opus/'config.h','-pthread']
    command+=['-I'+str(p) for p in (ROOT,rade,opus,opus/'include',opus/'dnn',opus/'celt',work/'sources/libsamplerate-0.2.2/include')]
    sources=[ROOT/'tools/freedv/rade_stream.c',ROOT/'tools/freedv/rade_stream_test.c',ROOT/'src/freedv/capture_ring.c']
    libraries=[work/'rade_c/src/librade.so.0.1',opus/'.libs/libopus.a',work/'samplerate/src/libsamplerate.a']
    ne10=sysroot/'usr/lib/libNE10.so'
    if ne10.is_file(): libraries.append(ne10)
    executable=out/'rade-stream-test'
    subprocess.run(list(map(str,command+sources+libraries+['-lm','-o',executable])),check=True)
    environment='LD_LIBRARY_PATH='+':'.join(map(str,(work/'rade_c/src',sysroot/'usr/lib',sysroot/'lib')))
    result=subprocess.run(['qemu-arm','-cpu','cortex-a7','-L',str(sysroot),'-E',environment,str(executable),'--unpaced'],
                          check=True,capture_output=True,text=True,timeout=900)
    (out/'stdout.txt').write_text(result.stdout); (out/'stderr.txt').write_text(result.stderr)
    if 'PASS:' not in result.stdout or 'bounded' not in result.stdout:
        raise ValueError('streaming correctness test did not report its completed checks')
    fixture_source=ROOT/'tools/freedv/rade_stream_fixture_test.c'
    fixture_executable=out/'rade-stream-fixture-test'
    fixture_sources=[ROOT/'tools/freedv/rade_stream.c',fixture_source,ROOT/'src/freedv/capture_ring.c']
    subprocess.run(list(map(str,command+fixture_sources+libraries+['-lm','-o',fixture_executable])),check=True)
    fixture_result=subprocess.run(['qemu-arm','-cpu','cortex-a7','-L',str(sysroot),'-E',environment,
        str(fixture_executable),str(work/'package/fixtures/rade-modem.raw')],check=True,capture_output=True,text=True,timeout=900)
    (out/'fixture-stdout.txt').write_text(fixture_result.stdout); (out/'fixture-stderr.txt').write_text(fixture_result.stderr)
    if 'PASS:' not in fixture_result.stdout:
        raise ValueError('synchronized streaming test did not complete its assertions')
    record={'schema_version':1,'status':'PASS','execution_environment':'QEMU cortex-a7 unpaced correctness',
            'hardware_qualified':False,'timing_qualifies_radio':False,
            'compiler_version':run(cc,'--version').splitlines()[0],
            'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in sources+[fixture_source,ROOT/'tools/freedv/rade_stream.h',ROOT/'tools/freedv/rade_speech.h',ROOT/'src/freedv/capture_ring.h']},
            'executable_sha256':sha(executable),'stdout':result.stdout,
            'synchronized_fixture_test':{'executable_sha256':sha(fixture_executable),'stdout':fixture_result.stdout}}
    (out/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
    print('PASS: matching-SDK bounded streaming correctness; radio performance remains unqualified')


if __name__=='__main__': main()
