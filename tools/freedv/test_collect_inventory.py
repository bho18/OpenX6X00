#!/usr/bin/env python3
"""Exercise inventory packaging with and without Buildroot's optional gzip module."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


class InventoryPackagingTest(unittest.TestCase):
    def collect(self, without_gzip):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / 'collect_inventory.py'
            shutil.copyfile(Path(__file__).with_name('collect_inventory.py'), helper)
            (root / 'inventory.py').write_text(
                "import pathlib, sys\n"
                "pathlib.Path(sys.argv[2]).write_text('{\"fixture\": true}\\n')\n")
            output = root / 'result'
            bootstrap = '''
import builtins, runpy, sys
original_import = builtins.__import__
def without_gzip(name, *args, **kwargs):
    if name in ('gzip', 'zlib'):
        raise ImportError('simulated Buildroot without compression support')
    return original_import(name, *args, **kwargs)
if sys.argv.pop(1) == 'missing':
    builtins.__import__ = without_gzip
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
            command = [sys.executable, '-c', bootstrap,
                       'missing' if without_gzip else 'available', str(helper),
                       '--output', str(output)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            suffix = '.tar' if without_gzip else '.tar.gz'
            archive = output.with_name(output.name + suffix)
            self.assertTrue(archive.is_file(), result.stdout)
            self.assertIn(str(archive), result.stdout)
            with tarfile.open(archive) as bundle:
                for name in ('inventory.json', 'manifest.json', 'SHA256SUMS', 'OPERATOR.md'):
                    self.assertEqual(bundle.extractfile('result/' + name).read(),
                                     (output / name).read_bytes())
            self.assertFalse(json.loads((output / 'manifest.json').read_text())['radio_settings_changed'])
            for line in (output / 'SHA256SUMS').read_text().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            previous = archive.read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn('already exists', repeated.stderr)
            self.assertEqual(archive.read_bytes(), previous)
            # Retaining an archive alone must also prevent accidental replacement.
            shutil.rmtree(output)
            repeated = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertFalse(output.exists())
            self.assertEqual(archive.read_bytes(), previous)

    def test_gzip_available(self):
        self.collect(without_gzip=False)

    def test_gzip_unavailable(self):
        self.collect(without_gzip=True)


if __name__ == '__main__':
    unittest.main()
