#!/usr/bin/env python3
"""Collect USB GUI traces and optionally decode SDR demodulated audio; never key RF."""
import argparse
from array import array
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave
from usb_headset_inventory import collect, sha256


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def read_sdr(path):
    with wave.open(str(path), 'rb') as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if channels not in (1, 2) or width != 2 or rate not in (8000, 44100, 48000) or not 0 < frames <= rate * 30:
            raise ValueError('SDR input must be 1..30 seconds of PCM16 mono/stereo at 8, 44.1 or 48 kHz. Record demodulated USB audio, not IQ.')
        raw = wav.readframes(frames)
        if len(raw) != frames * channels * 2:
            raise ValueError('Truncated SDR WAV')
    samples = array('h', raw)
    if sys.byteorder != 'little': samples.byteswap()
    if channels == 2: samples = array('h', (int((samples[i] + samples[i + 1]) / 2) for i in range(0, len(samples), 2)))
    metadata = dict(rate=rate, channels=channels, frames=frames, seconds=frames / rate,
                    rms=math.sqrt(sum(x * x for x in samples) / len(samples)),
                    clipped_samples=sum(abs(x) >= 32760 for x in samples))
    if sys.byteorder != 'little': samples.byteswap()
    return samples.tobytes(), metadata


def wrap(raw, destination, rate=48000):
    with wave.open(str(destination), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(raw)


def collect_evidence(output, manifest, sdr=None):
    result = dict(schema='x6200.usb-rf-evidence.v1', status='BLOCKED',
                  rf_key_commands_sent=False, rf_recording_decoded=False,
                  listening='UNASSESSED', physical_rf_off_timing='UNMEASURED')
    try:
        inventory = collect(expected_gui=manifest['gui_sha256'])
        save(output / 'inventory.json', inventory)
        result['inventory_status'] = inventory['status']
        # Device loss is a reason to collect evidence, not to discard the trace.
        sessions = sorted(Path('/tmp').glob('freedv-usb-*.jsonl'), key=lambda p: p.stat().st_mtime)
        if not sessions:
            raise ValueError('No GUI USB-mode trace found. Complete a USB-mode test first.')
        trace = sessions[-1]
        prefix = trace.with_suffix('')
        # A stopped worker closes all trace descriptors. Refuse a torn capture.
        for process in Path('/proc').glob('[0-9]*/fd'):
            for fd in process.glob('*'):
                try:
                    if str(fd.resolve()) == str(trace):
                        raise ValueError('USB audio worker is still running. Stop/Exit the mode before collecting.')
                except OSError:
                    pass
        if trace.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Trace exceeds collection bound.')
        shutil.copy2(trace, output / 'usb-session.jsonl')
        rows = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
        if not rows:
            raise ValueError('GUI trace is empty; session preparation did not complete.')
        result['last_gui_state'] = rows[-1]
        for name in ('mic', 'radio-rx', 'modem', 'decoded'):
            path = Path(str(prefix) + '-' + name + '.raw')
            if path.exists():
                if path.stat().st_size > 48000 * 2 * 30 or path.stat().st_size % 2:
                    raise ValueError('Unexpected trace PCM length: ' + name)
                wrap(path.read_bytes(), output / (name + '.wav'))
        result['status'] = 'COLLECTED_NO_SDR_RECORDING'
        if sdr:
            from usb_half_duplex import stream_process
            runner = Path('/opt/freedv-benchmark/bin/freedv_voice_runner')
            if sha256(runner) != manifest['runner_sha256']:
                raise ValueError('Installed decoder runner mismatch.')
            if sha256(Path('/opt/freedv-benchmark/lib/librade.so.0.1')) != manifest['rade_sha256']:
                raise ValueError('Installed RADE library mismatch.')
            raw, metadata = read_sdr(sdr)
            shutil.copy2(sdr, output / 'sdr-usb-audio.wav')
            result['sdr_input'] = metadata
            # The existing runner loops RX fixtures. Pad to the declared length
            # so it never wraps to the beginning of this off-air recording.
            seconds = math.ceil(metadata['seconds']) + 1
            raw += b'\0' * (seconds * metadata['rate'] * 2 - len(raw))
            decoded = output / 'offair-decode'
            decoded.mkdir()
            with tempfile.TemporaryDirectory(prefix='usb-sdr-decode-') as temp:
                pcm = Path(temp) / 'sdr.raw';pcm.write_bytes(raw)
                stream_process([runner, '--rx', pcm, '--output', decoded, '--rate', metadata['rate'],
                                '--seconds', seconds], output, timeout=180)
            stats = json.loads((decoded / 'engine-stats.json').read_text())
            result['offair_decoder_stats'] = stats
            if stats.get('failures', 1) or stats.get('synchronized_blocks', 0) < 1 or stats.get('synthesis_blocks', 0) < 1:
                raise ValueError('Off-air recording did not produce synchronized decoded speech.')
            raw_decoded = decoded / 'decoded-s16le.raw'
            wrap(raw_decoded.read_bytes(), decoded / 'offair-decoded.wav', metadata['rate'])
            result['decoded_pcm_sha256'] = sha256(raw_decoded)
            raw_decoded.unlink()  # WAV is lossless; avoid duplicating audio in upload.
            result.update(status='RF_RECORDING_DECODED_LISTENING_REQUIRED', rf_recording_decoded=True)
    except (OSError, ValueError, KeyError, EOFError, wave.Error, subprocess.SubprocessError) as error:
        result['reason'] = str(error)
        print('STOP: ' + str(error), flush=True)
    finally:
        save(output / 'rf-evidence.json', result)
        files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(sha256(p) + '  ' + p.relative_to(output).as_posix() + '\n' for p in files))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sdr-wav', type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, exist_ok=False)
    manifest = json.loads(Path(__file__).with_name('rf-test-manifest.json').read_text())
    result = collect_evidence(args.output, manifest, args.sdr_wav)
    print('Evidence status: ' + result['status'], flush=True)
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__': main()
