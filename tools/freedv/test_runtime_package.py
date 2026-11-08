#!/usr/bin/env python3
"""Runtime guards reject unreviewed platform changes and incomplete/tampered overlays."""
import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import runtime_package as runtime
from native_image import sha


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in {'buildroot/local-build.sh': 'baseline\n',
                            'rootfs/usr/share/x6100/image-required': 'x6200 2\n',
                            'webserver/pyproject.toml': '[project]\ndependencies=[]\n'}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Runtime Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline')
        baseline = self.git('rev-parse', 'HEAD')
        (self.root / 'buildroot/local-build.sh').write_text('reviewed application tooling\n')
        path = self.root / 'tools/freedv/runtime-compatibility.json'
        path.parent.mkdir(parents=True)
        self.review = {'schema': 'x6200.runtime.compatibility.v1', 'baseline_source': baseline,
                       'x6200_image_level': '2',
                       'web_project_sha256': sha(self.root / 'webserver/pyproject.toml'),
                       'reviewed_buildroot_changes': {'buildroot/local-build.sh': sha(self.root / 'buildroot/local-build.sh')}}
        path.write_text(json.dumps(self.review))

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, stderr=subprocess.DEVNULL).strip()

    def test_audited_runtime_change_keeps_platform_checks(self):
        result = runtime.compatibility(self.root)
        self.assertEqual(result['x6200_image_level'], '2')
        self.assertEqual(result['record_sha256'], sha(self.root / 'tools/freedv/runtime-compatibility.json'))

    def test_new_or_modified_platform_inputs_are_blocked(self):
        for mutation in ('reviewed-file', 'new-kernel-file', 'image-level', 'python-dependency'):
            with self.subTest(mutation=mutation):
                paths = {
                    'reviewed-file': self.root / 'buildroot/local-build.sh',
                    'new-kernel-file': self.root / 'buildroot/new-kernel.patch',
                    'image-level': self.root / 'rootfs/usr/share/x6100/image-required',
                    'python-dependency': self.root / 'webserver/pyproject.toml'}
                path = paths[mutation]
                before = path.read_bytes() if path.exists() else None
                path.write_text('x6200 3\n' if mutation == 'image-level' else 'unreviewed input\n')
                try:
                    with self.assertRaises(ValueError):
                        runtime.compatibility(self.root)
                finally:
                    if before is None:
                        path.unlink()
                    else:
                        path.write_bytes(before)


class RuntimePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        overlay = self.package / 'overlay'
        self.programs = ['/usr/bin/xara', '/usr/bin/x6x00-mercury', '/usr/bin/ardopcf',
                         '/usr/sbin/btcat', '/usr/sbin/x6100-bootanim']
        self.files = [*self.programs, '/usr/bin/pat', '/usr/lib/libft8.so', '/usr/share/x6100/params.default.db',
                      '/usr/share/x6100/licenses/ft8/LICENSE', '/usr/share/x6100/licenses/mercury/LICENSE',
                      '/usr/share/x6100/licenses/mercury/LICENSE-freedv', '/usr/share/x6100/licenses/xara/LICENSE',
                      '/usr/share/x6100/licenses/xara/THIRD-PARTY',
                      '/usr/share/x6100/licenses/xara/rust-std-COPYRIGHT.html.gz',
                      '/usr/share/x6100/licenses/pat/LICENSE', '/usr/share/x6100/licenses/pat/GO-LICENSE',
                      '/usr/share/x6100/licenses/pat/MODULES']
        elf = bytearray(52)
        elf[:6] = b'\x7fELF\x01\x01'
        struct.pack_into('<H', elf, 18, 40)
        struct.pack_into('<I', elf, 36, 0x400)
        for name in self.files:
            path = overlay / name.lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(elf if name in self.programs or name.endswith('libft8.so') else b'synthetic fixture')
            path.chmod(0o755 if name in self.programs else 0o644)
        static = bytearray(84)
        static[:6] = b'\x7fELF\x01\x01'
        struct.pack_into('<HH', static, 16, 2, 40)
        struct.pack_into('<I', static, 28, 52)
        struct.pack_into('<HH', static, 42, 32, 1)
        struct.pack_into('<I', static, 52, 1)
        (overlay / 'usr/bin/pat').write_bytes(static)
        self.record = {'schema': runtime.PACKAGE_SCHEMA, 'status': 'PASS', 'source_commit': 'a' * 40,
                       'compatibility_sha256': 'b' * 64, 'ft8_commit': 'c' * 40,
                       'elf_programs': self.programs, 'static_programs': ['/usr/bin/pat'],
                       'elf_libraries': ['/usr/lib/libft8.so'],
                       'files': {'/' + p.relative_to(overlay).as_posix(): {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777}
                                 for p in overlay.rglob('*') if p.is_file()}}
        (self.package / 'runtime-inputs.json').write_text('synthetic receipt')
        self.record['runtime_inputs_sha256'] = sha(self.package / 'runtime-inputs.json')
        self.save()

    def save(self):
        (self.package / 'runtime-manifest.json').write_text(json.dumps(self.record))

    def validate(self):
        with patch.object(runtime, 'git', return_value='a' * 40), \
             patch.object(runtime, 'compatibility', return_value={'record_sha256': 'b' * 64, 'ft8_commit': 'c' * 40}), \
             patch.object(runtime, 'validate_inputs', return_value={
                 'ft8': {'library': {'sha256': self.record['files']['/usr/lib/libft8.so']['sha256']}},
                 'xara': {'binary': {'sha256': self.record['files']['/usr/bin/xara']['sha256']}},
                 'pat': {'binary': {'sha256': self.record['files']['/usr/bin/pat']['sha256']}}}):
            return runtime.validate_package(self.package, self.root)

    def test_complete_package_is_accepted_without_claiming_live_hardware(self):
        self.assertEqual(self.validate()['elf_libraries'], ['/usr/lib/libft8.so'])
        self.assertNotIn('hardware_qualified', self.record)

    def test_bytes_modes_and_source_identity_are_bound(self):
        path = self.package / 'overlay/usr/bin/xara'
        original = path.read_bytes()
        path.write_bytes(original + b'changed')
        with self.assertRaises(ValueError):
            self.validate()
        path.write_bytes(original)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.validate()
        path.chmod(0o755)
        self.record['source_commit'] = 'd' * 40
        self.save()
        with self.assertRaises(ValueError):
            self.validate()

    def test_every_helper_needs_loader_coverage_and_only_ft8_can_override_sdk(self):
        self.record['elf_programs'] = self.programs[:-1]
        self.save()
        with self.assertRaises(ValueError):
            self.validate()
        self.record['elf_programs'] = self.programs
        self.record['elf_libraries'] += ['/usr/lib/libc.so.6']
        self.save()
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_notices_are_rejected_even_with_updated_manifest(self):
        name = '/usr/share/x6100/licenses/xara/THIRD-PARTY'
        (self.package / 'overlay' / name.lstrip('/')).unlink()
        del self.record['files'][name]
        self.save()
        with self.assertRaises(ValueError):
            self.validate()


class ShutdownHookTests(unittest.TestCase):
    def invoke(self, gone):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / 'bin'
            commands.mkdir()
            log = root / 'commands.log'
            mocks = {
                'start-stop-daemon': 'exit 0',
                'killall': 'printf "killall %s\\n" "$*" >> "$HOOK_LOG"',
                'pidof': 'test "$GUI_GONE" != 1',
                'sleep': 'exit 0',
                'gpioset': 'echo key-up >> "$HOOK_LOG"',
                'logger': 'cat > /dev/null',
                'wr12': 'echo wr12-bypass >> "$HOOK_LOG"',
            }
            for name, body in mocks.items():
                path = commands / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            # Only relocate the helper executable into this isolated command
            # namespace; execute the real stop/wait/cleanup ordering unchanged.
            script = root / 'S95gui'
            source = (runtime.ROOT / 'rootfs/etc/init.d/S95gui').read_text()
            source = source.replace('/usr/sbin/x6100-wr12-bypass', str(commands / 'wr12'))
            source = source.replace('PIDFILE="/var/run/$DAEMON.pid"', 'PIDFILE="' + str(root / 'daemon.pid') + '"')
            source = source.replace('"/etc/default/$DAEMON"', '"' + str(root / 'daemon.default') + '"')
            script.write_text(source)
            environment = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'],
                               HOOK_LOG=str(log), GUI_GONE='1' if gone else '0')
            result = subprocess.run(['/bin/sh', str(script), 'stop'], env=environment,
                                    text=True, capture_output=True, timeout=5)
            return result, log.read_text() if log.exists() else ''

    def test_completed_shutdown_releases_key_and_restores_wr12_after_gui_exit(self):
        result, log = self.invoke(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('key-up\nwr12-bypass', log)
        self.assertLess(log.index('killall -q -TERM x6100_gui'), log.index('key-up'))

    def test_timed_out_shutdown_refuses_restart_without_fighting_live_gui(self):
        result, log = self.invoke(False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('bounded shutdown; restart refused', result.stdout)
        self.assertNotIn('key-up', log)
        self.assertNotIn('wr12-bypass', log)


if __name__ == '__main__':
    unittest.main()
