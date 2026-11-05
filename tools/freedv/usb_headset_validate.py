#!/usr/bin/env python3
"""USB-only speech/codec validation on the pinned .2 image; never controls RF."""
import argparse
from array import array
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import wave

from usb_headset_inventory import collect, sha256

RATE = 44100
SECONDS = 12
RUNNER = Path('/opt/freedv-benchmark/bin/freedv_voice_runner')
RUNNER_SHA = '33e8cadab3aff8b593af43b7a487dc24a50c61df85cb355ab3cc160c3be37d4f'
RADE = Path('/opt/freedv-benchmark/lib/librade.so.0.1')
RADE_SHA = '9d04766b1743a4cc3ef575497273a0d6a993e1cc10731d1bc5bada04723e230d'


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def endpoints(inventory):
    if inventory['status'] != 'USB_DUPLEX_CANDIDATE':
        raise ValueError('Inventory does not establish a USB duplex candidate.')
    cards = [c for c in inventory['usb_audio_cards'] if c['status'] == 'USB_DUPLEX_CANDIDATE']
    if len(cards) != 1:
        raise ValueError('Connect exactly one USB audio device; refusing ambiguous selection.')
    card = cards[0]
    if (card['usb']['id_vendor'], card['usb']['id_product']) != ('3511', '2b1e'):
        raise ValueError('This kit targets the inventoried Shokz Loop110 (3511:2b1e).')
    values = []
    for key, prefix in [('pulse_capture_sources', 'alsa_input.usb-'),
                        ('pulse_playback_sinks', 'alsa_output.usb-')]:
        candidates = card[key]
        if len(candidates) != 1:
            raise ValueError('Ambiguous USB source/sink; no default or profile fallback.')
        endpoint = candidates[0]
        name = endpoint['name']
        if (not endpoint['physical_alsa'] or endpoint['monitor'] or
                endpoint['alsa_card'] != card['number'] or
                not name.startswith(prefix) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,511}', name)):
            raise ValueError('Endpoint is not an explicitly named physical USB device.')
        if endpoint['mute'] != 'no':
            raise ValueError('USB endpoint is muted; inspect its existing mixer state first.')
        values.append(name)
    return tuple(values)


def pcm_metrics(path):
    data = path.read_bytes()
    if not data or len(data) % 2 or len(data) > RATE * 2 * 30:
        raise ValueError('PCM is empty, malformed, or exceeds 30 seconds.')
    samples = array('h', data)
    if sys.byteorder != 'little':
        samples.byteswap()
    return {'samples': len(samples), 'seconds': len(samples) / RATE,
            'peak': max(abs(s) for s in samples),
            'rms': math.sqrt(sum(s * s for s in samples) / len(samples)),
            'clipped_samples': sum(abs(s) >= 32760 for s in samples)}


def wav(raw):
    with wave.open(str(raw.with_suffix('.wav')), 'wb') as out:
        out.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        out.writeframes(raw.read_bytes())


def marker(path):
    # Three quiet 200 ms tones during a 12-second simultaneous capture/playback.
    samples = array('h', (int(2000 * math.sin(2 * math.pi * 660 * i / RATE))
                         if any(start <= i < start + RATE // 5
                                for start in (RATE, 5 * RATE, 9 * RATE)) else 0
                         for i in range(SECONDS * RATE)))
    if sys.byteorder != 'little':
        samples.byteswap()
    path.write_bytes(samples.tobytes())


def run_logged(args, output, name, timeout):
    environment = dict(os.environ, LC_ALL='C',
                       LD_LIBRARY_PATH='/opt/freedv-benchmark/lib:/usr/lib/pulseaudio')
    with (output / (name + '.log')).open('w') as log:
        result = subprocess.run([str(a) for a in args], stdout=log, stderr=subprocess.STDOUT,
                                env=environment, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError(f'{name} failed (exit {result.returncode}); inspect its log/report.')


def check_pcm(report, source, sink, expected_capture):
    if (report.get('schema') != 'x6200.usb-pcm.v1' or report.get('ok') is not True or
            report.get('dont_move') is not True or report.get('source') != source or
            report.get('sink') != sink or report.get('captured_bytes') != expected_capture or
            report.get('drained') is not True or
            any(report.get(k, 1) for k in ('holes', 'overflows', 'underflows'))):
        raise ValueError('PCM report failed binding, completeness, drain, or continuity checks.')


def check_engine(stats):
    if (stats.get('failures', 1) or stats.get('eoo_count') != 1 or
            any(stats.get(k, 0) < 1 for k in ('speech_frames', 'modem_blocks',
                                            'synchronized_blocks', 'synthesis_blocks'))):
        raise ValueError('Actual codec processing did not complete cleanly; inspect engine stats.')


def validate(output, helper):
    result = {'schema': 'x6200.usb-headset-validation.v1', 'status': 'BLOCKED',
              'rf_tested': False, 'hardware_qualified': False, 'live_codec_tested': False,
              'intelligibility': 'UNASSESSED', 'stages_completed': [],
              'scope': 'USB duplex PCM plus recorded speech through actual installed codec; no RF or PTT.'}
    try:
        if shutil.disk_usage(output).free < 32 * 1024 * 1024:
            raise ValueError('At least 32 MiB free space is required for retained evidence.')
        kit = json.loads((helper.parent / 'validation-manifest.json').read_text())
        if sha256(helper) != kit['pcm_helper_sha256']:
            raise ValueError('PCM helper does not match its packaged manifest.')
        if sha256(RUNNER) != RUNNER_SHA or sha256(RADE) != RADE_SHA:
            raise ValueError('Installed runner/library does not match the verified .2 image.')
        before = collect()
        save(output / 'inventory-before.json', before)
        source, sink = endpoints(before)
        result.update(source=source, sink=sink)
        result['stages_completed'].append('preflight')
        original = output / 'microphone-s16le.raw'
        tones = output / 'marker-s16le.raw'
        marker(tones)
        print('USB endpoints verified. Speak into the headset for 12 seconds after GO.\n'
              'Say: USB test. One two three four five. The quick brown fox jumps over the lazy dog. End of test.\n'
              'Listen for three quiet beeps while speaking. Leave every PTT released.', flush=True)
        for i in (3, 2, 1):
            print(i, flush=True)
            time.sleep(1)
        print('GO — speak now.', flush=True)
        run_logged([helper, source, sink, original, tones, output / 'duplex.json', SECONDS],
                   output, 'duplex', 45)
        check_pcm(json.loads((output / 'duplex.json').read_text()), source, sink, SECONDS * RATE * 2)
        result['capture'] = pcm_metrics(original)
        wav(original)
        if result['capture']['rms'] < 30:
            raise ValueError('Microphone capture is silent or extremely quiet; codec test withheld.')
        result['stages_completed'].append('usb_duplex_capture')
        print('Capture complete. Playing ORIGINAL microphone speech in the USB headset.', flush=True)
        run_logged([helper, '-', sink, '-', original, output / 'original-playback.json', 0],
                   output, 'original-playback', 40)
        check_pcm(json.loads((output / 'original-playback.json').read_text()), '-', sink, 0)
        result['stages_completed'].append('original_playback')
        # A file-based actual-codec round trip; deliberately not described as live.
        print('Running actual installed speech encoder, modem and decoder. Allow up to three minutes.', flush=True)
        codec = output / 'codec'
        codec.mkdir()
        speech_list = output / 'speech-list.txt'
        speech_list.write_text(str(original.resolve()) + '\n')
        run_logged([RUNNER, '--speech-list', speech_list, '--output', codec,
                    '--rate', RATE, '--turns', 1, '--seconds', SECONDS], output, 'codec', 180)
        stats = json.loads((codec / 'engine-stats.json').read_text())
        result['engine_stats'] = stats
        check_engine(stats)
        decoded = codec / 'turn-000-decoded-s16le.raw'
        result['decoded'] = pcm_metrics(decoded)
        wav(decoded)
        if result['decoded']['rms'] < 30:
            raise ValueError('Decoded output is silent or extremely quiet; inspect retained files.')
        result['stages_completed'].append('recorded_codec_roundtrip')
        print('Playing DECODED speech in the USB headset. Compare words, start and end with the original.', flush=True)
        run_logged([helper, '-', sink, '-', decoded, output / 'decoded-playback.json', 0],
                   output, 'decoded-playback', 45)
        check_pcm(json.loads((output / 'decoded-playback.json').read_text()), '-', sink, 0)
        result['stages_completed'].append('decoded_playback')
        after = collect()
        save(output / 'inventory-after.json', after)
        if endpoints(after) != (source, sink):
            raise ValueError('Endpoint identity changed during the campaign.')
        result['status'] = 'COMPLETED_LISTENING_REVIEW_REQUIRED'
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        result['reason'] = str(error)
        print('STOP: ' + str(error), flush=True)
    finally:
        save(output / 'validation.json', result)
        files = sorted(p for p in output.rglob('*') if p.is_file())
        (output / 'SHA256SUMS').write_text(''.join(sha256(p) + '  ' + p.relative_to(output).as_posix() + '\n' for p in files))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, exist_ok=False)
    result = validate(args.output.resolve(), Path(__file__).resolve().with_name('usb_headset_pcm'))
    print('Result: ' + result['status'], flush=True)
    return 0 if result['status'] == 'COMPLETED_LISTENING_REVIEW_REQUIRED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
