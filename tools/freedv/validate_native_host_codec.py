#!/usr/bin/env python3
"""Exercise the real codec-enabled adapter on the host before SDK image builds.

Uses pinned disposable native sources and the same guarded/parallel source
patches as the SDK, with ARM NEON disabled on the host. No radio/audio device.
"""
import argparse
import json
from pathlib import Path
import subprocess
from build_benchmark_package import apply_rade_acquisition_parallel
from native_image import sha
from validate_native_host import prepare_host_sndfile, prepare_host_sqlite

ROOT=Path(__file__).resolve().parents[2]
# Must match the pinned SDK builder's baseline exactly. Patch-manifest hash
# checks reject any drift before enabling the acquisition worker source.
NEON_CMAKE='''
option(RADE_ACQ_NEON "Experimental four-time-lane ARM acquisition" OFF)
if(RADE_ACQ_NEON)
  if(NOT CMAKE_CROSSCOMPILING)
    message(FATAL_ERROR "RADE_ACQ_NEON is enabled only for the matching ARM SDK")
  endif()
  set_source_files_properties(rade_acq.c PROPERTIES
    COMPILE_DEFINITIONS RADE_ACQ_NEON=1
    COMPILE_OPTIONS "-marm;-mfpu=neon-vfpv4;-ffp-contract=fast")
endif()
'''


def run(*args,**kwargs):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=2)
    a=parser.parse_args(); work=a.work.resolve(); work.mkdir(parents=True,exist_ok=False)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    sndfile=prepare_host_sndfile(a.jobs)
    sqlite=prepare_host_sqlite(a.jobs)
    package=work/'reference-build'
    run('python3',ROOT/'tools/freedv/build_benchmark_package.py','--host-validation','--work',package,'--jobs',a.jobs)
    source=package/'sources/rade_c'
    run('git','-C',source,'apply','--check',ROOT/'tools/freedv/patches/rade-acq-neon.patch')
    run('git','-C',source,'apply',ROOT/'tools/freedv/patches/rade-acq-neon.patch')
    cmake=source/'src/CMakeLists.txt'; cmake.write_text(cmake.read_text()+NEON_CMAKE)
    patch=apply_rade_acquisition_parallel(ROOT,source)
    run('cmake','-S',source,'-B',package/'rade_c','-DRADE_ACQ_PARALLEL=ON','-DRADE_ACQ_NEON=OFF')
    run('cmake','--build',package/'rade_c','--target','rade','rade_rx_wav','rade_v1_text_test','-j',a.jobs)
    opus=package/'rade_c/build_opus-prefix/src/build_opus'
    run('python3',ROOT/'tools/freedv/validate_shared_speech.py','--rade-source',source/'src',
        '--rade-library',package/'rade_c/src/librade.so.0.1','--opus-build',opus,
        '--fixtures',package/'package/fixtures','--output',work/'shared-speech-checks')
    paths={'FREEDV_RADE_SOURCE':source/'src','FREEDV_RADE_LIBRARY':package/'rade_c/src/librade.so.0.1',
           'FREEDV_OPUS_BUILD':opus,'FREEDV_OPUS_LIBRARY':opus/'.libs/libopus.a',
           'FREEDV_SAMPLERATE_LIBRARY':package/'samplerate/src/libsamplerate.a',
           'FREEDV_SAMPLERATE_INCLUDE_DIR':package/'sources/libsamplerate-0.2.2/include'}
    build=ROOT/'build-host'
    run('cmake','-S',ROOT,'-B',build,'-U','__pkg_config_checked_deps','-U','__pkg_config_checked_SQLITE3',
        '-U','pkgcfg_lib_deps_sndfile','-U','pkgcfg_lib_deps_sqlite3','-U','pkgcfg_lib_SQLITE3_sqlite3',
        *sndfile['cmake_args'],*sqlite['cmake_args'],'-DHOST_BUILD=ON','-DENABLE_FREEDV_DIAGNOSTICS=ON',
        '-DENABLE_FREEDV_NATIVE_CODEC=ON',*['-D'+k+'='+str(v) for k,v in paths.items()])
    run('cmake','--build',build,'--target','freedv_native_audio_codec_adapter_test',
        'freedv_native_stream_test','freedv_native_stream_fixture_test','-j',a.jobs)
    results={}
    for executable,args in [('freedv_native_stream_test',['--unpaced']),
                            ('freedv_native_stream_fixture_test',[package/'package/fixtures/rade-modem.raw']),
                            ('freedv_native_audio_codec_adapter_test',[package/'package/fixtures/rade-modem.raw'])]:
        result=run(build/executable,*args,capture_output=True,text=True)
        if 'PASS:' not in result.stdout: raise ValueError(executable+' did not complete assertions')
        (work/(executable+'.stdout')).write_text(result.stdout); (work/(executable+'.stderr')).write_text(result.stderr)
        results[executable]={'status':'PASS','stdout':result.stdout}
    summary={'schema_version':1,'status':'PASS','hardware_qualified':False,'timing_qualifies_radio':False,
             'host_sndfile':sndfile,'host_sqlite':sqlite,'firmware_commit':revision,'parallel_patch_sha256':patch['patch_sha256'],
             'shared_speech_summary_sha256':sha(work/'shared-speech-checks/summary.json'),'executed_checks':results}
    (work/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('PASS: full native codec/adapter, shared reference equality and bounded lifecycle checks')


if __name__=='__main__': main()
