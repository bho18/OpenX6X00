#!/usr/bin/env python3
"""Execute SDK benchmarks under ARM emulation; never qualifies radio performance."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', required=True, type=Path)
    p.add_argument('--sysroot', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    package, sysroot = a.package.resolve(), a.sysroot.resolve()
    if json.loads((package/'package-manifest.json').read_text())['target'] != 'x6200-buildroot':
        p.error('expected matching SDK package')
    a.output.mkdir(parents=True, exist_ok=False)
    cases = [('700E','tx','700e-speech'),('700E','rx','700e-modem'),('RADE-V1','tx','rade-speech'),('RADE-V1','rx','rade-modem')]
    results = []
    for mode, direction, fixture in cases:
        name = mode + '-' + direction
        executable = 'codec2-frame-bench' if mode == '700E' else 'rade-frame-bench'
        command = ['qemu-arm', '-cpu', 'cortex-a7', '-L', str(sysroot), '-E',
                   'LD_LIBRARY_PATH=' + ':'.join(map(str, [package/'lib',sysroot/'usr/lib',sysroot/'lib'])),
                   str(package/'bin'/executable), direction, str(package/'fixtures'/(fixture+'.raw')), '3','unpaced']
        command += ['resample','1'] if mode == '700E' else ['1','continuous']
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (a.output/(name+'.stdout')).write_text(completed.stdout)
            (a.output/(name+'.stderr')).write_text(completed.stderr)
            if completed.returncode:
                raise RuntimeError(completed.stderr[-1500:] or 'benchmark exited '+str(completed.returncode))
            result = json.loads(completed.stdout)
            assert result['mode']==mode and result['direction']==direction and result['frames']>0
            assert result['output_samples']>0 and not result['radio_io']
            if direction=='rx':
                assert result['sync_frames' if mode=='700E' else 'sync_blocks']>0
            results.append({'case':name,'passed':True,'opus_arch':result.get('opus_arch')})
        except Exception as error:
            message=(name+': '+str(error)).replace('%','%25').replace('\r','%0D').replace('\n','%0A')
            print('::error::'+message,flush=True)
            raise
    (a.output/'summary.json').write_text(json.dumps({'schema_version':1,'execution_environment':'QEMU user-mode cortex-a7',
        'radio_hardware_tested':False,'timing_qualifies_radio':False,'cases':results},indent=2)+'\n')
    print('PASS: SDK ARM TX/RX execution and synchronization under emulation; timing does not qualify radio')


if __name__=='__main__':
    main()
