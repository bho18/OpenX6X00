#!/usr/bin/env python3
"""Prepare and optionally build the complete pinned official GUI.

Works on disposable copies only. Archives and the Opus model are hash checked
before any extraction. No git pull, floating backend/RADE checkout or manual
Opus source verification. Installed official releases are a separate track.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile
from kit import sha, write, canonical

LOCK = Path(__file__).with_name('reference-sources.json')


def extract(archive, dest):
    dest.mkdir(parents=True, exist_ok=False)
    files = {}
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            entries = [(Path(i.filename), z.read(i), (i.external_attr >> 16) & 0o777) for i in z.infolist() if not i.is_dir()]
    else:
        with tarfile.open(archive) as t:
            entries = []
            for i in t.getmembers():
                if i.isdir():
                    continue
                if not i.isfile():
                    raise ValueError('nonregular source archive member: '+i.name)
                entries.append((Path(i.name), t.extractfile(i).read(), i.mode & 0o777))
    for name, data, mode in entries:
        if name.is_absolute() or '..' in name.parts or len(name.parts)<2:
            raise ValueError('unsafe source path: '+str(name))
        relative = Path(*name.parts[1:])
        if str(relative) in files:
            raise ValueError('duplicate source path')
        p = dest/relative; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); p.chmod(mode or 0o644)
        files[str(relative)] = hashlib.sha256(data).hexdigest()
    return files


def verify_tree(tree, expected):
    # Generated files may exist after configure; every pinned upstream source
    # file must still match. Apply and record RADE's explicit nnet patches only
    # after this check, in the disposable build source.
    wrong = [name for name, digest in expected.items() if not (tree/name).is_file() or sha(tree/name)!=digest]
    if wrong:
        raise ValueError('upstream source mismatch: '+', '.join(wrong[:10]))


def prepare(archives, model, work):
    pins = json.loads(LOCK.read_text())
    for key, pin in pins.items():
        p = model if key == 'model' else archives/pin['file']
        if not p.is_file() or sha(p)!=pin['sha256']:
            raise ValueError('missing or mismatched pinned source/model: '+str(p))
    work.mkdir(parents=True, exist_ok=False)
    trees = {}
    for key in ('gui','backend','rade','opus'):
        trees[key] = extract(archives/pins[key]['file'], work/key)
        verify_tree(work/key, trees[key])
    # Preserve all original source files and include only the verified model.
    # The GUI builds the official upstream RADE, without our acquisition patches.
    vendored = work/'opus-with-model.zip'
    with zipfile.ZipFile(vendored,'x',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(trees['opus']):
            z.write(work/'opus'/name, 'opus/'+name)
        z.write(model, 'opus/'+pins['model']['file'])
    backend = work/'backend/cmake/BuildRADE.cmake'
    text = backend.read_text()
    text = text.replace('-DOPUS_URL=https://github.com/xiph/opus/archive/'+pins['opus']['commit']+'.zip', '-DOPUS_URL='+vendored.as_uri())
    text = text.replace('   GIT_REPOSITORY https://github.com/freedv/rade_c', '   SOURCE_DIR "'+str(work/'rade')+'"\n   DOWNLOAD_COMMAND ""\n   UPDATE_COMMAND ""')
    text = text.replace('   GIT_TAG main','')
    if 'GIT_TAG main' in text or 'https://github.com/freedv/rade_c' in text:
        raise ValueError('failed to replace floating RADE dependency')
    backend.write_text(text)
    # GCC 14 requires the standard C++ namespace for these math functions.
    # This names the same functions; it does not change codec arithmetic.
    gui_main = work/'gui/src/main.cpp'
    original = gui_main.read_text()
    before = 'if (!(isnan(snrEstimate) || isinf(snrEstimate)) && syncState)'
    after = 'if (!(std::isnan(snrEstimate) || std::isinf(snrEstimate)) && syncState)'
    if original.count(before) != 1:
        raise ValueError('pinned GUI math compatibility context changed')
    gui_main.write_text('#include <cmath>\n' + original.replace(before, after))
    gui_interface = work/'gui/src/freedv_interface.cpp'
    original_interface = gui_interface.read_text()
    if original_interface.count('isnan(tmpStats->snr_est)') != 1 or original_interface.count('isinf(tmpStats->snr_est)') != 1:
        raise ValueError('pinned GUI interface math compatibility context changed')
    gui_interface.write_text('#include <cmath>\n' + original_interface.replace('isnan(tmpStats->snr_est)', 'std::isnan(tmpStats->snr_est)').replace('isinf(tmpStats->snr_est)', 'std::isinf(tmpStats->snr_est)'))
    result = {'schema_version':1,'status':'INCONCLUSIVE','kind':'complete-gui-build',
              'source_verification':'PASS','pins':pins,'upstream_file_manifests':trees,
              'opus_upstream_verified':True,'vendored_opus_zip_sha256':sha(vendored),
              'backend_pin_adapter_sha256':sha(backend),'gui_math_namespace_adapter_sha256':{'main.cpp':sha(gui_main),'freedv_interface.cpp':sha(gui_interface)},'hardware_qualified':False,
              'installed_official_release':'BLOCKED: separately record installed package/version/hash/platform/audio settings',
              'gui_binary':None}
    write(work/'reference-manifest.json',result)
    return result


def build(work,result,jobs):
    result.pop('failed_command', None); result.pop('missing', None)
    # Require system development libraries to avoid unpinned fallback builds.
    required = ['gtk+-3.0','sndfile','speexdsp','hamlib','libebur128','samplerate','alsa','libpulse']
    missing = [p for p in required if subprocess.run(['pkg-config','--exists',p]).returncode]
    if not shutil.which('wx-config'):
        missing.append('wx-config (wxWidgets >=3.1.3 with core/base/aui/html/net/adv/propgrid)')
    if missing:
        result.update(status='BLOCKED',missing=missing);return result
    config=['cmake','-S',str(work/'gui'),'-B',str(work/'build'),'-DCMAKE_BUILD_TYPE=RelWithDebInfo',
            '-DUSE_NATIVE_AUDIO=ON','-DUSE_STATIC_DEPS=OFF','-DBOOTSTRAP_WXWIDGETS=OFF',
            '-DFETCHCONTENT_SOURCE_DIR_FREEDV_BACKEND='+str(work/'backend'),'-UDBus_LIBRARIES']
    commands=[config,['cmake','--build',str(work/'build'),'--target','freedv','--parallel',str(jobs)]]
    result['commands']=commands;result['dependencies']={p:subprocess.check_output(['pkg-config','--modversion',p],text=True).strip() for p in required}
    for i,command in enumerate(commands):
        with (work/f'build-{i}.log').open('w') as f:
            p=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT)
        if p.returncode:
            result.update(status='FAIL',failed_command=i);return result
    binary=work/'build/src/freedv'
    if not binary.is_file():
        result.update(status='FAIL',reason='complete GUI executable missing');return result
    result.update(status='PASS',gui_binary={'path':str(binary),'sha256':sha(binary)},
                  cmake_cache_sha256=sha(work/'build/CMakeCache.txt'))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archives',type=Path,required=True);p.add_argument('--model',type=Path,required=True)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--build',action='store_true');p.add_argument('--jobs',type=int,default=2)
    a=p.parse_args()
    try:
        work=a.work.resolve();r=prepare(a.archives,a.model,work)
        if a.build:r=build(work,r,a.jobs)
        write(work/'reference-manifest.json',r)
        print(json.dumps({k:v for k,v in r.items() if k!='upstream_file_manifests'},indent=2))
        return 0 if r['status']=='PASS' else 1
    except (ValueError,OSError) as e:
        print('BLOCKED:',e);return 2


if __name__=='__main__':
    raise SystemExit(main())
