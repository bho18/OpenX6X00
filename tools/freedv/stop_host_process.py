#!/usr/bin/env python3
"""Bounded shutdown of a direct child owned by the calling Linux CI shell.

Only call shell `wait` after this helper succeeds: a pidfd then proves that the
original process has exited. The pidfd prevents signaling a reused PID.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import time


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return None
    return int(fields[1]), int(fields[19])  # parent PID, start ticks


def stop(pid, term_seconds, kill_seconds):
    if pid <= 1:
        raise ValueError('expected a direct child PID greater than one')
    before = identity(pid)
    if before is None:
        return 'already-gone'
    if before[0] != os.getppid():
        raise ValueError('refusing to signal a process not owned by the calling shell')
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return 'already-gone'
    try:
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        if poll.poll(0):
            return 'already-exited'
        if identity(pid) != before:
            raise ValueError('process identity changed before shutdown')
        for sig, seconds, label in ((signal.SIGTERM, term_seconds, 'term'),
                                    (signal.SIGKILL, kill_seconds, 'kill')):
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                return 'already-exited'
            if poll.poll(int(seconds * 1000)):
                return label
        raise TimeoutError('child remained alive after bounded TERM and KILL waits')
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid', type=int)
    parser.add_argument('--term-seconds', type=float, default=5)
    parser.add_argument('--kill-seconds', type=float, default=5)
    args = parser.parse_args()
    if not (0 < args.term_seconds <= 30 and 0 < args.kill_seconds <= 30):
        parser.error('shutdown intervals must be positive and at most 30 seconds')
    start = time.monotonic()
    result = stop(args.pid, args.term_seconds, args.kill_seconds)
    print(json.dumps({'pid': args.pid, 'shutdown': result,
                      'elapsed_seconds': round(time.monotonic() - start, 3)}), flush=True)


if __name__ == '__main__':
    main()
