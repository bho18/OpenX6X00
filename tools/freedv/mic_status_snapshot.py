#!/usr/bin/env python3
"""Read a narrowly identified GUI packet buffer; never attach, write or read UART."""
import hashlib
import os
from pathlib import Path
import struct

EXPECTED_GUI = '5fff9870e32f4ce10d4fe84982f2c1b7b40d3461085acb387859a0e8947caf76'
# Exact .2 GUI ELF: nm -an => 01bb6a10 b pack, hw/x6200/hw_x6200.c.
# ELF32 little-endian ARM PIE, first PT_LOAD offset/vaddr both zero.
PACK_POINTER_RVA = 0x01BB6A10
PACK_SIZE = 552


def mappings(text):
    result = []
    for line in text.splitlines():
        fields = line.split(None, 5)
        lo, hi = (int(x, 16) for x in fields[0].split('-'))
        result.append({'lo': lo, 'hi': hi, 'perms': fields[1],
                       'offset': int(fields[2], 16),
                       'path': fields[5] if len(fields) > 5 else ''})
    return result


def start_ticks(text):
    return text.rsplit(')', 1)[1].split()[19]


def readable(regions, address, size):
    return any('r' in r['perms'] and r['lo'] <= address and address + size <= r['hi']
               for r in regions)


def decode_packet(raw):
    if len(raw) != PACK_SIZE or struct.unpack_from('<I', raw)[0] != 0xAA5555AA:
        raise ValueError('Packet buffer length/magic differs from the pinned layout')
    flags = struct.unpack_from('<I', raw, 516)[0]
    return {'flags_u32': flags, 'flags_hex': f'{flags:08x}',
            'aggregate_tx': bool(flags & 2), 'hkey_u32': struct.unpack_from('<I', raw, 544)[0],
            'hkey_hex': raw[544:548].hex(), 'status_tail_hex': raw[516:].hex(),
            'raw_packet_hex': raw.hex(),
            'provenance': 'sampled GUI library packet buffer, not a UART wire capture',
            'wire_crc_verified': False}


class PacketReader:
    def __init__(self, process, exe):
        self.process = process
        self.identity = start_ticks((process / 'stat').read_text())
        self.regions = mappings((process / 'maps').read_text())
        bases = [r['lo'] for r in self.regions if r['offset'] == 0 and r['path'] == str(exe)]
        if len(bases) != 1:
            raise ValueError('Cannot uniquely identify the pinned GUI PIE load base')
        self.pointer_address = bases[0] + PACK_POINTER_RVA
        if not readable(self.regions, self.pointer_address, 4):
            raise ValueError('Pinned packet pointer lies outside readable mappings')
        self.fd = os.open(str(process / 'mem'), os.O_RDONLY)
        try:
            self.sample()
        except Exception:
            self.close()
            raise

    def sample(self):
        if start_ticks((self.process / 'stat').read_text()) != self.identity:
            raise ValueError('GUI process identity changed; stopping observation')
        pointer = os.pread(self.fd, 4, self.pointer_address)
        if len(pointer) != 4:
            raise ValueError('Cannot read pinned packet pointer')
        address = struct.unpack('<I', pointer)[0]
        if not address or address % 4 or not readable(self.regions, address, PACK_SIZE):
            raise ValueError('Packet allocation is absent or outside readable mappings')
        first = os.pread(self.fd, PACK_SIZE, address)
        second = os.pread(self.fd, PACK_SIZE, address)
        if os.pread(self.fd, 4, self.pointer_address) != pointer:
            raise ValueError('Packet allocation changed during observation')
        result = decode_packet(second)
        result['repeat_read_equal'] = first == second
        result['coherent_snapshot_guaranteed'] = False
        return result

    def close(self):
        os.close(self.fd)


def open_verified_gui(proc=Path('/proc'), exe=Path('/usr/sbin/x6100_gui'), expected=EXPECTED_GUI):
    candidates = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(entry / 'exe') == str(exe):
                candidates.append(entry)
        except OSError:
            continue
    if len(candidates) != 1:
        raise ValueError('Expected exactly one running x6100_gui')
    process = candidates[0]
    # Hash the executable actually running, not just the on-disk install path.
    if hashlib.sha256((process / 'exe').read_bytes()).hexdigest() != expected:
        raise ValueError('Running GUI differs from pinned .2 ELF; memory offsets rejected')
    return PacketReader(process, exe)


def existing_gpio_values(root=Path('/sys/class/gpio')):
    result = {}
    for path in sorted(root.glob('gpio[0-9]*/value')):
        try:
            result[path.parent.name] = path.read_text().strip()
        except OSError as e:
            result[path.parent.name] = 'UNREADABLE: ' + str(e)
    return result
