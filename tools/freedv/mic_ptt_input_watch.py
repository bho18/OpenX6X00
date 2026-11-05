#!/usr/bin/env python3
"""Observe evdev without grabbing devices or issuing radio control commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import struct
import time

EVENT = struct.Struct('@llHHi')  # Native Linux input_event, including 32-bit ARM longs.
EXPECTED_GUI = '5fff9870e32f4ce10d4fe84982f2c1b7b40d3461085acb387859a0e8947caf76'
SCHEDULE = [(0, 'BASELINE: leave both PTT controls released'),
            (5, 'PANEL: press radio side PTT for ONE second, then release'),
            (15, 'MIC FIRST: press microphone PTT for ONE second, then release'),
            (25, 'MIC SECOND: press microphone PTT for ONE second, then release'),
            (35, 'FINISH: leave both PTT controls released')]
WIDE_SCHEDULE = [(0, 'BASELINE: leave all microphone buttons and both PTT controls released'),
                 (5, 'MIC UP: briefly tap the hand-microphone UP button once'),
                 (12, 'MIC DOWN: briefly tap the hand-microphone DOWN button once'),
                 (20, 'PANEL: press radio side PTT for ONE second, then release'),
                 (30, 'MIC FIRST: press microphone PTT for ONE second, then release'),
                 (40, 'MIC SECOND: press microphone PTT for ONE second, then release'),
                 (50, 'FINISH: all buttons and PTT controls released')]


class EventDecoder:
    def __init__(self):
        self.pending = b''

    def feed(self, data):
        self.pending += data
        result = []
        while len(self.pending) >= EVENT.size:
            sec, usec, kind, code, value = EVENT.unpack(self.pending[:EVENT.size])
            self.pending = self.pending[EVENT.size:]
            result.append({'kernel_seconds': sec, 'kernel_microseconds': usec,
                           'type': kind, 'code': code, 'value': value})
        return result


def observe(devices, out, seconds=40, packet_reader=None):
    started = time.monotonic()
    counts = {name: 0 for name, _ in devices}
    issues = []
    steps = iter(WIDE_SCHEDULE if packet_reader else SCHEDULE)
    step = next(steps, None)
    decoders = {fd: EventDecoder() for _, fd in devices}
    rows = 0
    sampled = unstable = 0
    last_gpio = None
    next_sample = next_gpio = started
    gpio_reader = None
    if packet_reader:
        from mic_status_snapshot import existing_gpio_values
        gpio_reader = existing_gpio_values
    with selectors.DefaultSelector() as poll, (out / 'events.jsonl').open('x') as trace, \
            (out / 'status-samples.jsonl').open('x') as status_trace:
        for name, fd in devices:
            poll.register(fd, selectors.EVENT_READ, name)
        print('READY: observation started; GUI remains active. Follow the prompts.', flush=True)
        while time.monotonic() - started < seconds:
            elapsed = time.monotonic() - started
            if packet_reader and time.monotonic() >= next_sample:
                next_sample = time.monotonic() + .02
                try:
                    sample = packet_reader.sample()
                except (OSError, ValueError) as e:
                    issues.append('GUI packet sampling stopped: ' + str(e))
                    break
                sample.update(kind='gui_packet_sample', observed_elapsed_s=time.monotonic() - started)
                status_trace.write(json.dumps(sample) + '\n')
                sampled += 1
                unstable += not sample['repeat_read_equal']
                if status_trace.tell() > 10 * 1024 * 1024:
                    issues.append('Status evidence size limit reached')
                    break
            if gpio_reader and time.monotonic() >= next_gpio:
                next_gpio = time.monotonic() + .1
                gpio = gpio_reader()
                if gpio != last_gpio:
                    trace.write(json.dumps({'kind': 'existing_linux_gpio_values',
                                            'observed_elapsed_s': elapsed, 'values': gpio}) + '\n')
                    last_gpio = gpio
            while step is not None and elapsed >= step[0]:
                print(step[1], flush=True)
                trace.write(json.dumps({'kind': 'operator_prompt', 'elapsed_s': elapsed,
                                        'text': step[1]}) + '\n')
                trace.flush()
                step = next(steps, None)
            for key, _ in poll.select(.01 if packet_reader else .1):
                try:
                    data = os.read(key.fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError as e:
                    issues.append(key.data + ': ' + str(e))
                    poll.unregister(key.fd)
                    continue
                if not data:
                    issues.append(key.data + ': device stream ended')
                    poll.unregister(key.fd)
                    continue
                for event in decoders[key.fd].feed(data):
                    event.update(kind='input_event', device=key.data,
                                 observed_elapsed_s=time.monotonic() - started)
                    trace.write(json.dumps(event) + '\n')
                    counts[key.data] += 1
                    rows += 1
                    if event['type'] == 0 and event['code'] == 3:
                        issues.append(key.data + ': SYN_DROPPED; event history incomplete')
                    if rows >= 20000:
                        issues.append('Event limit reached; observation stopped early')
                        break
                trace.flush()
                if rows >= 20000:
                    break
            if rows >= 20000:
                break
    for fd, decoder in decoders.items():
        if decoder.pending:
            issues.append('Partial input_event retained at stream end: fd ' + str(fd))
    return {'status': 'INCOMPLETE' if issues else 'OBSERVED', 'issues': issues,
            'elapsed_s': time.monotonic() - started, 'events_by_device': counts,
            'event_size_bytes': EVENT.size, 'mic_ptt_mapping': 'UNASSESSED',
            'gui_packet_samples': sampled, 'unequal_repeat_read_samples': unstable,
            'sampling_limits': 'Sampled memory can miss short events, repeat stale packets or contain mixed updates; not every UART packet',
            'negative_finding_scope': 'No events cannot exclude an input outside these evdev devices',
            'aggregate_tx': 'Sampled MCU packet flag; not raw mic PTT' if packet_reader else 'Operator GUI observation only; no automated aggregate TX capture',
            'release_while_software_tx_held': 'NOT_TESTED', 'hardware_qualified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--wide', action='store_true', help='Also sample pinned GUI packet buffer and existing exported GPIO values')
    args = parser.parse_args()
    devices, inventory = [], []
    packet_reader = None
    out = args.output
    try:
        gui = Path('/usr/sbin/x6100_gui')
        if hashlib.sha256(gui.read_bytes()).hexdigest() != EXPECTED_GUI:
            raise ValueError('Installed GUI is not the verified .2 candidate')
        paths = sorted(Path('/dev/input').glob('event*'))
        if not paths:
            raise ValueError('No evdev devices found')
        for path in paths:
            # A separate read-only descriptor receives its own event queue.
            # No EVIOCGRAB, input injection, I2C or radio API operations.
            fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
            devices.append((str(path), fd))
            name = Path('/sys/class/input', path.name, 'device/name')
            inventory.append({'path': str(path), 'name': name.read_text().strip()})
        if args.wide:
            from mic_status_snapshot import open_verified_gui
            packet_reader = open_verified_gui()
        if args.check_only:
            print(json.dumps({'status': 'READY', 'devices': inventory,
                              'gui_packet_sampling': packet_reader is not None,
                              'note': 'Check only. No press test has started.'}, indent=2))
            return 0
        if out is None or not out.is_absolute() or out.parent != Path('/tmp'):
            raise ValueError('Provide a fresh absolute --output directory directly under /tmp')
        out.mkdir(mode=0o700)
        (out / 'identity.json').write_text(json.dumps({
            'gui_sha256': EXPECTED_GUI, 'devices': inventory, 'schedule': WIDE_SCHEDULE if args.wide else SCHEDULE,
            'scope': 'evdev plus sampled GUI status and existing exported GPIO' if args.wide else 'evdev only',
            'radio_control_commands': False,
            'wall_time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, indent=2) + '\n')
        result = observe(devices, out, seconds=55 if args.wide else 40, packet_reader=packet_reader)
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
        return 0 if result['status'] == 'OBSERVED' else 1
    except (OSError, ValueError) as e:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(e)}), flush=True)
        return 1
    finally:
        if packet_reader is not None:
            packet_reader.close()
        for _, fd in devices:
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
