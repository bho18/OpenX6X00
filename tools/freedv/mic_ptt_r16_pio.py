#!/usr/bin/env python3
"""Read R16/A33 PIO configuration/data only; never claim or configure a pin."""
import mmap
import os
from pathlib import Path
import struct

CONTROLLERS = {
    'main': (b'allwinner,sun8i-a33-pinctrl', 0x01C20800),
    'always_on': (b'allwinner,sun8i-a23-r-pinctrl', 0x01F02C00),
}
# Bonded pins from Linux v6.1 pinctrl-sun8i-a33.c and pinctrl-sun8i-a23-r.c.
PINS = {'B': list(range(8)), 'C': list(range(17)),
        'D': [2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27],
        'E': list(range(18)), 'F': list(range(6)), 'G': list(range(14)),
        'H': list(range(10)), 'L': list(range(12))}


def verify_controllers(root):
    found = {}
    for prop in root.rglob('compatible'):
        compatible = prop.read_bytes().split(b'\0')
        for name, (expected, address) in CONTROLLERS.items():
            if expected not in compatible:
                continue
            node = prop.parent
            parent = node.parent
            if (parent / '#address-cells').read_bytes() != struct.pack('>I', 1) or \
                    (parent / '#size-cells').read_bytes() != struct.pack('>I', 1):
                raise ValueError('Unexpected PIO address-cell layout')
            reg = (node / 'reg').read_bytes()
            if reg != struct.pack('>II', address, 0x400):
                raise ValueError('PIO device-tree register range differs from reviewed R16 layout')
            ranges = (parent / 'ranges').read_bytes()
            if ranges:
                if len(ranges) != 12:
                    raise ValueError('Unreviewed PIO bus address translation')
                child, physical, size = struct.unpack('>III', ranges)
                if child != physical or not child <= address or address + 0x400 > child + size:
                    raise ValueError('PIO bus does not use the reviewed identity address translation')
            if name in found:
                raise ValueError('Duplicate PIO device-tree controller')
            found[name] = {'compatible': expected.decode(), 'physical_base': address,
                           'node': str(node)}
    if set(found) != set(CONTROLLERS):
        raise ValueError('Both reviewed R16/A33 PIO controllers were not found in the live device tree')
    return found


class R16PioReader:
    def __init__(self, dt_root=Path('/sys/firmware/devicetree/base'), memory=Path('/dev/mem')):
        self.controllers = verify_controllers(dt_root)
        self.maps = {}
        fd = os.open(str(memory), os.O_RDONLY | os.O_SYNC)
        try:
            for name, (_, address) in CONTROLLERS.items():
                page = address & ~(mmap.PAGESIZE - 1)
                self.maps[name] = (mmap.mmap(fd, mmap.PAGESIZE, flags=mmap.MAP_SHARED,
                                           prot=mmap.PROT_READ, offset=page), address - page)
            self.sample()
        except Exception:
            self.close()
            raise
        finally:
            os.close(fd)

    def sample(self):
        banks = {}
        for port, pins in PINS.items():
            name = 'always_on' if port == 'L' else 'main'
            bank = 0 if port == 'L' else ord(port) - ord('A')
            region, base = self.maps[name]
            offset = base + bank * 0x24
            # Only the four mux configuration words and the DATA word are read.
            # No IRQ status/acknowledgment registers, writes, or pin requests.
            config = [struct.unpack('<I', region[offset+i*4:offset+i*4+4])[0] for i in range(4)]
            data = struct.unpack('<I', region[offset+0x10:offset+0x14])[0]
            banks[port] = {'data_hex': f'{data:08x}', 'mux_words_hex': [f'{v:08x}' for v in config]}
        return banks

    def close(self):
        for region, _ in self.maps.values():
            region.close()
