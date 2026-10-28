#!/usr/bin/env python3
"""Exercise refusal paths before the delivery workflow can upload assets."""
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile
import upload_native_release as delivery


class DeliveryGuards(unittest.TestCase):
    def test_zip_paths_duplicates_links_and_size_are_refused(self):
        cases = [('traversal', '../outside', False), ('absolute', '/outside', False),
                 ('windows', 'C:\\outside', False), ('link', 'link', True), ('duplicate', 'file', False)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case, name, link in cases:
                archive = root / (case + '.zip')
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(archive, 'w') as output:
                        item = zipfile.ZipInfo(name)
                        item.external_attr = ((stat.S_IFLNK if link else stat.S_IFREG) | 0o600) << 16
                        output.writestr(item, b'payload')
                        if case == 'duplicate':
                            output.writestr(item, b'payload')
                with self.assertRaises(ValueError, msg=case):
                    delivery.extract_zip(archive, root / (case + '-extracted'))
            archive = root / 'large.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('file', b'1234')
            with self.assertRaises(ValueError):
                delivery.extract_zip(archive, root / 'large-extracted', max_bytes=3)
            self.assertFalse((root.parent / 'outside').exists())

    def test_checksum_failure_and_unlisted_file_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'file').write_bytes(b'good')
            (root / 'SHA256SUMS').write_text('0' * 64 + '  file\n')
            with self.assertRaises(ValueError):
                delivery.verify_checksums(root)
            (root / 'SHA256SUMS').write_text(hashlib.sha256(b'good').hexdigest() + '  file\n')
            delivery.verify_checksums(root)
            (root / 'unexpected').write_text('not listed')
            with self.assertRaises(ValueError):
                delivery.verify_checksums(root)

    def test_wrong_artifact_source_refused_before_download(self):
        identity = delivery.ARTIFACTS[0]
        metadata = {'id': identity[1], 'name': identity[0], 'expired': False,
                    'digest': 'sha256:' + identity[2], 'workflow_run': {'id': identity[3], 'head_sha': 'wrong'}}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(delivery, 'api', return_value=metadata), patch.object(delivery.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    delivery.fetch_artifact(identity, Path(temporary))
                run.assert_not_called()

    def test_release_and_existing_asset_guards(self):
        correct = {'id': delivery.RELEASE, 'tag_name': delivery.TAG, 'target_commitish': delivery.SOURCE,
                   'draft': True, 'prerelease': True}
        delivery.release_guard(correct)
        for key, value in [('draft', False), ('target_commitish', 'main'), ('tag_name', 'other'), ('prerelease', False)]:
            with self.assertRaises(ValueError, msg=key):
                delivery.release_guard({**correct, key: value})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'file'
            path.write_bytes(b'asset')
            asset = {'name': path.name, 'state': 'uploaded', 'size': path.stat().st_size,
                     'digest': 'sha256:' + delivery.sha(path)}
            self.assertEqual(delivery.existing_assets_match([asset], [path]), {path.name})
            for changed in ({**asset, 'digest': 'sha256:' + '0' * 64}, {**asset, 'name': 'unexpected'},
                            {**asset, 'size': 0}, {**asset, 'state': 'starter'}):
                with self.assertRaises(ValueError):
                    delivery.existing_assets_match([changed], [path])


if __name__ == '__main__':
    unittest.main()
