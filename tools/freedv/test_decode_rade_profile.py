#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small synthetic format tests; no radio files or target execution required."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import decode_rade_profile as decoder


def synthetic_elf():
    data = bytearray(672)
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, ident, 3, 40, 1, 0, 52, 512,
                     0x5000400, 52, 32, 1, 40, 4, 0)
    struct.pack_into('<8I', data, 52, 1, 0, 0, 0, len(data), len(data), 5, 0x1000)
    data[0x120:0x125] = b'\0foo\0'
    struct.pack_into('<IIIBBH', data, 0x150, 1, 0x100, 16, 0x12, 0, 1)
    struct.pack_into('<10I', data, 552, 0, 1, 6, 0x100, 0x100, 16, 0, 0, 4, 0)
    struct.pack_into('<10I', data, 592, 0, 3, 0, 0, 0x120, 5, 0, 0, 1, 0)
    struct.pack_into('<10I', data, 632, 0, 2, 0, 0, 0x140, 32, 2, 1, 4, 16)
    return bytes(data)


def synthetic_capture(path='/tmp/freedv-benchmark/lib/librade.so.0.1'):
    name = path.encode() + b'\0'
    name += bytes((-len(name)) % 8)
    mmap = struct.pack('<IIQQQIIQQII', 123, 123, 0x100000, 4096, 0, 0, 0, 1, 0, 5, 2)
    mmap += name + struct.pack('<IIQ', 123, 123, 1)
    records = struct.pack('<IHH', 10, 2, len(mmap) + 8) + mmap
    records += struct.pack('<IHHQIIQQ', 9, 2, 40, 0x100104, 123, 123, 10, 10101010)
    data = bytearray(256)
    struct.pack_into('<8s12Q', data, 0, b'PERFILE2', 104, 144, 112, 144,
                     256, len(records), 0, 0, 0, 0, 0, 0)
    struct.pack_into('<Q', data, 104, 1)
    flags = sum(1 << b for b in [5, 10, 18, 23])
    struct.pack_into('<IIQQQQQ', data, 112, 1, 128, 0, 99, 0x107, 20, flags)
    struct.pack_into('<QQ', data, 240, 104, 8)
    return data + records


def rewrite_records(*records):
    header = synthetic_capture()[:256]
    payload = b''.join(records)
    struct.pack_into('<Q', header, 48, len(payload))
    return header + payload


def mmap_record(path='/tmp/freedv-benchmark/lib/librade.so.0.1', time=1):
    data = synthetic_capture(path)
    size = struct.unpack_from('<H', data, 262)[0]
    record = data[256:256 + size]
    struct.pack_into('<Q', record, len(record) - 8, time)
    return record


def sample_record(time=10):
    record = synthetic_capture()[-40:]
    struct.pack_into('<Q', record, 24, time)
    return record


def exec_record(time=15):
    payload = struct.pack('<II', 123, 123) + b'worker\0\0' + struct.pack('<IIQ', 123, 123, time)
    return struct.pack('<IHH', 3, 1 << 13, len(payload) + 8) + payload


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        for name in ['librade.so.0.1', 'rade-frame-bench']:
            (self.directory / name).write_bytes(synthetic_elf())
        (self.directory / 'package-manifest.json').write_text(json.dumps({
            'kind': 'benchmark-package', 'target': 'x6200-buildroot'}))
        hashes = []
        for local, packaged in [*decoder.RUNTIME_PATHS.values(), ('package-manifest.json','package-manifest.json')]:
            hashes.append(hashlib.sha256((self.directory / local).read_bytes()).hexdigest() + '  ' + packaged)
        (self.directory / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n')
        (self.directory / 'rade-rx-profile.data').write_bytes(synthetic_capture())

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_symbol_and_no_fixed_sample_count(self):
        result = decoder.decode(self.directory)
        self.assertEqual(result['samples'], 1)
        self.assertEqual(result['function_totals'][0]['key'], ('/tmp/freedv-benchmark/lib/librade.so.0.1', 'foo'))
        self.assertEqual(result['function_totals'][0]['percent'], 100)
        self.assertEqual(result['lost_samples'], 0)
        self.assertIsNone(result['original_binary_hotspot'])

    def test_metadata_written_after_samples_uses_event_time(self):
        (self.directory / 'rade-rx-profile.data').write_bytes(rewrite_records(sample_record(), mmap_record()))
        result = decoder.decode(self.directory)
        self.assertEqual(result['samples'], 1)
        self.assertEqual(result['function_totals'][0]['key'], ('/tmp/freedv-benchmark/lib/librade.so.0.1', 'foo'))
        self.assertTrue(result['file_record_times_out_of_order'])
        self.assertTrue(result['timestamp_ordered_mapping_resolution'])

    def test_remapping_follows_timestamp_not_file_order(self):
        (self.directory / 'rade-rx-profile.data').write_bytes(rewrite_records(
            mmap_record('/tmp/freedv-benchmark/bin/rade-frame-bench', time=20), sample_record(time=30),
            mmap_record(time=1), sample_record(time=10)))
        result = decoder.decode(self.directory)
        self.assertEqual(result['samples'], 2)
        counts = {row['key']: row['samples'] for row in result['function_totals']}
        self.assertEqual(counts, {
            ('/tmp/freedv-benchmark/lib/librade.so.0.1', 'foo'): 1,
            ('/tmp/freedv-benchmark/bin/rade-frame-bench', 'foo'): 1})

    def test_future_mapping_is_not_used(self):
        (self.directory / 'rade-rx-profile.data').write_bytes(rewrite_records(mmap_record(time=20), sample_record(time=10)))
        with self.assertRaisesRegex(decoder.DecodeError, 'no earlier matching mapping'):
            decoder.decode(self.directory)

    def test_pre_exec_mapping_is_not_reused(self):
        (self.directory / 'rade-rx-profile.data').write_bytes(rewrite_records(sample_record(time=20), mmap_record(), exec_record()))
        with self.assertRaisesRegex(decoder.DecodeError, 'no earlier matching mapping'):
            decoder.decode(self.directory)

    def test_ambiguous_timestamps_are_rejected(self):
        variants = [
            (mmap_record(time=10), sample_record(time=10)),
            (mmap_record(), mmap_record('/tmp/freedv-benchmark/bin/rade-frame-bench'), sample_record()),
            (mmap_record(), exec_record(time=10), sample_record(time=10)),
        ]
        for records in variants:
            with self.subTest(records=len(records)):
                (self.directory / 'rade-rx-profile.data').write_bytes(rewrite_records(*records))
                with self.assertRaisesRegex(decoder.DecodeError, 'ambiguous'):
                    decoder.decode(self.directory)

    def test_truncated_header_record_and_elf(self):
        capture = synthetic_capture()
        for data in [capture[:20], capture[:-1], capture[:-16]]:
            with self.subTest(size=len(data)):
                (self.directory / 'rade-rx-profile.data').write_bytes(data)
                with self.assertRaisesRegex(decoder.DecodeError, 'truncated|bounds'):
                    decoder.decode(self.directory)
        with self.assertRaisesRegex(decoder.DecodeError, 'truncated|bounds'):
            decoder.read_elf(synthetic_elf()[:-1])

    def test_unsupported_sample_layout_and_flags(self):
        capture = synthetic_capture()
        struct.pack_into('<Q', capture, 112 + 24, 0x117)
        (self.directory / 'rade-rx-profile.data').write_bytes(capture)
        with self.assertRaisesRegex(decoder.DecodeError, 'unsupported event'):
            decoder.decode(self.directory)
        capture = synthetic_capture()
        struct.pack_into('<Q', capture, 112 + 40, 1 << 4)
        (self.directory / 'rade-rx-profile.data').write_bytes(capture)
        with self.assertRaisesRegex(decoder.DecodeError, 'userspace included'):
            decoder.decode(self.directory)

    def test_checksum_mismatch_before_symbolization(self):
        (self.directory / 'librade.so.0.1').write_bytes(b'wrong binary')
        with self.assertRaisesRegex(decoder.DecodeError, 'checksum mismatch'):
            decoder.decode(self.directory)

    def test_basename_does_not_establish_identity(self):
        (self.directory / 'rade-rx-profile.data').write_bytes(synthetic_capture('/other/lib/librade.so.0.1'))
        with self.assertRaisesRegex(decoder.DecodeError, 'expected runtime binary paths'):
            decoder.decode(self.directory)

    def test_unsupported_record_fails_without_partial_output_even_under_optimization(self):
        capture = synthetic_capture()
        struct.pack_into('<I', capture, 256, 999)
        (self.directory / 'rade-rx-profile.data').write_bytes(capture)
        output = self.directory / 'output.json'
        result = subprocess.run([sys.executable, '-O', str(Path(decoder.__file__)), str(self.directory), '--output', str(output)],
                                text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsupported perf record type', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
