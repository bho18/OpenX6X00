#!/usr/bin/env python3
"""Three live half-duplex USB turns using the installed codec library; no RF/PTT."""
import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time
import wave

from usb_headset_inventory import collect, command, read_text, sha256
from usb_headset_validate import endpoints, RADE, RADE_SHA


def save(path, item):
    path.write_text(json.dumps(item, indent=2) + '\n', encoding='utf-8')


def check(report):
    if (report.get('schema') != 'x6200.usb-half-duplex.v1' or report.get('ok') is not True or
            report.get('client_rate') != 48000 or report.get('turns_completed') != 3 or
            report.get('eoo_count') != 3 or report.get('stream_creations') != 2 or
            any(report.get(k) != 1 for k in ('rade_creations', 'encoder_creations', 'decoder_creations')) or
            report.get('worker_creations') != 2 or report.get('failures', 1) or
            report.get('synthesis_blocks', 0) < 1 or report.get('rf_tested') is not False or
            report.get('ptt_tested') is not False or
            any(report.get(k, 1) for k in ('holes', 'capture_overflows'))):
        raise ValueError('Half-duplex engine, continuity, retained-resource or turn checks failed.')
    rate = report.get('read_rate_hz', 0)
    if report.get('timing_samples', 0) < 4 or not 47520 <= rate <= 48480:
        raise ValueError('Server playback pacing is missing or differs from 48 kHz by more than 1%.')


def stream_process(args, output, timeout=180):
    """Tee bounded live prompts/logs so speech is requested at the right time."""
    environment = dict(os.environ, LC_ALL='C', LD_LIBRARY_PATH='/opt/freedv-benchmark/lib:/usr/lib/pulseaudio')
    with (output / 'session.log').open('wb') as log:
        process = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=environment)
        total = 0
        deadline = time.monotonic() + timeout
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as poll:
                poll.register(process.stdout, selectors.EVENT_READ)
                while poll.get_map():
                    if time.monotonic() > deadline:
                        raise ValueError('Live session exceeded its 180-second bound.')
                    for key, _ in poll.select(.1):
                        data = os.read(key.fd, 65536)
                        if not data:
                            poll.unregister(key.fileobj)
                            continue
                        total += len(data)
                        if total > 1024 * 1024:
                            raise ValueError('Live session log exceeded 1 MiB bound.')
                        log.write(data)
                        print(data.decode('utf-8', errors='replace'), end='', flush=True)
            process.wait(timeout=max(.1, deadline - time.monotonic()))
            if process.returncode:
                raise ValueError(f'Live session stopped (exit {process.returncode}); inspect session.json and session.log.')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdout.close()


def active_inventory(output):
    # Fresh queries during a retained session also record the active hardware
    # view, unlike the pre/post snapshots of the earlier file replay test.
    save(output / 'pulse-active.json', {k: command(['pactl', 'list', k]) for k in ('sinks', 'sources', 'sink-inputs', 'source-outputs')})
    save(output / 'alsa-active.json', {str(p): read_text(p) for p in Path('/proc/asound').glob('card*/stream*')})


def validate(output, helper, run=stream_process):
    result = {'schema': 'x6200.usb-half-duplex-validation.v1', 'status': 'BLOCKED',
              'rf_tested': False, 'ptt_tested': False, 'hardware_qualified': False,
              'live_codec_tested': False, 'listening': 'UNASSESSED',
              'scope': 'Live USB mic encoding then paced file-channel decoding, alternating directions; no RF/PTT.'}
    try:
        if shutil.disk_usage(output).free < 48 * 1024 * 1024:
            raise ValueError('At least 48 MiB free space required.')
        manifest = json.loads((helper.parent / 'half-duplex-manifest.json').read_text())
        if sha256(helper) != manifest['helper_sha256'] or sha256(RADE) != RADE_SHA:
            raise ValueError('Helper or installed RADE library integrity mismatch.')
        before = collect()
        save(output / 'inventory-before.json', before)
        source, sink = endpoints(before)
        result.update(source=source, sink=sink)
        # The C helper retains both streams; query hardware while it is running.
        # A short separate observer cannot open audio or control the radio.
        import threading
        stop = threading.Event()
        def observe():
            try:
                for _ in range(150):
                    if stop.wait(.2):
                        return
                    trace = output / 'trace.jsonl'
                    if trace.exists() and trace.stat().st_size:
                        break
                else:
                    return
                if not stop.is_set():
                    active_inventory(output)
            except Exception as error:
                save(output / 'observer-error.json', {'reason': str(error)})
        observer = threading.Thread(target=observe, daemon=True)
        observer.start()
        try:
            run([helper, source, sink, output, 8, 3], output)
        finally:
            stop.set()
            observer.join(timeout=25)
        report = json.loads((output / 'session.json').read_text())
        result['session'] = report
        result['live_codec_tested'] = report.get('speech_frames', 0) > 0 or report.get('synthesis_blocks', 0) > 0
        check(report)
        after = collect()
        save(output / 'inventory-after.json', after)
        if endpoints(after) != (source, sink):
            raise ValueError('USB endpoint identity changed during the session.')
        result['status'] = ('COMPLETED_WITH_PLAYBACK_GAPS' if report['render_gap_callbacks'] or report['pa_underflows']
                            else 'COMPLETED_LISTENING_REVIEW_REQUIRED')
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        result['reason'] = str(error)
        print('STOP: ' + str(error), flush=True)
    finally:
        if (output / 'session.json').exists() and 'session' not in result:
            try:
                result['session'] = json.loads((output / 'session.json').read_text())
                result['live_codec_tested'] = (result['session'].get('speech_frames', 0) > 0 or
                                               result['session'].get('synthesis_blocks', 0) > 0)
            except (OSError, ValueError):
                pass
        for raw in output.glob('turn-*-*.raw'):
            try:
                if raw.stat().st_size % 2:
                    continue
                with wave.open(str(raw.with_suffix('.wav')), 'wb') as wav:
                    wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(raw.read_bytes())
            except OSError as error:
                result['wav_error'] = str(error)
        save(output / 'validation.json', result)
        files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(sha256(p) + '  ' + p.relative_to(output).as_posix() + '\n' for p in files))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, exist_ok=False)
    result = validate(args.output.resolve(), Path(__file__).resolve().with_name('usb_half_duplex'))
    print('Result: ' + result['status'], flush=True)
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
