import mmap
import os
from pathlib import Path
import struct
import tempfile
import unittest

import mic_ptt_r16_pio as pio


class PioTests(unittest.TestCase):
    def fixture(self, root):
        dt = root / 'dt'
        soc = dt / 'soc'
        soc.mkdir(parents=True)
        (soc / '#address-cells').write_bytes(struct.pack('>I', 1))
        (soc / '#size-cells').write_bytes(struct.pack('>I', 1))
        (soc / 'ranges').write_bytes(struct.pack('>III', 0, 0, 0x40000000))
        for name, (compatible, address) in pio.CONTROLLERS.items():
            node = soc / name
            node.mkdir()
            (node / 'compatible').write_bytes(compatible + b'\0')
            (node / 'reg').write_bytes(struct.pack('>II', address, 0x400))
        memory = root / 'fake-mem'
        with memory.open('wb') as f:
            f.truncate(0x02000000)
            for name, (_, base) in pio.CONTROLLERS.items():
                # PB data is offset 0x24+0x10, PL data is offset 0x10.
                offset = 0x34 if name == 'main' else 0x10
                f.seek(base + offset)
                f.write(struct.pack('<I', 0x81))
        return dt, memory

    def test_verified_controllers_and_readonly_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            dt, memory = self.fixture(Path(tmp))
            reader = pio.R16PioReader(dt, memory)
            try:
                first = reader.sample()
                self.assertEqual(first['B']['data_hex'], '00000081')
                self.assertEqual(first['L']['data_hex'], '00000081')
                region, _ = reader.maps['main']
                with self.assertRaises(TypeError):
                    region[0] = 1
                with memory.open('r+b') as f:
                    f.seek(0x01C20800 + 0x34)
                    f.write(struct.pack('<I', 0x80))
                self.assertEqual(reader.sample()['B']['data_hex'], '00000080')
            finally:
                reader.close()

    def test_wrong_register_address_is_rejected_before_memory_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            dt, memory = self.fixture(Path(tmp))
            (dt / 'soc/main/reg').write_bytes(struct.pack('>II', 0xDEAD0000, 0x400))
            memory.unlink()
            with self.assertRaisesRegex(ValueError, 'range differs'):
                pio.R16PioReader(dt, memory)

    def test_unreviewed_translation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dt, memory = self.fixture(Path(tmp))
            (dt / 'soc/ranges').write_bytes(struct.pack('>III', 0, 0x10000000, 0x40000000))
            with self.assertRaisesRegex(ValueError, 'identity address'):
                pio.R16PioReader(dt, memory)

    def test_missing_controller_does_not_silently_reduce_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            dt, memory = self.fixture(Path(tmp))
            (dt / 'soc/always_on/compatible').write_bytes(b'unknown\0')
            with self.assertRaisesRegex(ValueError, 'not found'):
                pio.R16PioReader(dt, memory)


if __name__ == '__main__':
    unittest.main()
