#!/usr/bin/env python3
"""Conservative PCM accounting and identifiable-signal comparison.

Silence or a sample jump in arbitrary speech is not proof of a transport gap.
Use the known reference and acquisition clock for gaps/delays/discontinuities.
"""
import argparse
import array
import json
import math
import sys
import wave
from pathlib import Path


def pcm(path):
    with wave.open(str(path), 'rb') as wav:
        if wav.getsampwidth() != 2:
            raise ValueError('16-bit PCM WAV required')
        data = array.array('h', wav.readframes(wav.getnframes()))
        if sys.byteorder != 'little':
            data.byteswap()
        return data, wav.getframerate(), wav.getnchannels()


def accounting(samples, rate, expected_samples=None):
    if rate <= 0 or not samples:
        raise ValueError('nonempty PCM and positive sample rate required')
    delta = [abs(int(b) - int(a)) for a, b in zip(samples, samples[1:])]
    return {'samples': len(samples), 'duration_seconds': len(samples)/rate,
            'sample_difference': None if expected_samples is None else len(samples)-expected_samples,
            'clipped_samples': sum(abs(s) >= 32767 for s in samples),
            'peak': max(abs(s) for s in samples),
            'rms': math.sqrt(sum(float(s)*s for s in samples)/len(samples)),
            'maximum_step': max(delta, default=0),
            'gap_assessment': 'INCONCLUSIVE without known reference and clock boundaries',
            'intelligibility': 'UNASSESSED'}


def compare_known(reference, observed, maximum_delay):
    """Exact known-fixture check. Not an RF/speech equality criterion."""
    if not reference or maximum_delay < 0 or maximum_delay > 48000:
        raise ValueError('known nonempty reference and bounded delay required')
    best = None
    for delay in range(min(maximum_delay, len(observed))+1):
        overlap = min(len(reference), len(observed)-delay)
        if not overlap:
            continue
        errors = sum(a != b for a, b in zip(reference[:overlap], observed[delay:delay+overlap]))
        # Penalize absent reference samples so a one-sample overlap cannot win.
        score = errors + len(reference)-overlap
        candidate = (score, delay, errors, overlap)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return {'status': 'FAIL', 'reason': 'no overlap'}
    score, delay, errors, overlap = best
    return {'status': 'PASS' if not score else 'FAIL', 'delay_samples': delay,
            'mismatched_samples': errors, 'missing_reference_samples': len(reference)-overlap,
            'extra_samples': max(0, len(observed)-delay-len(reference))}


def marker_projection(raw, marker):
    """Aligned, distinguishable RF/playback marker projection, no sync claim."""
    if len(raw) != len(marker) or not raw:
        raise ValueError('aligned equal-length marker window required')
    dot = sum(float(a)*b for a, b in zip(raw, marker))
    norm = sum(float(b)*b for b in marker)
    power = sum(float(a)*a for a in raw)
    return {'gain': dot/norm if norm else None,
            'correlation': dot/math.sqrt(norm*power) if norm and power else None,
            'qualification': 'INCONCLUSIVE until RF input and playback marker identities are independently verified'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav', type=Path); p.add_argument('--expected-frames', type=int); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); samples, rate, channels = pcm(a.wav)
    result = {'channels': channels, 'sample_rate': rate,
              'channels_analysis': [accounting(samples[ch::channels], rate, a.expected_frames) for ch in range(channels)]}
    a.out.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
