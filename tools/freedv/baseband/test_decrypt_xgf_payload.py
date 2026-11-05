import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("decrypt_xgf_payload.py")
SPEC = importlib.util.spec_from_file_location("decrypt_xgf_payload", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
# NIST SP 800-38A, F.1.5 ECB-AES256, first block (public known-answer data).
KEY = "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4"
PLAIN = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
CIPHER = bytes.fromhex("f3eed1bdb5d2a03c064b5a7e3db181f8")


class DecryptPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.xgf"
        self.output = self.root / "payload.bin"
        self.manifest = self.root / "payload.json"
        self.source.write_bytes(CIPHER)

    def decrypt(self, **kwargs):
        values = {"key_hex": KEY, "offset": 0, "length": 16}
        values.update(kwargs)
        return MODULE.decrypt_payload(self.source, self.output, self.manifest, **values)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI required for known-answer verification")
    def test_nist_aes256_ecb_known_answer_and_provenance(self):
        report = self.decrypt()
        self.assertEqual(self.output.read_bytes(), PLAIN)
        self.assertEqual(self.source.read_bytes(), CIPHER)
        self.assertEqual(report, json.loads(self.manifest.read_text()))
        self.assertEqual(report["source"]["sha256"], hashlib.sha256(CIPHER).hexdigest())
        self.assertEqual(report["output"]["sha256"], hashlib.sha256(PLAIN).hexdigest())
        self.assertEqual(report["parameters"]["key_sha256"], hashlib.sha256(bytes.fromhex(KEY)).hexdigest())
        self.assertEqual(report["firmware_validation"], "UNVALIDATED")
        self.assertNotIn(KEY, self.manifest.read_text())

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI required")
    def test_explicit_wrapped_range_does_not_assume_header_size(self):
        wrapped = b"arbitrary XGF" + CIPHER + b"trailing bytes"
        self.source.write_bytes(wrapped)
        report = self.decrypt(offset=len(b"arbitrary XGF"))
        self.assertEqual(self.output.read_bytes(), PLAIN)
        self.assertEqual(self.source.read_bytes(), wrapped)
        self.assertEqual(report["selected_payload"]["offset"], 13)
        self.assertEqual(report["source"]["sha256"], hashlib.sha256(wrapped).hexdigest())

    def test_bad_keys_ranges_and_limits_preserve_input(self):
        bad = [{"key_hex": "00" * 16}, {"key_hex": "gg" * 32}, {"key_hex": KEY + "\n"},
               {"offset": -1}, {"length": 0}, {"length": 15}, {"length": 32},
               {"length": MODULE.MAX_PAYLOAD_BYTES + 16}]
        with patch.object(MODULE.subprocess, "run", side_effect=AssertionError("must not invoke OpenSSL")):
            for parameters in bad:
                with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                    self.decrypt(**parameters)
        self.assertEqual(self.source.read_bytes(), CIPHER)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_input_size_limit_is_checked_before_reading(self):
        with self.source.open("r+b") as stream:
            stream.truncate(MODULE.MAX_INPUT_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "128 MiB"):
            self.decrypt()
        self.assertFalse(self.output.exists())

    def test_existing_output_or_manifest_is_preserved(self):
        for target in (self.output, self.manifest):
            with self.subTest(target=target):
                target.write_bytes(b"preserve")
                with self.assertRaisesRegex(ValueError, "existing target"):
                    self.decrypt()
                self.assertEqual(target.read_bytes(), b"preserve")
                target.unlink()
        self.assertEqual(self.source.read_bytes(), CIPHER)

    def test_aliases_and_symlinks_cannot_overwrite_input(self):
        for output, manifest in ((self.source, self.manifest), (self.output, self.source),
                                 (self.output, self.output)):
            with self.subTest(output=output, manifest=manifest), self.assertRaises(ValueError):
                MODULE.decrypt_payload(self.source, output, manifest, key_hex=KEY, offset=0, length=16)
        self.output.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "existing target"):
            self.decrypt()
        self.assertEqual(self.source.read_bytes(), CIPHER)

    def test_offset_and_length_are_required_cli_arguments(self):
        command = [sys.executable, str(SCRIPT), str(self.source), str(self.output),
                   "--manifest", str(self.manifest), "--key-hex", KEY]
        for parameters in ([], ["--offset", "0"], ["--length", "16"]):
            with self.subTest(parameters=parameters):
                result = subprocess.run(command + parameters, capture_output=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"required", result.stderr)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI required")
    def test_manifest_publication_race_does_not_leave_untracked_payload(self):
        original = MODULE._write_exclusive
        def create_conflicting_manifest(path, data, owned):
            if path == self.manifest:
                path.write_bytes(b"other writer")
            original(path, data, owned)
        with patch.object(MODULE, "_write_exclusive", side_effect=create_conflicting_manifest):
            with self.assertRaises(FileExistsError):
                self.decrypt()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.manifest.read_bytes(), b"other writer")
        self.assertEqual(self.source.read_bytes(), CIPHER)


if __name__ == "__main__":
    unittest.main()
