#!/usr/bin/env python3
"""Measure whole-file FreeDV reference tools; never accesses radio controls.

This is an unpaced reference measurement, not the planned streaming/deadline
benchmark. The input, transmitter and receiver must come from pinned builds.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import wave


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run(command, log):
    start = time.monotonic()
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    elapsed = time.monotonic() - start
    log.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f'{command[0]} exited {result.returncode}; see {log}')
    return elapsed, result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('700E', 'RADE-V1'), required=True)
    parser.add_argument('--tx', type=Path, required=True, help='upstream transmit executable')
    parser.add_argument('--rx', type=Path, required=True, help='upstream receive executable')
    parser.add_argument('--input', type=Path, required=True, help='pinned speech fixture')
    parser.add_argument('--output', type=Path, required=True, help='new results directory')
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name('sources.json').read_text())
    fixture = args.input.resolve(strict=True)
    expected = manifest['codec2' if args.mode == '700E' else 'rade_c']['speech_fixture_sha256']
    actual = sha256(fixture)
    if actual != expected:
        parser.error('speech fixture SHA256 does not match sources.json')
    if args.mode == '700E':
        size = fixture.stat().st_size
        if size % 2:
            parser.error('700E fixture must contain 16-bit PCM')
        duration = size / (2 * 8000)
    else:
        with wave.open(str(fixture), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                parser.error('RADE fixture must be mono 16-bit PCM at 16 kHz')
            duration = wav.getnframes() / wav.getframerate()
    if duration <= 0:
        parser.error('empty speech fixture')
    args.output.mkdir(parents=True, exist_ok=False)
    tx, rx = str(args.tx.resolve(strict=True)), str(args.rx.resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix='freedv-reference-') as temporary:
        temp = Path(temporary)
        modem = temp / ('modem.raw' if args.mode == '700E' else 'modem.wav')
        decoded = temp / ('decoded.raw' if args.mode == '700E' else 'decoded.wav')
        if args.mode == '700E':
            tx_cmd = [tx, '700E', str(fixture), str(modem)]
            rx_cmd = [rx, '700E', str(modem), str(decoded)]
        else:
            tx_cmd = [tx, str(fixture), str(modem)]
            rx_cmd = [rx, str(modem), str(decoded)]
        tx_time, _ = run(tx_cmd, args.output / 'tx.log')
        rx_time, rx_log = run(rx_cmd, args.output / 'rx.log')
        if not modem.exists() or not modem.stat().st_size or not decoded.exists():
            raise RuntimeError('codec did not produce modem and decoded files')
        if args.mode == '700E':
            match = re.search(r'frames decoded:\s*(\d+)\s+output speech samples:\s*(\d+)', rx_log)
            if not match or int(match[1]) == 0 or int(match[2]) < 8000:
                raise RuntimeError('700E receiver did not decode sufficient speech')
            pcm = decoded.read_bytes()
        else:
            with wave.open(str(decoded), 'rb') as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                    raise RuntimeError('unexpected RADE output format')
                if wav.getnframes() < 16000:
                    raise RuntimeError('RADE receiver did not decode sufficient speech')
                pcm = wav.readframes(wav.getnframes())
        if not any(pcm):
            raise RuntimeError('decoded speech is silent')
        result = {
            'schema_version': 1, 'mode': args.mode, 'fixture_sha256': actual,
            'audio_seconds': duration, 'measurement': 'unpaced_whole_file',
            'tx_elapsed_seconds': tx_time, 'rx_elapsed_seconds': rx_time,
            'tx_realtime_factor': tx_time / duration, 'rx_realtime_factor': rx_time / duration,
            'streaming_deadlines_measured': False, 'radio_hardware_tested': False,
        }
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
