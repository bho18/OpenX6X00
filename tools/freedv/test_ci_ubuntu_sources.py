#!/usr/bin/env python3
"""Ubuntu mirror transport changes must preserve repository trust and scope."""
from pathlib import Path
import stat
import tempfile
import unittest
from ci_ubuntu_sources import normalize_sources, rewrite_source


class UbuntuMirrorTests(unittest.TestCase):
    def test_runner_file_mirror_routes_to_https_without_changing_trust(self):
        for scheme in ('file:', 'mirror+file:'):
            for slashes in ('/', '//', '///'):
                for suffix, host in (('', 'archive'), ('-security', 'security')):
                    uri=scheme+slashes+'etc/apt/apt-mirrors'+suffix+'.txt'
                    for deb822, source in ((False,'deb [signed-by=/keys/ubuntu.gpg] '+uri+' noble main\n'),
                                           (True,'Types: deb\nURIs: '+uri+'\nSuites: noble\nSigned-By: /keys/ubuntu.gpg\n')):
                        expected=source.replace(uri,'https://'+host+'.ubuntu.com/ubuntu')
                        self.assertEqual(rewrite_source(source,deb822),(expected,1))
                        self.assertEqual(rewrite_source(expected,deb822),(expected,0))

    def test_only_active_ubuntu_http_uri_tokens_change(self):
        source=('Types: deb\nURIs: http://archive.ubuntu.com/ubuntu/ http://security.ubuntu.com/ubuntu\n'
                'Suites: noble noble-security\nSigned-By: /keys/ubuntu.gpg\n'
                '# http://archive.ubuntu.com/ubuntu\n')
        expected=source.replace('URIs: http://','URIs: https://').replace('ubuntu/ http://security','ubuntu/ https://security')
        self.assertEqual(rewrite_source(source,True),(expected,2))

    def test_legacy_suites_components_and_signature_options_are_unchanged(self):
        source='deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu.gpg] http://azure.archive.ubuntu.com/ubuntu noble main universe\r\n'
        expected=source.replace('http://azure.archive.ubuntu.com/ubuntu','https://archive.ubuntu.com/ubuntu')
        self.assertEqual(rewrite_source(source),(expected,1))
        self.assertEqual(rewrite_source(expected),(expected,0))

    def test_deb822_and_continuations_preserve_trust_fields(self):
        source=('Types: deb deb-src\nURIs: http://azure.archive.ubuntu.com/ubuntu/\n'
                ' https://security.ubuntu.com/ubuntu http://azure.archive.ubuntu.com/ubuntu\n'
                'Suites: resolute resolute-updates resolute-security\nComponents: main universe restricted multiverse\n'
                'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n'
                '# keep http://azure.archive.ubuntu.com/ubuntu in this comment\n')
        expected=source.replace('URIs: http://azure.archive.ubuntu.com/ubuntu/','URIs: https://archive.ubuntu.com/ubuntu/')
        expected=expected.replace('ubuntu http://azure.archive.ubuntu.com/ubuntu\n','ubuntu https://archive.ubuntu.com/ubuntu\n')
        self.assertEqual(rewrite_source(source,True),(expected,2))

    def test_other_repositories_disabled_entries_and_inline_keys_are_untouched(self):
        legacy=('# deb http://azure.archive.ubuntu.com/ubuntu noble main\n'
                'deb https://packages.microsoft.com/ubuntu noble main # http://azure.archive.ubuntu.com/ubuntu\n'
                'deb https://azure.archive.ubuntu.com.example/ubuntu noble main\n')
        self.assertEqual(rewrite_source(legacy),(legacy,0))
        key=('Types: deb\nURIs: https://other.example/ubuntu\nSigned-By:\n'
             ' -----BEGIN PGP PUBLIC KEY BLOCK-----\n http://azure.archive.ubuntu.com/ubuntu\n -----END PGP PUBLIC KEY BLOCK-----\n')
        self.assertEqual(rewrite_source(key,True),(key,0))

    def test_only_source_files_are_rewritten_and_mode_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'sources.list.d';directory.mkdir()
            path=root/'sources.list';source=b'deb http://azure.archive.ubuntu.com/ubuntu noble main\n'
            path.write_bytes(source);path.chmod(0o640)
            unrelated=directory/'not-source.txt';unrelated.write_bytes(source)
            key=root/'trusted.gpg';key.write_bytes(source)
            report=normalize_sources(root)
            self.assertEqual(report['source_files_changed'],1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o640)
            self.assertEqual(unrelated.read_bytes(),source)
            self.assertEqual(key.read_bytes(),source)
            self.assertNotIn(b'azure.archive',path.read_bytes())

    def test_check_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'sources.list'
            source=b'deb-src http://azure.archive.ubuntu.com/ubuntu noble main\n';path.write_bytes(source)
            self.assertEqual(normalize_sources(root,True)['source_files_changed'],1)
            self.assertEqual(path.read_bytes(),source)

    def test_linked_source_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'sources.list.d';directory.mkdir()
            outside=root/'outside';source=b'deb http://azure.archive.ubuntu.com/ubuntu noble main\n';outside.write_bytes(source)
            (directory/'ubuntu.list').symlink_to(outside)
            with self.assertRaises(ValueError):normalize_sources(root)
            self.assertEqual(outside.read_bytes(),source)


if __name__ == '__main__':
    unittest.main()
