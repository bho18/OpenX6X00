#!/usr/bin/env python3
"""Install hash-locked Debian host dependencies into a workspace prefix only.

No root/system package writes, radio access or credential discovery. Fetching
is explicit with --download. Use the pinned Debian 13 x86-64 base environment.
The radio always uses its separately verified Buildroot SDK.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import urllib.request
from kit import sha, write


def install(lock_paths, cache, prefix, download):
    if platform.machine() != 'x86_64' or platform.freedesktop_os_release().get('ID') != 'debian' or platform.freedesktop_os_release().get('VERSION_ID') != '13':
        raise ValueError('pinned Debian 13 x86-64 base required')
    cache.mkdir(parents=True, exist_ok=True); prefix.mkdir(parents=True, exist_ok=True)
    records = {}
    for lock_path in lock_paths:
        lock = json.loads(lock_path.read_text())
        for name, digest in lock.get('base_files', {}).items():
            if not Path(name).is_file() or sha(name) != digest:
                raise ValueError('base dependency mismatch: '+name)
        for item in lock['packages']:
            name = item.get('file', item.get('filename'))
            if not name or Path(name).name != name:
                raise ValueError('invalid archive name')
            target = cache/name
            if not target.is_file():
                if not download:
                    raise ValueError('missing archive (use --download): '+name)
                partial = cache/(name+'.partial')
                for url in item['urls']:
                    try:
                        with urllib.request.urlopen(url, timeout=60) as src, partial.open('wb') as dst:
                            shutil.copyfileobj(src,dst)
                        if sha(partial) != item['sha256']:
                            raise ValueError('download hash mismatch')
                        partial.replace(target);break
                    except (OSError, ValueError):
                        partial.unlink(missing_ok=True)
                else:
                    raise ValueError('locked archive unavailable: '+name)
            if sha(target) != item['sha256']:
                raise ValueError('cached archive hash mismatch: '+name)
            subprocess.run(['dpkg-deb','-x',str(target),str(prefix)],check=True)
            records[name] = item['sha256']
    # Relocate textual pkg-config/wx-config paths, preserving ELF contents.
    for pattern in ('usr/lib/x86_64-linux-gnu/pkgconfig/*.pc','usr/share/pkgconfig/*.pc',
                    'usr/lib/x86_64-linux-gnu/wx/config/*',
                    'usr/bin/autoconf','usr/bin/autoheader','usr/bin/autom4te','usr/bin/autoreconf',
                    'usr/bin/autoscan','usr/bin/autoupdate','usr/bin/ifnames','usr/bin/aclocal-1.17',
                    'usr/bin/automake-1.17','usr/bin/libtoolize','usr/share/autoconf/**/*.cfg',
                    'usr/share/automake-1.17/**/*.pm'):
        for p in prefix.glob(pattern):
            if p.is_file():
                s = p.read_text().replace('/usr/',str(prefix)+'/usr/').replace('prefix=/usr\n','prefix='+str(prefix)+'/usr\n')
                s = s.replace('this_prefix:-/usr','this_prefix:-'+str(prefix)+'/usr')
                lines = s.splitlines(keepends=True)
                if lines and lines[0].startswith('#!'):
                    lines[0] = lines[0].replace(str(prefix)+'/usr/','/usr/')
                p.write_text(''.join(lines))
    wx = prefix/'usr/lib/x86_64-linux-gnu/wx/config/gtk3-unicode-3.2'
    if wx.is_file():
        alias = prefix/'usr/bin/wx-config'
        if alias.is_symlink(): alias.unlink()
        if not alias.exists(): alias.symlink_to(wx)
    # Resolve only hash-verified existing base file dependencies.
    base = {}
    for lock_path in lock_paths: base.update(json.loads(lock_path.read_text()).get('base_files',{}))
    for p in prefix.rglob('*'):
        if p.is_symlink() and not p.exists():
            target = os.path.normpath(os.path.join(str(p.parent),os.readlink(p)))
            if target.startswith(str(prefix)+'/'):
                host = target[len(str(prefix)):]
                if host in base and Path(host).is_file() and sha(host)==base[host]:
                    p.unlink();p.symlink_to(host)
    for alias, name in [('cc','gcc'),('c++','g++'),('automake','automake-1.17'),('aclocal','aclocal-1.17')]:
        p=prefix/'usr/bin'/alias
        if not p.exists() and not p.is_symlink() and (prefix/'usr/bin'/name).is_file(): p.symlink_to(name)
    write(prefix/'installed-locks.json',{'archives':records,'locks':{str(p):sha(p) for p in lock_paths}})
    print('Prepared',len(records),'hash-verified host packages under',prefix)
    print('Set PATH to prefix/usr/bin:prefix/usr/sbin, PKG_CONFIG_PATH to its pkgconfig directories,')
    print('and LD_LIBRARY_PATH/CMAKE_PREFIX_PATH to its host libraries/prefix. Never use this prefix for radio deployment.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lock',type=Path,action='append',required=True)
    p.add_argument('--cache',type=Path,required=True);p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--download',action='store_true');a=p.parse_args()
    try: install(a.lock,a.cache.resolve(),a.prefix.resolve(),a.download)
    except (ValueError,OSError) as e: print('BLOCKED:',e);return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
