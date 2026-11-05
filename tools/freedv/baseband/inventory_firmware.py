#!/usr/bin/env python3
"""Bounded, read-only firmware identity and ZIP inventory. Never extracts or runs."""

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import zipfile
import zlib


MIB = 1024 * 1024


def entropy(data):
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    return -sum((n / len(data)) * math.log2(n / len(data)) for n in counts if n)


def member_risks(info, duplicate=False):
    name = info.filename
    risks = []
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive:
        risks.append("absolute_path")
    if ".." in PurePosixPath(name.replace("\\", "/")).parts:
        risks.append("parent_path_component")
    if "\\" in name:
        risks.append("backslash_path")
    if stat.S_ISLNK(info.external_attr >> 16):
        risks.append("symlink")
    if info.flag_bits & 1:
        risks.append("encrypted")
    if duplicate:
        risks.append("duplicate_name")
    return risks


def inventory(path, *, max_file_bytes=128 * MIB, sample_bytes=MIB,
              hash_members=False, max_member_bytes=16 * MIB,
              max_total_member_bytes=64 * MIB, max_entries=4096):
    """Hash source, sample entropy, and optionally verify bounded ZIP members."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Input must be a regular file, not a symlink or device")
    if before.st_size > max_file_bytes:
        raise ValueError(f"Input exceeds {max_file_bytes} byte limit")
    digest = hashlib.sha256()
    sample = bytearray()
    count = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(MIB), b""):
            count += len(block)
            if count > max_file_bytes:
                raise ValueError("Input grew beyond file limit while reading")
            digest.update(block)
            if len(sample) < sample_bytes:
                sample.extend(block[:sample_bytes - len(sample)])
    result = {
        "path": str(path.absolute()), "size_bytes": count,
        "sha256": digest.hexdigest(), "header_hex": bytes(sample[:32]).hex(),
        "entropy_sample_bytes": len(sample),
        "entropy_bits_per_byte": round(entropy(sample), 6),
        "interpretation": "Identity and container observations only; no CPU, encryption, or runtime inference",
        "status": "inventoried", "zip": None,
    }
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                raise ValueError(f"ZIP exceeds {max_entries} member limit")
            names = {}
            for entry in entries:
                names[entry.filename] = names.get(entry.filename, 0) + 1
            used = 0
            result["zip"] = []
            for index, entry in enumerate(entries):
                row = {
                    "index": index, "name": entry.filename,
                    "size_bytes": entry.file_size,
                    "compressed_size_bytes": entry.compress_size,
                    "compression_method": entry.compress_type,
                    "crc32_declared": f"{entry.CRC:08x}",
                    "risks": member_risks(entry, names[entry.filename] > 1),
                    "content_status": "not_read", "sha256": None,
                }
                if hash_members and not entry.is_dir():
                    if row["risks"]:
                        row["content_status"] = "skipped_risky_member"
                    elif entry.file_size > max_member_bytes:
                        row["content_status"] = "skipped_member_size_limit"
                    elif used + entry.file_size > max_total_member_bytes:
                        row["content_status"] = "skipped_total_size_limit"
                    else:
                        # Read at most declared length plus one; zipfile checks CRC at EOF.
                        used += entry.file_size
                        try:
                            with archive.open(entry) as stream:
                                content = stream.read(entry.file_size + 1)
                            if len(content) != entry.file_size:
                                raise ValueError("ZIP member length mismatch")
                            row["sha256"] = hashlib.sha256(content).hexdigest()
                            row["content_status"] = "sha256_and_crc_verified"
                        except (OSError, EOFError, RuntimeError, ValueError, zlib.error, zipfile.BadZipFile,
                                NotImplementedError) as error:
                            row["content_status"] = "read_failed"
                            row["error"] = str(error)
                            result["status"] = "invalid_zip_member"
                result["zip"].append(row)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Input changed while inventorying")
    return result


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--hash-members", action="store_true")
    parser.add_argument("--max-file-bytes", type=positive_int, default=128 * MIB)
    parser.add_argument("--max-member-bytes", type=positive_int, default=16 * MIB)
    parser.add_argument("--max-total-member-bytes", type=positive_int, default=64 * MIB)
    args = parser.parse_args()
    rows = []
    failed = False
    for path in args.paths:
        try:
            row = inventory(path, hash_members=args.hash_members,
                            max_file_bytes=args.max_file_bytes,
                            max_member_bytes=args.max_member_bytes,
                            max_total_member_bytes=args.max_total_member_bytes)
            failed |= row["status"] != "inventoried"
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            row = {"path": path, "status": "error", "error": str(error)}
            failed = True
        rows.append(row)
    print(json.dumps({"schema_version": 1, "files": rows}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
