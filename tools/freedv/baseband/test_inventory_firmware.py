import hashlib
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "inventory", Path(__file__).parent / "inventory_firmware.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_unknown_binary_is_not_mislabeled_encrypted_or_arm(self):
        path = self.root / "stock.xgf"
        data = bytes(range(256)) * 2
        path.write_bytes(data)
        row = MODULE.inventory(path)
        self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(row["entropy_bits_per_byte"], 8.0)
        self.assertEqual(path.read_bytes(), data)
        self.assertIsNone(row["zip"])

    def test_empty_file_and_size_limit(self):
        path = self.root / "empty"
        path.touch()
        self.assertEqual(MODULE.inventory(path)["entropy_bits_per_byte"], 0)
        path.write_bytes(b"abc")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            MODULE.inventory(path, max_file_bytes=2)

    def test_zip_member_integrity_and_no_extraction(self):
        path = self.root / "stock.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/firmware.xgf", b"firmware")
        row = MODULE.inventory(path, hash_members=True)["zip"][0]
        self.assertEqual(row["content_status"], "sha256_and_crc_verified")
        self.assertEqual(row["sha256"], hashlib.sha256(b"firmware").hexdigest())
        self.assertFalse((self.root / "nested").exists())

    def test_hostile_archive_paths_and_symlinks_are_not_read(self):
        path = self.root / "hostile.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name in ("../outside", "/absolute", "C:/drive", "a\\..\\outside"):
                archive.writestr(name, b"payload")
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, b"/etc/passwd")
        rows = MODULE.inventory(path, hash_members=True)["zip"]
        self.assertTrue(all(r["content_status"] == "skipped_risky_member" for r in rows))
        self.assertIn("symlink", rows[-1]["risks"])

    def test_large_compressed_and_total_budget_are_not_decompressed(self):
        path = self.root / "large.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("large", b"a" * 100000)
            archive.writestr("one", b"1234")
            archive.writestr("two", b"5678")
        rows = MODULE.inventory(path, hash_members=True,
                                max_member_bytes=10, max_total_member_bytes=4)["zip"]
        self.assertEqual([r["content_status"] for r in rows], [
            "skipped_member_size_limit", "sha256_and_crc_verified", "skipped_total_size_limit"])

    def test_duplicate_names_cannot_silently_select_last_member(self):
        path = self.root / "duplicates.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("firmware", b"one")
                archive.writestr("firmware", b"two")
        rows = MODULE.inventory(path, hash_members=True)["zip"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("duplicate_name" in row["risks"] for row in rows))

    def test_input_symlinks_are_rejected(self):
        actual = self.root / "actual"
        actual.write_bytes(b"content")
        link = self.root / "link"
        link.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, "regular file"):
            MODULE.inventory(link)

    def test_corrupt_zip_member_is_an_error(self):
        path = self.root / "corrupt.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("firmware", b"UNIQUE_PAYLOAD")
        data = path.read_bytes().replace(b"UNIQUE_PAYLOAD", b"BROKEN_PAYLOAD")
        path.write_bytes(data)
        row = MODULE.inventory(path, hash_members=True)
        self.assertEqual(row["status"], "invalid_zip_member")
        self.assertEqual(row["zip"][0]["content_status"], "read_failed")

    def test_invalid_deflate_stream_is_reported_not_a_traceback(self):
        path = self.root / "bad-deflate.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("firmware", b"repeated data" * 100)
        data = bytearray(path.read_bytes())
        data[30 + len("firmware")] = 7  # Invalid DEFLATE block type 3.
        path.write_bytes(data)
        row = MODULE.inventory(path, hash_members=True)
        self.assertEqual(row["status"], "invalid_zip_member")
        self.assertEqual(row["zip"][0]["content_status"], "read_failed")


if __name__ == "__main__":
    unittest.main()
