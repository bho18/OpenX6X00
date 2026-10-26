#!/usr/bin/env python3
"""Generate a local, pinned 700E diagnostic waveform; never controls the radio."""
import argparse
import array
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'tools/freedv/sources.json'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tx', type=Path, required=True, help='built pinned Codec2 freedv_tx')
    parser.add_argument('--speech', type=Path, required=True, help='pinned ve9qrp_10s.raw fixture')
    parser.add_argument('--output', type=Path, required=True, help='new output directory outside this repository')
    args = parser.parse_args()
    tx, speech = (p.resolve(strict=True) for p in (args.tx, args.speech))
    manifest = json.loads(SOURCES.read_text())
    speech_hash = digest(speech)
    if speech_hash != manifest['codec2']['speech_fixture_sha256'] or speech.stat().st_size != 160000:
        parser.error('speech fixture does not match the pinned ten-second 8 kHz PCM input')
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('output directory must be outside the source repository')
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error('output directory already exists; choose a new one')
    modem = output / '700e-modem-8000-s16le.raw'
    wav = output / '700e-modem-44100.wav'
    tx_result = subprocess.run([str(tx), '700E', str(speech), str(modem)], capture_output=True, text=True)
    (output / 'transmitter.log').write_text(tx_result.stdout + tx_result.stderr)
    if tx_result.returncode or not modem.is_file() or modem.stat().st_size != 160000:
        raise SystemExit(f'700E transmitter failed or produced an unexpected length; see {output}/transmitter.log')
    version = subprocess.run(['sox', '--version'], check=True, capture_output=True, text=True).stdout.strip()
    # Fixed-quality resampling, no automatic dither, no gain or hardware I/O.
    sox = ['sox', '-D', '-t', 'raw', '-r', '8000', '-e', 'signed-integer', '-b', '16', '-c', '1', '-L', str(modem),
           '-t', 'wav', '-r', '44100', '-e', 'signed-integer', '-b', '16', '-c', '1', str(wav), 'rate', '-v']
    converted = subprocess.run(sox, capture_output=True, text=True)
    (output / 'resampler.log').write_text(converted.stdout + converted.stderr)
    if converted.returncode or re.search(r'\bclipp?(?:ed|ing)\b', converted.stderr, re.IGNORECASE):
        raise SystemExit(f'SoX conversion failed; see {output}/resampler.log')
    with wave.open(str(wav), 'rb') as data:
        if (data.getnchannels(), data.getsampwidth(), data.getframerate(), data.getnframes(), data.getcomptype()) != (1, 2, 44100, 441000, 'NONE'):
            raise SystemExit('unexpected generated WAV format or duration')
        pcm = array.array('h')
        pcm.frombytes(data.readframes(data.getnframes()))
    if sys.byteorder != 'little':
        pcm.byteswap()
    peak = max(abs(int(sample)) for sample in pcm)
    if peak == 0 or peak >= 32767:
        raise SystemExit('generated WAV is silent or reaches the signed PCM limit')
    result = {
        'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'mode': '700E', 'duration_seconds': 10, 'source_manifest_sha256': digest(SOURCES),
        'expected_codec2_commit': manifest['codec2']['commit'], 'transmitter_provenance_verified': False,
        'speech_sha256': speech_hash,
        'freedv_tx_sha256': digest(tx), 'sox_version': version, 'resampling': 'SoX rate -v, dither disabled',
        'modem_8000_sha256': digest(modem), 'modem_44100_wav_sha256': digest(wav), 'wav_peak_abs': peak,
        'radio_control': False, 'radio_hardware_tested': False, 'automatic_upload': False,
    }
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'Generated 700E waveform and manifest in {output}; no radio was accessed.')


if __name__ == '__main__':
    main()
