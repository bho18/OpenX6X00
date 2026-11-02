#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only symbolization for the documented RADE software-clock perf capture.

Supports one little-endian PERF v2 CPU_CLOCK event at 99 Hz, sample layout
IP/TID/TIME/PERIOD, MMAP2 records, and little-endian ARM ELF32 binaries. Rejects
other layouts. It is not a general perf replacement. Never executes input files.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

MAX_FILE_BYTES = 64 * 1024 * 1024
RUNTIME_PATHS = {
    '/tmp/freedv-benchmark/lib/librade.so.0.1': ('librade.so.0.1', 'lib/librade.so.0.1'),
    '/tmp/freedv-benchmark/bin/rade-frame-bench': ('rade-frame-bench', 'bin/rade-frame-bench'),
}


class DecodeError(ValueError):
    """Input is incomplete, inconsistent, or outside the supported format."""


def require(condition, message):
    if not condition:
        raise DecodeError(message)


def load(path, limit=MAX_FILE_BYTES):
    require(path.is_file(), f'missing regular file: {path}')
    require(path.stat().st_size <= limit, f'file exceeds {limit} bytes: {path}')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, f'file grew beyond {limit} bytes: {path}')
    return data


def block(data, offset, size, label):
    require(offset >= 0 and size >= 0 and offset <= len(data) and size <= len(data) - offset,
            f'truncated or out-of-bounds {label}')
    return data[offset:offset + size]


def unpack(fmt, data, offset, label):
    return struct.unpack(fmt, block(data, offset, struct.calcsize(fmt), label))


def cstring(data, offset, label):
    require(0 <= offset < len(data), f'invalid string offset: {label}')
    end = data.find(b'\0', offset)
    require(end >= 0, f'unterminated string: {label}')
    try:
        return data[offset:end].decode('utf-8')
    except UnicodeDecodeError as exc:
        raise DecodeError(f'non-UTF-8 string: {label}') from exc


def verify_package(directory):
    sums = {}
    for line in load(directory / 'SHA256SUMS', 1024 * 1024).decode('utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'malformed SHA256SUMS entry')
        digest, name = match.groups()
        require(name not in sums, 'duplicate SHA256SUMS entry: ' + name)
        sums[name] = digest
    checked = {}
    for local, packaged in [*RUNTIME_PATHS.values(), ('package-manifest.json', 'package-manifest.json')]:
        data = load(directory / local)
        digest = hashlib.sha256(data).hexdigest()
        require(sums.get(packaged) == digest, 'package checksum mismatch: ' + packaged)
        checked[local] = (data, digest)
    try:
        manifest = json.loads(checked['package-manifest.json'][0])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecodeError('invalid package manifest JSON') from exc
    require(isinstance(manifest, dict) and manifest.get('kind') == 'benchmark-package'
            and manifest.get('target') == 'x6200-buildroot', 'unexpected package kind or target')
    return checked


def read_elf(data):
    require(block(data, 0, 7, 'ELF identification') == b'\x7fELF\x01\x01\x01',
            'expected little-endian ELF32 version 1')
    require(unpack('<H', data, 18, 'ELF machine')[0] == 40, 'expected ARM ELF')
    phoff, shoff = unpack('<II', data, 28, 'ELF table offsets')
    ehsize, phentsize, phnum, shentsize, shnum = unpack('<HHHHH', data, 40, 'ELF table sizes')
    require(ehsize == 52 and phentsize == 32 and shentsize == 40,
            'unsupported ELF header/table entry size')
    require(0 < phnum <= 4096 and 0 < shnum <= 4096, 'unsupported ELF table counts')
    block(data, phoff, phnum * phentsize, 'ELF program table')
    block(data, shoff, shnum * shentsize, 'ELF section table')
    segments = [unpack('<8I', data, phoff + i * phentsize, 'ELF program header') for i in range(phnum)]
    sections = [unpack('<10I', data, shoff + i * shentsize, 'ELF section header') for i in range(shnum)]
    for segment in segments:
        if segment[0] == 1:
            block(data, segment[1], segment[4], 'ELF LOAD segment')
    symbols = set()
    for section in sections:
        if section[1] not in (2, 11):
            continue
        require(section[9] == 16 and section[5] % 16 == 0, 'unsupported ELF symbol entry layout')
        require(section[6] < len(sections), 'invalid ELF symbol string-table link')
        string_section = sections[section[6]]
        require(string_section[1] == 3, 'ELF symbol strings must reference STRTAB')
        strings = block(data, string_section[4], string_section[5], 'ELF symbol strings')
        block(data, section[4], section[5], 'ELF symbol table')
        for pos in range(section[4], section[4] + section[5], 16):
            name, value, size, info, _other, index = unpack('<IIIBBH', data, pos, 'ELF symbol')
            if info & 15 != 2 or not index or not size:
                continue
            symbols.add((value & ~1, size, cstring(strings, name, 'ELF symbol name')))
    require(symbols, 'ELF file has no sized function symbols')
    return segments, sorted(symbols)


def decode(directory, capture_name='rade-rx-profile.data'):
    require(Path(capture_name).name == capture_name, 'capture name must be a filename, without directories')
    checked = verify_package(directory)
    files = {recorded: read_elf(checked[local][0]) for recorded, (local, _packaged) in RUNTIME_PATHS.items()}
    raw = load(directory / capture_name)
    require(block(raw, 0, 8, 'perf magic') == b'PERFILE2', 'expected little-endian PERF v2')
    header_size, attr_size, attr_offset, attrs_size, data_offset, data_size = unpack('<6Q', raw, 8, 'perf header')
    require(header_size == 104 and attr_size == 144 and attrs_size == 144,
            'unsupported perf header or event count; one 128-byte event attribute required')
    attrs = block(raw, attr_offset, attrs_size, 'perf attribute section')
    event_type, attr_struct_size, config, frequency, sample_type, read_format, flags = unpack('<IIQQQQQ', attrs, 0, 'perf event attribute')
    require((event_type, attr_struct_size, config, frequency, sample_type) == (1, 128, 0, 99, 0x107),
            'unsupported event: require software CPU_CLOCK, 99 Hz, IP/TID/TIME/PERIOD')
    require(read_format == 20, 'unsupported perf read format')
    require(flags & (1 << 5) and not flags & (1 << 4), 'require userspace included and kernel excluded')
    require(flags & (1 << 10), 'require frequency sampling')
    require(flags & (1 << 18), 'require sample_id_all')
    require(flags & (1 << 23), 'require MMAP2 records')
    ids_offset, ids_size = unpack('<QQ', attrs, 128, 'event ID section')
    require(ids_size > 0 and ids_size % 8 == 0, 'unsupported event ID section')
    block(raw, ids_offset, ids_size, 'event IDs')
    require(data_offset >= attr_offset + attrs_size, 'overlapping perf data and attributes')
    block(raw, data_offset, data_size, 'perf data section')
    feature_words = unpack('<4Q', raw, 72, 'perf feature bits')
    require(feature_words[1:] == (0, 0, 0), 'unsupported high feature bits')
    feature_bits = [i for i in range(64) if feature_words[0] & (1 << i)]
    feature_table_end = data_offset + data_size + 16 * len(feature_bits)
    block(raw, data_offset + data_size, 16 * len(feature_bits), 'perf feature table')
    for i, feature in enumerate(feature_bits):
        start, size = unpack('<QQ', raw, data_offset + data_size + 16 * i, 'perf feature section')
        require(start >= feature_table_end, 'overlapping perf feature data')
        block(raw, start, size, f'perf feature {feature}')
    mappings = []
    samples = []
    records = collections.Counter()
    dso_periods, function_periods, address_periods = (collections.Counter() for _ in range(3))
    dso_counts, function_counts, address_counts = (collections.Counter() for _ in range(3))
    lost_samples = 0
    offset = data_offset
    data_end = data_offset + data_size
    while offset < data_end:
        kind, misc, size = unpack('<IHH', raw, offset, 'perf record header')
        require(size >= 8 and size <= data_end - offset, 'truncated or invalid perf record')
        payload = block(raw, offset + 8, size - 8, 'perf record payload')
        records[kind] += 1
        if kind == 10:  # PERF_RECORD_MMAP2
            require(not misc & (1 << 14), 'unsupported MMAP2 build-ID union')
            require(len(payload) >= 88, 'truncated MMAP2 payload')
            pid, tid, start, length, pgoff, _major, _minor, _ino, _gen, prot, _mapflags = unpack('<IIQQQIIQQII', payload, 0, 'MMAP2 fixed fields')
            name = cstring(payload[64:-16], 0, 'MMAP2 filename')
            require(length > 0, 'empty MMAP2 range')
            trailer_pid, trailer_tid, _time = unpack('<IIQ', payload, len(payload) - 16, 'MMAP2 sample_id')
            require((trailer_pid, trailer_tid) == (pid, tid), 'MMAP2 sample identity mismatch')
            mappings.append(dict(pid=pid, tid=tid, start=start, length=length, pgoff=pgoff, name=name, prot=prot))
        elif kind == 9:  # PERF_RECORD_SAMPLE
            require(size == 40 and misc & 7 == 2, 'unsupported non-userspace sample or sample layout')
            ip, pid, tid, time, period = unpack('<QIIQQ', payload, 0, 'sample')
            require(period > 0, 'sample period must be positive')
            candidates = [m for m in mappings if m['pid'] == pid and m['start'] <= ip < m['start'] + m['length']]
            require(candidates, 'sample has no preceding matching mapping: ' + hex(ip))
            mapping = candidates[-1]
            recorded_path = mapping['name']
            file_offset = ip - mapping['start'] + mapping['pgoff']
            symbol, virtual = '<library not provided>', None
            if recorded_path in files:
                segments, symbols = files[recorded_path]
                matching_segments = [s for s in segments if s[0] == 1 and s[1] <= file_offset < s[1] + s[4]]
                require(len(matching_segments) == 1, 'sample does not map to exactly one ELF LOAD segment')
                segment = matching_segments[0]
                virtual = file_offset - segment[1] + segment[2]
                matching_symbols = [s for s in symbols if s[0] <= virtual < s[0] + s[1]]
                symbol = min(matching_symbols, key=lambda s: s[1])[2] if matching_symbols else '<no function symbol>'
                address_periods[(recorded_path, virtual)] += period
                address_counts[(recorded_path, virtual)] += 1
            dso_periods[recorded_path] += period
            dso_counts[recorded_path] += 1
            function_periods[(recorded_path, symbol)] += period
            function_counts[(recorded_path, symbol)] += 1
            samples.append(dict(time=time, period=period, path=recorded_path, virtual=virtual))
        elif kind == 2:  # PERF_RECORD_LOST
            require(len(payload) == 32, 'unsupported LOST layout')
            _event_id, lost = unpack('<QQ', payload, 0, 'LOST fields')
            lost_samples += lost
        elif kind == 13:  # PERF_RECORD_LOST_SAMPLES
            require(len(payload) == 24, 'unsupported LOST_SAMPLES layout')
            lost_samples += unpack('<Q', payload, 0, 'LOST_SAMPLES count')[0]
        elif kind not in (3, 4, 68, 69, 73, 74, 78, 82):
            raise DecodeError('unsupported perf record type: ' + str(kind))
        offset += size
    require(offset == data_end and samples, 'profile must contain complete sample records')
    require(any(m['name'] in RUNTIME_PATHS for m in mappings), 'expected runtime binary paths were not captured')
    period_sum = sum(s['period'] for s in samples)

    def rows(periods, counts):
        return [dict(key=key, samples=counts[key], period=period, percent=100 * period / period_sum)
                for key, period in periods.most_common()]

    hotspot = [s for s in samples if s['path'] == '/tmp/freedv-benchmark/lib/librade.so.0.1'
               and s['virtual'] is not None and 0xd22c <= s['virtual'] <= 0xd280]
    return dict(schema_version=1, capture_sha256=hashlib.sha256(raw).hexdigest(), samples=len(samples),
        lost_samples=lost_samples, event_period_sum=period_sum,
        event=dict(type=event_type, config=config, frequency=frequency, sample_type=sample_type,
                   exclude_kernel=True, exclude_user=False, sample_id_all=True),
        record_counts=records, feature_bits=feature_bits, build_id_feature_present=2 in feature_bits,
        first_sample_time=min(s['time'] for s in samples), last_sample_time=max(s['time'] for s in samples),
        sample_span_seconds=(max(s['time'] for s in samples) - min(s['time'] for s in samples)) / 1e9,
        verified_sha256={name:entry[1] for name,entry in checked.items()}, mappings=mappings,
        dso_totals=rows(dso_periods,dso_counts), function_totals=rows(function_periods,function_counts),
        top_addresses=rows(address_periods,address_counts)[:35],
        original_binary_hotspot=(dict(start='0xd22c', end_inclusive='0xd280', samples=len(hotspot),
            percent=100*sum(s['period'] for s in hotspot)/period_sum,
            note='Historical address range in the original SDK library.')
            if checked['librade.so.0.1'][1] == '9d411e29b2aa69d79009817a23f5cc45bbf93d91da152cae1e7f3a3625bfe00c' else None),
        limitations=[
            'Package checksums are checked for supplied files; they are not an authenticated signature.',
            'Recorded build IDs are not matched by this helper; exact path and returned package checksums support identity.',
            'Symbols use exact recorded runtime paths and their supplied ELF LOAD offsets; system-library files were not supplied.',
            'Self samples only; no callchain or inclusive per-function timing.',
            'The whole command includes startup, warm-up, processing and teardown; no steady-state partitioning.',
            'Limited-format independent decoder, not upstream perf or a general-purpose parser.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--capture-name', default='rade-rx-profile.data')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = decode(args.directory, args.capture_name)
        text = json.dumps(result, indent=2) + '\n'
        if args.output:
            with args.output.open('x') as stream:
                stream.write(text)
        else:
            print(text, end='')
    except (DecodeError, OSError, UnicodeDecodeError) as exc:
        parser.exit(1, 'Profile decode failed: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
