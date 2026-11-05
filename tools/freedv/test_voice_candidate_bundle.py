#!/usr/bin/env python3
"""Verify candidate source delivery works without a prerequisite repository."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from build_voice_candidate import create_source_bundle, verify_source_manifest


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='voice-bundle-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = self.root / 'source'
        self.run_git('init', str(self.repository))
        for value in ('baseline', 'candidate'):
            (self.repository / 'source.c').write_text(value + '\n')
            self.run_git('-C', str(self.repository), 'add', 'source.c')
            self.run_git('-C', str(self.repository), '-c', 'user.name=Bundle Test',
                         '-c', 'user.email=bundle-test@example.invalid', 'commit', '-m', value)
        self.revision = self.run_git('-C', str(self.repository), 'rev-parse', 'HEAD').strip()

    def run_git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL)

    def test_full_bundle_restores_in_an_empty_repository(self):
        bundle = self.root / 'source.bundle'
        create_source_bundle(self.repository, bundle, self.revision)
        restored = self.root / 'restored.git'
        self.run_git('init', '--bare', str(restored))
        self.run_git('-C', str(restored), 'bundle', 'verify', str(bundle))
        self.run_git('-C', str(restored), 'fetch', str(bundle), 'HEAD:refs/heads/candidate')
        self.assertEqual(self.run_git('-C', str(restored), 'show', 'candidate:source.c'), 'candidate\n')
        self.assertEqual(self.run_git('-C', str(restored), 'show', 'candidate~1:source.c'), 'baseline\n')

    def test_changed_head_cannot_be_labeled_as_verified_revision(self):
        bundle = self.root / 'wrong.bundle'
        with self.assertRaisesRegex(ValueError, 'HEAD changed'):
            create_source_bundle(self.repository, bundle, '0' * 40)
        self.assertFalse(bundle.exists())

    def source_manifest(self):
        files = {'source.c': hashlib.sha256((self.repository / 'source.c').read_bytes()).hexdigest()}
        return files, self.tree_digest(files)

    @staticmethod
    def tree_digest(files):
        return hashlib.sha256(''.join(files[name] + '  ' + name + '\n'
                                      for name in sorted(files)).encode()).hexdigest()

    def test_complete_committed_manifest_is_accepted(self):
        files, digest = self.source_manifest()
        verify_source_manifest(self.repository, files, digest)

    def test_partial_or_extra_manifest_is_rejected(self):
        files, _ = self.source_manifest()
        for invalid in ({}, {**files, '../outside.c': '0' * 64}):
            with self.subTest(files=invalid), self.assertRaisesRegex(ValueError, 'every committed regular file'):
                verify_source_manifest(self.repository, invalid, self.tree_digest(invalid))

    def test_malformed_manifest_is_rejected(self):
        for invalid in (None, [], {1: '0' * 64}):
            with self.subTest(files=invalid), self.assertRaisesRegex(ValueError, 'file-hash mapping'):
                verify_source_manifest(self.repository, invalid, '0' * 64)
        for value in (None, 123, 'invalid', 'A' * 64):
            with self.subTest(hash=value), self.assertRaisesRegex(ValueError, 'invalid verification source hash'):
                verify_source_manifest(self.repository, {'source.c': value}, '0' * 64)

    def test_tampered_hash_or_tree_digest_is_rejected(self):
        files, digest = self.source_manifest()
        invalid = {'source.c': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'verification source mismatch'):
            verify_source_manifest(self.repository, invalid, self.tree_digest(invalid))
        with self.assertRaisesRegex(ValueError, 'source tree digest mismatch'):
            verify_source_manifest(self.repository, files, '0' * 64)

    def test_rehashed_uncommitted_source_is_rejected(self):
        (self.repository / 'source.c').write_text('uncommitted\n')
        files, digest = self.source_manifest()
        with self.assertRaisesRegex(ValueError, 'clean committed tree'):
            verify_source_manifest(self.repository, files, digest)

    def test_gitlink_is_pinned_without_becoming_a_regular_file(self):
        (self.repository / 'dependency').mkdir()
        self.run_git('-C', str(self.repository), 'update-index', '--add', '--cacheinfo',
                     '160000,' + self.revision + ',dependency')
        self.run_git('-C', str(self.repository), '-c', 'user.name=Bundle Test',
                     '-c', 'user.email=bundle-test@example.invalid', 'commit', '-m', 'pin dependency')
        files, digest = self.source_manifest()
        verify_source_manifest(self.repository, files, digest)


if __name__ == '__main__':
    unittest.main()
