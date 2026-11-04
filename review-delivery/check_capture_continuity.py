#!/usr/bin/env python3
"""Exact PCM comparison with a concurrent same-source recording, not intelligibility.

Accepts contiguous matches or proves one deletion by checking every sample on
both sides. Ambiguous/repeated waveforms and changed sample formats/gain remain
INCONCLUSIVE. Pipe timestamps are deliberately not treated as ADC timestamps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import wave


def read_pcm(path):
    with wave.open(str(path)) as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 44100):
            raise ValueError('Requires mono 16-bit 44100 Hz WAV')
        frames = w.getnframes()
        if frames > 44100 * 120: raise ValueError('Maximum comparison length is 120 seconds')
        data = w.readframes(frames)
        if len(data) != frames * 2: raise ValueError('Truncated PCM')
        return data


def unique_aligned(reference, pattern):
    positions = []
    p = reference.find(pattern)
    while p >= 0:
        if p % 2 == 0:
            positions.append(p)
            if len(positions) > 1: return None
        p = reference.find(pattern, p + 1)
    return positions[0] if positions else None


def compare(reference, captured):
    inconclusive = {'status': 'INCONCLUSIVE', 'reason': 'No unique exact contiguous or single-deletion match'}
    if len(reference) % 2 or len(captured) % 2 or len(captured) < 8192: return inconclusive
    first = unique_aligned(reference, captured[:4096])
    last = unique_aligned(reference, captured[-4096:])
    if first is None or last is None: return inconclusive
    after = last - (len(captured) - 4096)
    if after == first and reference[first:first + len(captured)] == captured:
        return {'status': 'PASS', 'scope': 'Exact PCM continuity only', 'reference_start_sample': first // 2,
                'compared_samples': len(captured) // 2, 'missing_samples': 0}
    if after <= first or after + len(captured) > len(reference): return inconclusive
    # Find all valid splice positions. Repeated adjacent ADC values can make
    # the boundary a range even when the missing sample count is exact.
    prefix = 0
    while prefix < len(captured) and captured[prefix:prefix+2] == reference[first+prefix:first+prefix+2]: prefix += 2
    suffix = len(captured)
    while suffix > 0 and captured[suffix-2:suffix] == reference[after+suffix-2:after+suffix]: suffix -= 2
    if suffix > prefix: return inconclusive
    missing = (after - first) // 2
    return {'status': 'FAIL', 'reason': 'Exact single deletion proved by all samples before and after the splice',
            'reference_start_sample': first // 2, 'compared_samples': len(captured) // 2,
            'missing_samples': missing, 'missing_ms': missing * 1000 / 44100,
            'splice_capture_sample_min': suffix // 2, 'splice_capture_sample_max': prefix // 2}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('reference', type=Path); p.add_argument('capture', type=Path)
    a = p.parse_args()
    try: result = compare(read_pcm(a.reference), read_pcm(a.capture))
    except (OSError, ValueError, EOFError, wave.Error) as e:
        result = {'status': 'INCONCLUSIVE', 'reason': str(e)}
    for name, path in [('reference', a.reference), ('capture', a.capture)]:
        if path.is_file(): result[name + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    result['listening'] = 'UNASSESSED'
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
