#!/usr/bin/env python3
"""Reject incorrect parallel-acquisition inputs before SDK packaging."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from build_benchmark_package import apply_rade_acquisition_parallel, PINS


def digest(value):
    return hashlib.sha256(value).hexdigest()


class ParallelPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.code, self.source = root / 'code', root / 'rade'
        patches = self.code / 'tools/freedv/patches'
        patches.mkdir(parents=True)
        (self.source / 'src').mkdir(parents=True)
        self.original = b'int receiver = 0;\n'
        self.changed = b'int receiver = 1;\n'
        self.full = b'int full_search = 0;\n'
        (self.source / 'src/rade_rx.c').write_bytes(self.original)
        (self.source / 'src/rade_acq.c').write_bytes(self.full)
        patch = b'--- a/src/rade_rx.c\n+++ b/src/rade_rx.c\n@@ -1 +1 @@\n-int receiver = 0;\n+int receiver = 1;\n'
        (patches / 'rade-acq-parallel.patch').write_bytes(patch)
        (self.code / 'tools/freedv/validate_rade_parallel_sdk.py').write_text('# test fixture\n')
        (self.code / 'tools/freedv/acquisition_evidence.py').write_text('# test fixture\n')
        self.pin = {'schema_version': 1, 'revision': 'guard-test',
                    'base_rade_commit': PINS['rade_c']['commit'], 'default_enabled': False,
                    'requires_guarded_neon_patch': True, 'target_definition': 'RADE_ACQ_PARALLEL=1',
                    'patch_sha256': digest(patch), 'new_files': [],
                    'baseline_files': {'src/rade_rx.c': digest(self.original), 'src/rade_acq.c': digest(self.full)},
                    'candidate_files': {'src/rade_rx.c': digest(self.changed)},
                    'unchanged_guarded_acquisition_sha256': digest(self.full)}
        self.pin_path = patches / 'rade-acq-parallel.json'
        self.save_pin()

    def save_pin(self):
        self.pin_path.write_text(json.dumps(self.pin))

    def test_exact_patch_application_keeps_full_search(self):
        result = apply_rade_acquisition_parallel(self.code, self.source)
        self.assertEqual((self.source / 'src/rade_rx.c').read_bytes(), self.changed)
        self.assertEqual((self.source / 'src/rade_acq.c').read_bytes(), self.full)
        self.assertEqual(result['target_wide_definition'], 'RADE_ACQ_PARALLEL=1')
        self.assertFalse(result['production_qualified'])

    def test_wrong_baseline_rejected_before_apply(self):
        (self.source / 'src/rade_rx.c').write_text('different source\n')
        with mock.patch('build_benchmark_package.run') as run:
            with self.assertRaisesRegex(ValueError, 'baseline mismatch'):
                apply_rade_acquisition_parallel(self.code, self.source)
            run.assert_not_called()

    def test_patch_hash_rejected_before_apply(self):
        self.pin['patch_sha256'] = '0' * 64; self.save_pin()
        with mock.patch('build_benchmark_package.run') as run:
            with self.assertRaisesRegex(ValueError, 'pin mismatch'):
                apply_rade_acquisition_parallel(self.code, self.source)
            run.assert_not_called()

    def test_target_layout_pin_rejected(self):
        self.pin['target_definition'] = 'RADE_ACQ_PARALLEL=0'; self.save_pin()
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            apply_rade_acquisition_parallel(self.code, self.source)

    def test_post_patch_hash_rejected(self):
        self.pin['candidate_files']['src/rade_rx.c'] = '0' * 64; self.save_pin()
        with self.assertRaisesRegex(ValueError, 'patched source mismatch'):
            apply_rade_acquisition_parallel(self.code, self.source)

    def test_unexpected_new_file_rejected(self):
        self.pin['new_files'] = ['src/rade_rx.c']; self.save_pin()
        with self.assertRaisesRegex(ValueError, 'already contains'):
            apply_rade_acquisition_parallel(self.code, self.source)

    def test_host_parallel_flag_rejected_without_build(self):
        work = Path(self.temp.name) / 'must-not-build'
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('build_benchmark_package.py')),
                                 '--work', str(work), '--host-validation', '--rade-acq-parallel'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--rade-acq-parallel requires --sdk, --rade-acq-neon and --rade-pipeline', result.stderr)
        self.assertFalse(work.exists())


if __name__ == '__main__':
    unittest.main()
