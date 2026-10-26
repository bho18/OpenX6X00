#!/usr/bin/env python3
"""Exercise a prebuilt RADE benchmark with the pinned V1 speech fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', required=True, type=Path)
    parser.add_argument('--tx', required=True, type=Path, help='pinned rade_tx_wav executable')
    parser.add_argument('--speech', required=True, type=Path, help='pinned 16 kHz input_sample.wav')
    args = parser.parse_args()
    expected = json.loads((ROOT / 'tools/freedv/sources.json').read_text())['rade_c']['speech_fixture_sha256']
    if hashlib.sha256(args.speech.read_bytes()).hexdigest() != expected:
        raise ValueError('speech fixture hash does not match sources.json')
    benchmark, tx, speech = (p.resolve() for p in (args.benchmark, args.tx, args.speech))
    with tempfile.TemporaryDirectory(prefix='freedv-rade-bench-test-') as temporary:
        tmp = Path(temporary)
        modem_wav = tmp / 'modem.wav'
        subprocess.run([str(tx), str(speech), str(modem_wav)], check=True, stdout=subprocess.DEVNULL)
        for label, wav_path, rate in (('speech', speech, 16000), ('modem', modem_wav, 8000)):
            with wave.open(str(wav_path), 'rb') as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, rate, 'NONE'):
                    raise ValueError(f'{label} is not mono 16-bit PCM at {rate} Hz')
                (tmp / f'{label}.raw').write_bytes(wav.readframes(wav.getnframes()))
        (tmp / 'silence.raw').write_bytes(bytes(16000))

        def run(direction, fixture, seconds, pacing='unpaced', warmup=1, workload='continuous'):
            result = subprocess.run([str(benchmark), direction, str(tmp / (fixture + '.raw')),
                                     str(seconds), pacing, str(warmup), workload],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f'benchmark failed ({result.returncode}):\n{result.stderr}')
            obj = json.loads(result.stdout)
            if obj['mode'] != 'RADE-V1' or not obj['resampling_included'] or obj['radio_io'] or obj['full_pipeline_qualified']:
                raise AssertionError('invalid benchmark identity or qualification flag')
            if obj['frames'] < 1 or obj['nonzero_output_samples'] > obj['resampled_output_samples']:
                raise AssertionError('invalid benchmark counters')
            return obj

        tx_result = run('tx', 'speech', 2)
        rx_result = run('rx', 'modem', 4)
        no_sync = run('rx', 'silence', 2)
        interrupted = run('rx', 'modem', 14, warmup=0, workload='interrupt')
        paced = run('tx', 'speech', 1, pacing='paced')
        assert tx_result['nonzero_output_samples'] > 0 and tx_result['deadline_misses'] is None
        assert rx_result['sync_blocks'] > 0 and rx_result['feature_blocks'] > 0 and rx_result['nonzero_output_samples'] > 0
        assert no_sync['sync_blocks'] == 0 and no_sync['output_samples'] == 0
        assert interrupted['interrupted_input_samples'] == 16000 and interrupted['sync_blocks_after_first_interrupt'] > 0
        assert paced['deadline_misses'] >= 0 and paced['warmup_audio_seconds'] >= 1
        print('PASS: RADE V1 TX, synchronized RX, no-sync RX, interruption and paced streaming prototype')


if __name__ == '__main__':
    main()
