import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import mic_status_snapshot as snap
import mic_ptt_input_watch as watch


def packet(tx=False, hkey=0):
    result = bytearray(snap.PACK_SIZE)
    struct.pack_into('<I', result, 0, 0xAA5555AA)
    struct.pack_into('<I', result, 516, 2 if tx else 0)
    struct.pack_into('<I', result, 544, hkey)
    return bytes(result)


class StatusTests(unittest.TestCase):
    def test_preserves_unknown_bits_and_bytes(self):
        raw = bytearray(packet(True, 0x84))
        struct.pack_into('<I', raw, 516, 0xFEDCBA9A)
        raw[538] = 193
        result = snap.decode_packet(raw)
        self.assertTrue(result['aggregate_tx'])
        self.assertEqual(result['hkey_u32'], 0x84)
        self.assertEqual(result['flags_u32'], 0xFEDCBA9A)
        self.assertEqual(bytes.fromhex(result['raw_packet_hex']), raw)
        self.assertFalse(result['wire_crc_verified'])
        with self.assertRaises(ValueError):
            snap.decode_packet(bytes(552))

    def fake_proc(self, root):
        exe = root / 'x6100_gui'
        exe.write_bytes(b'synthetic pinned executable')
        process = root / 'proc' / '123'
        process.mkdir(parents=True)
        (process / 'exe').symlink_to(exe)
        # State is field 3, starttime is field 22. Include ')' in comm.
        (process / 'stat').write_text('123 (fake ) gui) ' + ' '.join(['S'] + ['0'] * 18 + ['5678']))
        base = 0x10000
        address = 0x50000
        pointer_address = base + snap.PACK_POINTER_RVA
        (process / 'maps').write_text(
            f'{base:x}-{base+4096:x} r-xp 00000000 00:00 1 {exe}\n'
            f'{pointer_address & ~4095:x}-{(pointer_address & ~4095)+4096:x} rw-p 00000000 00:00 0\n'
            f'{address:x}-{address+4096:x} rw-p 00000000 00:00 0 [heap]\n')
        with (process / 'mem').open('wb') as f:
            f.seek(pointer_address)
            f.write(struct.pack('<I', address))
            f.seek(address)
            f.write(packet(True, 0x84))
        return exe, process, address, pointer_address

    def test_relocation_readonly_memory_and_restart_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe, process, address, pointer_address = self.fake_proc(root)
            digest = hashlib.sha256(exe.read_bytes()).hexdigest()
            reader = snap.open_verified_gui(root / 'proc', exe, digest)
            try:
                result = reader.sample()
                self.assertEqual(result['hkey_u32'], 0x84)
                self.assertTrue(result['repeat_read_equal'])
                # Descriptor is opened read-only, including for fake proc fixtures.
                with self.assertRaises(OSError):
                    os.pwrite(reader.fd, b'bad', address)
                (process / 'stat').write_text((process / 'stat').read_text().replace('5678', '5679'))
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    reader.sample()
            finally:
                reader.close()
            with self.assertRaisesRegex(ValueError, 'differs from pinned'):
                snap.open_verified_gui(root / 'proc', exe, '0' * 64)

    def test_out_of_mapping_pointer_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe, process, _, pointer_address = self.fake_proc(Path(tmp))
            with (process / 'mem').open('r+b') as f:
                f.seek(pointer_address)
                f.write(struct.pack('<I', 0xDEAD0000))
            with self.assertRaisesRegex(ValueError, 'outside readable mappings'):
                snap.PacketReader(process, exe)

    def test_status_samples_and_gpio_share_event_timeline(self):
        class Reader:
            def sample(self):
                return dict(snap.decode_packet(packet(True, 0x84)), repeat_read_equal=True)
        read_fd, write_fd = os.pipe()
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.object(snap, 'existing_gpio_values', return_value={}):
                out = Path(tmp)
                result = watch.observe([('synthetic', read_fd)], out, seconds=.03, packet_reader=Reader())
                rows = [json.loads(s) for s in (out / 'status-samples.jsonl').read_text().splitlines()]
                self.assertGreaterEqual(len(rows), 1)
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertTrue(rows[0]['aggregate_tx'])
                self.assertEqual(rows[0]['hkey_u32'], 0x84)
                self.assertEqual(result['release_while_software_tx_held'], 'NOT_TESTED')
        finally:
            os.close(read_fd)
            os.close(write_fd)


if __name__ == '__main__':
    unittest.main()
