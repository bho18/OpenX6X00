#!/usr/bin/env python3
"""Consume a private Pulse FIFO at monotonic sample deadlines.

This is a paced FIFO backend model, not physical hardware. Read allowance is
clocked; data already queued in the FIFO is never read ahead of its deadline.
The raw output retains every received byte, including an incomplete final frame
on failure. No child processes are spawned by the clock consumer.
"""
import argparse
from contextlib import ExitStack
import fcntl
import json
import math
import os
from pathlib import Path
import select
import signal
import stat
import time

NS = 1_000_000_000
SCOPE = 'paced FIFO backend model; not physical hardware'


def consume(fifo, output, timing, rate, channels, width, period_ms=10,
            startup_timeout=5.0, no_data_timeout=2.0, max_seconds=300.0):
    if not (1 <= rate <= 384000 and 1 <= channels <= 8 and width in (2, 3) and 1 <= period_ms <= 100):
        raise ValueError('Invalid bounded FIFO audio format or clock period')
    if not all(math.isfinite(value) and value > 0
               for value in (startup_timeout, no_data_timeout, max_seconds)):
        raise ValueError('FIFO time limits must be positive')
    fifo, output, timing = Path(fifo), Path(output), Path(timing)
    if len({str(path.absolute()) for path in (fifo, output, timing)}) != 3:
        raise ValueError('FIFO, raw output and timing paths must be distinct')
    frame_bytes = channels * width
    start = time.monotonic_ns()
    stop_signal = 0
    old_handlers = {}
    session = {'kind': 'session', 'schema': 'x6200.usb.fifo-clock.v1', 'scope': SCOPE,
               'rate_hz': rate, 'channels': channels, 'sample_width_bytes': width,
               'frame_bytes': frame_bytes, 'period_ms': period_ms, 'started_ns': start,
               'frame_interval': 'zero-based, end-exclusive', 'clock_origin_ns': None}

    def stop(signum, unused):
        nonlocal stop_signal
        del unused
        stop_signal = signum

    for number in (signal.SIGTERM, signal.SIGINT):
        old_handlers[number] = signal.signal(number, stop)

    try:
        with ExitStack() as stack:
            raw = stack.enter_context(output.open('xb'))
            log = stack.enter_context(timing.open('x', buffering=1))
            return _consume_opened(
                fifo, raw, log, rate, frame_bytes, period_ms, start, session,
                startup_timeout, no_data_timeout, max_seconds, lambda: stop_signal)
    finally:
        for number, previous in old_handlers.items():
            signal.signal(number, previous)


def _consume_opened(fifo, raw, log, rate, frame_bytes, period_ms, start, session,
                    startup_timeout, no_data_timeout, max_seconds, stopped):
    descriptor = None
    frames = bytes_read = block_count = 0
    pending = b''
    origin = None
    header_written = False
    reason, status, exit_code = 'unstarted', 'FAIL', 1

    def record(value):
        log.write(json.dumps(value, separators=(',', ':')) + '\n')

    try:
        startup_deadline = start + round(startup_timeout * NS)
        while descriptor is None and not stopped():
            if time.monotonic_ns() >= startup_deadline:
                raise TimeoutError('missing-fifo-startup-timeout')
            try:
                mode = fifo.lstat().st_mode
                if not stat.S_ISFIFO(mode):
                    raise ValueError('Input path is not a real FIFO')
                descriptor = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
                if not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
                    raise ValueError('Input path changed away from FIFO')
            except FileNotFoundError:
                time.sleep(.005)
        if descriptor is None:
            reason, status, exit_code = 'signal-' + str(stopped()), 'STOPPED', 0
            return exit_code
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
        session['fifo_opened_ns'] = time.monotonic_ns()
        session['fifo_capacity_bytes'] = fcntl.fcntl(descriptor, fcntl.F_GETPIPE_SZ)
        # Polling readiness establishes the clock origin without consuming
        # even a single future sample for bootstrap.
        while not stopped() and origin is None:
            if time.monotonic_ns() >= startup_deadline:
                raise TimeoutError('first-data-startup-timeout')
            events = poller.poll(10)
            if any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
                raise OSError('FIFO reported a startup error')
            if any(mask & select.POLLIN for _, mask in events):
                origin = time.monotonic_ns()
                break
            if events:  # HUP before a writer appears must not busy-spin.
                time.sleep(.005)
        session['clock_origin_ns'] = origin
        record(session)
        header_written = True
        period_ns = period_ms * 1_000_000
        period_index = 1
        last_data = origin or time.monotonic_ns()
        finished = False
        while not stopped() and not finished:
            now = time.monotonic_ns()
            if now - start >= round(max_seconds * NS):
                raise TimeoutError('FIFO-runtime-deadline')
            deadline = origin + period_index * period_ns
            if now < deadline:
                time.sleep(min((deadline - now) / NS, .01))
                continue
            target_frames = rate * period_index * period_ns // NS
            frame_start = frames
            before_ns = after_ns = None
            first_nonzero = last_nonzero = None
            block_bytes = 0
            try:
                while frames < target_frames and not stopped():
                    now = time.monotonic_ns()
                    if now - start >= round(max_seconds * NS):
                        raise TimeoutError('FIFO-runtime-deadline')
                    if now - last_data >= round(no_data_timeout * NS):
                        raise TimeoutError('FIFO-no-data-timeout')
                    allowed = (target_frames - frames) * frame_bytes - len(pending)
                    before = time.monotonic_ns()
                    try:
                        payload = os.read(descriptor, min(65536, allowed))
                    except BlockingIOError:
                        poller.poll(10)
                        continue
                    after = time.monotonic_ns()
                    if before_ns is None:
                        before_ns = before
                    after_ns = after
                    if not payload:
                        reason = 'partial-frame-at-eof' if pending else 'eof'
                        status, exit_code = ('FAIL', 1) if pending else ('COMPLETE', 0)
                        finished = True
                        break
                    last_data = after
                    raw.write(payload)
                    bytes_read += len(payload)
                    block_bytes += len(payload)
                    joined = pending + payload
                    completed = len(joined) // frame_bytes
                    for index in range(completed):
                        if any(joined[index * frame_bytes:(index + 1) * frame_bytes]):
                            absolute = frames + index
                            if first_nonzero is None:
                                first_nonzero = absolute
                            last_nonzero = absolute
                    frames += completed
                    pending = joined[completed * frame_bytes:]
            finally:
                if block_bytes or frames != frame_start:
                    raw.flush()
                    scheduled_last = origin + (frames - 1) * NS // rate if frames else None
                    record({'kind': 'block', 'block': block_count, 'period': period_index,
                            'frame_start': frame_start, 'frame_end': frames, 'bytes_read': block_bytes,
                            'partial_frame_bytes': len(pending), 'before_ns': before_ns, 'after_ns': after_ns,
                            'scheduled_last_ns': scheduled_last,
                            'lateness_ns': max(0, after_ns - scheduled_last) if scheduled_last is not None else None,
                            'first_nonzero_frame': first_nonzero, 'last_nonzero_frame': last_nonzero})
                    block_count += 1
            period_index += 1
        if stopped():
            reason, status, exit_code = 'signal-' + str(stopped()), 'STOPPED', 0
    except (OSError, ValueError, TimeoutError) as error:
        reason, status, exit_code = str(error), 'FAIL', 1
    finally:
        if descriptor is not None:
            os.close(descriptor)
        raw.flush()
        if not header_written:
            record(session)
        record({'kind': 'terminal', 'scope': SCOPE, 'status': status, 'reason': reason,
                'frames': frames, 'raw_bytes': bytes_read, 'partial_frame_bytes': len(pending),
                'blocks': block_count, 'clock_origin_ns': origin, 'finished_ns': time.monotonic_ns(),
                'exit_code': exit_code})
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fifo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timing', type=Path, required=True)
    parser.add_argument('--rate', type=int, required=True)
    parser.add_argument('--channels', type=int, required=True)
    parser.add_argument('--width', type=int, choices=(2, 3), required=True)
    parser.add_argument('--period-ms', type=int, default=10)
    parser.add_argument('--startup-timeout', type=float, default=5)
    parser.add_argument('--no-data-timeout', type=float, default=2)
    parser.add_argument('--max-seconds', type=float, default=300)
    args = parser.parse_args()
    return consume(args.fifo, args.output, args.timing, args.rate, args.channels, args.width,
                   args.period_ms, args.startup_timeout, args.no_data_timeout, args.max_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
