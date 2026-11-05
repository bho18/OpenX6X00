#!/usr/bin/env python3
"""Apply a caller-selected AES-256-ECB transform; never infer XGF packaging."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _output_path(path):
    path = Path(path).absolute()
    if os.path.lexists(path):
        raise ValueError(f"Refusing existing target: {path}")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Target parent must be an existing directory")
    return parent / path.name


def _source_payload(path, offset, length):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Source must be a regular file, not a symlink or device")
    if before.st_size > MAX_INPUT_BYTES:
        raise ValueError("Source exceeds the 128 MiB input limit")
    if offset + length > before.st_size:
        raise ValueError("Selected payload range extends beyond the source")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(path, flags)
    digest = hashlib.sha256()
    payload = bytearray()
    count = 0
    with os.fdopen(source_fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Source changed before reading")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            start = count
            count += len(block)
            if count > MAX_INPUT_BYTES:
                raise ValueError("Source grew beyond the input limit")
            digest.update(block)
            low, high = max(start, offset), min(count, offset + length)
            if low < high:
                payload.extend(block[low - start:high - start])
        after = os.fstat(stream.fileno())
    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Source changed while reading")
    if count != before.st_size or len(payload) != length:
        raise ValueError("Source or selected payload size changed while reading")
    return bytes(payload), count, digest.hexdigest()


def _write_exclusive(path, data, owned):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    identity = os.fstat(fd)
    owned.append((path, identity.st_dev, identity.st_ino))
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _remove_owned(owned):
    for path, device, inode in reversed(owned):
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (device, inode):
                path.unlink()
        except FileNotFoundError:
            pass


def decrypt_payload(source, output, manifest, *, key_hex, offset, length,
                    openssl_command="openssl"):
    if not isinstance(key_hex, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", key_hex):
        raise ValueError("Key must contain exactly 64 hexadecimal characters (256 bits)")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("Offset must be an explicit nonnegative byte offset")
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0 or length % 16:
        raise ValueError("Length must be positive and aligned to the AES 16-byte block size")
    if length > MAX_PAYLOAD_BYTES:
        raise ValueError("Payload exceeds the 16 MiB limit")
    source = Path(source).absolute()
    if source.is_symlink():
        raise ValueError("Source must not be a symlink")
    source = source.resolve(strict=True)
    output, manifest = _output_path(output), _output_path(manifest)
    if len({source, output, manifest}) != 3:
        raise ValueError("Source, output and manifest paths must be distinct")
    payload, source_size, source_hash = _source_payload(source, offset, length)
    resolved = shutil.which(openssl_command)
    if not resolved:
        raise ValueError("OpenSSL executable is unavailable")
    executable = str(Path(resolved).resolve(strict=True))
    try:
        version = subprocess.run([executable, "version"], capture_output=True,
                                 check=False, timeout=10)
        if version.returncode or not version.stdout.startswith(b"OpenSSL "):
            raise ValueError("Selected executable did not report an OpenSSL version")
        command = [executable, "enc", "-d", "-aes-256-ecb", "-K", key_hex,
                   "-nosalt", "-nopad"]
        result = subprocess.run(command, input=payload, capture_output=True,
                                check=False, timeout=30)
    except subprocess.TimeoutExpired:
        # TimeoutExpired.cmd contains the candidate key; never print it.
        raise ValueError("OpenSSL operation exceeded its time limit") from None
    if result.returncode:
        raise ValueError(f"OpenSSL decryption failed with exit code {result.returncode}")
    if len(result.stdout) != length:
        raise ValueError("OpenSSL output length differs from the selected no-padding payload")
    key_hash = _sha256(bytes.fromhex(key_hex))
    report = {
        "schema_version": 1,
        "operation": "caller_selected_payload_decryption",
        "firmware_validation": "UNVALIDATED",
        "source": {"path": str(source), "size_bytes": source_size, "sha256": source_hash},
        "selected_payload": {"offset": offset, "length": length, "sha256": _sha256(payload)},
        "output": {"path": str(output), "size_bytes": length, "sha256": _sha256(result.stdout)},
        "parameters": {"cipher": "AES-256", "mode": "ECB", "iv": None,
                       "iv_applicability": "ECB does not use an IV", "padding": "none",
                       "salt": "none", "key_sha256": key_hash,
                       "basis": "Caller-supplied parameters and byte range; not inferred from X6200 packaging"},
        "tool": {"executable": executable,
                 "version": version.stdout.decode("utf-8", errors="replace").strip(),
                 "arguments": ["enc", "-d", "-aes-256-ecb", "-K", "<caller-key-redacted>", "-nosalt", "-nopad"]},
        "limitations": ["Successful decryption does not authenticate the key, payload range or firmware",
                        "No XGF header, checksum, CPU, vector table or runtime behavior has been validated",
                        "No firmware was executed or installed"],
    }
    owned = []
    try:
        _write_exclusive(output, result.stdout, owned)
        _write_exclusive(manifest, (json.dumps(report, indent=2) + "\n").encode(), owned)
    except BaseException:
        _remove_owned(owned)
        raise
    return report


def _integer(value):
    try:
        return int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError("Use a decimal integer or 0x-prefixed hexadecimal value") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--key-hex", required=True, help="Exact 64-character caller-supplied key; no default")
    parser.add_argument("--offset", required=True, type=_integer)
    parser.add_argument("--length", required=True, type=_integer)
    parser.add_argument("--openssl", default="openssl", help="OpenSSL executable name or absolute path")
    args = parser.parse_args(argv)
    try:
        report = decrypt_payload(args.source, args.output, args.manifest,
                                 key_hex=args.key_hex, offset=args.offset,
                                 length=args.length, openssl_command=args.openssl)
    except (OSError, ValueError) as error:
        print(f"Decryption refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"output": report["output"], "manifest": str(Path(args.manifest).absolute()),
                      "firmware_validation": report["firmware_validation"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
