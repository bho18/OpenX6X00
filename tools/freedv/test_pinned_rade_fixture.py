#!/usr/bin/env python3
"""Exercise the package builder's actual frozen-waveform integrity boundary."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('package_builder', HERE / 'build_benchmark_package.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PinnedRadeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.code = Path(self.temporary.name) / 'code'
        shutil.copytree(HERE / 'fixtures', self.code / 'tools/freedv/fixtures')
        self.output = Path(self.temporary.name) / 'package/fixtures'
        self.output.mkdir(parents=True)
        self.pin = builder.PINS['rade_c']['modem_fixture']
        self.source = self.code / self.pin['path']

    def test_valid_payload_preserved_and_manifest_links_provenance(self):
        result = builder.copy_rade_modem_fixture(self.code, self.output)
        self.assertEqual((self.output / 'rade-modem.raw').read_bytes(), self.source.read_bytes())
        self.assertEqual(builder.sha(self.output / 'rade-modem.raw'), self.pin['sha256'])
        self.assertEqual(result['pin'], self.pin)
        self.assertFalse(result['generation_in_this_build'])
        self.assertEqual(result['metadata_sha256'], builder.sha(self.output / 'rade-modem.json'))
        self.assertEqual(json.loads((self.output / 'rade-modem.json').read_text())['fixture_revision'], self.pin['revision'])
        self.assertTrue((self.output / 'rade_c-LICENSE').is_file())

    def test_same_size_modified_payload_rejected_without_output(self):
        data = bytearray(self.source.read_bytes())
        data[len(data) // 2] ^= 1
        self.source.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'checksum/size mismatch'):
            builder.copy_rade_modem_fixture(self.code, self.output)
        self.assertFalse((self.output / 'rade-modem.raw').exists())

    def test_truncated_payload_rejected_without_output(self):
        self.source.write_bytes(self.source.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, 'checksum/size mismatch'):
            builder.copy_rade_modem_fixture(self.code, self.output)
        self.assertFalse((self.output / 'rade-modem.raw').exists())

    def test_metadata_revision_mismatch_rejected_without_output(self):
        metadata = self.code / self.pin['metadata_path']
        value = json.loads(metadata.read_text())
        value['fixture_revision'] = 'incorrect-fixture-revision'
        metadata.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'metadata revision mismatch'):
            builder.copy_rade_modem_fixture(self.code, self.output)
        self.assertFalse((self.output / 'rade-modem.raw').exists())


if __name__ == '__main__':
    unittest.main()
