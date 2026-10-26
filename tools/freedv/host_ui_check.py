#!/usr/bin/env python3
"""Exercise Settings in a freshly started host simulator using its local FIFOs.

Requires tesseract and the simulator's local screen WebSocket on port 50002.
This is a host-only test; it must not be run against a physical radio.
"""
import argparse
import errno
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def screen_text(client, image_path):
    ws = client.WS()
    ws.s.settimeout(3)
    try:
        for _ in range(100):
            opcode, payload = ws.frame()
            if opcode == 2 and len(payload) > 10:
                width, height, _ = struct.unpack('<HHI', payload[:8])
                if (width, height) == (800, 480) and payload[8:10] == b'\xff\xd8':
                    image_path.write_bytes(payload[8:])
                    env = dict(os.environ, OMP_THREAD_LIMIT='1')
                    result = subprocess.run(['tesseract', str(image_path), 'stdout'],
                                            capture_output=True, text=True, check=True, env=env)
                    return result.stdout.upper()
        raise RuntimeError('no simulator screen frame')
    finally:
        ws.s.close()


def wait_for(client, image_path, expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        text = screen_text(client, image_path)
        if all(value in text for value in expected):
            return
        time.sleep(0.4)
    raise RuntimeError(f'simulator screen did not show {expected}: {text}')


def emit_fifo(path, events):
    """Do not hang indefinitely if the simulator no longer reads its FIFO."""
    deadline = time.monotonic() + 10
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError as exc:
            if exc.errno != errno.ENXIO or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    try:
        payload = b''.join(struct.pack('llHHi', 0, 0, kind, code, value) +
                           struct.pack('llHHi', 0, 0, 0, 0, 0)
                           for kind, code, value in events)
        if os.write(fd, payload) != len(payload):
            raise RuntimeError(f'short write to simulator FIFO {path}')
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=ROOT / 'build-host')
    parser.add_argument('--snapshot', action='store_true', help='also check the opt-in diagnostic snapshot')
    args = parser.parse_args()
    run = args.build.resolve() / 'data/run'
    if not (run / 'keypad').is_fifo() or not (run / 'mfk').is_fifo():
        parser.error('expected host-simulator FIFOs; start a fresh host build first')
    drive = load('host_test_drive', ROOT / 'tools/screenshots/x6drive.py')
    drive.emit = emit_fifo
    drive.KEYPAD = str(run / 'keypad')
    drive.KNOBS['mfk'] = (str(run / 'mfk'), 2, -1)
    client = load('host_test_ws', ROOT / 'tools/tci/tci_client_test.py')
    client.PORT = 50002
    with tempfile.TemporaryDirectory(prefix='freedv-ui-check-') as temporary:
        image_path = Path(temporary) / 'screen.jpg'
        for key in ('APP', 'F1', 'F2'):
            drive.key(key, 100)
            time.sleep(0.7)
        wait_for(client, image_path, ('SETTINGS', 'GENERAL'))
        print('PASS: Settings opened in the host simulator')
        if args.snapshot:
            for key in ('F1', 'F1'):
                drive.key(key, 100)
                time.sleep(0.7)
            wait_for(client, image_path, ('FIRMWARE', 'BASEBAND'))
            # Focus can wrap when there are more or fewer Info rows. Advance in
            # small steps and inspect each screen rather than assuming a fixed
            # number of detents lands on the diagnostic card.
            for _ in range(12):
                drive.turn('mfk', 2)
                time.sleep(0.3)
                text = screen_text(client, image_path)
                if 'CACHED' in text and 'LAST RADIO STATE' in text:
                    break
            else:
                raise RuntimeError(f'diagnostic snapshot not visible: {text}')
            print('PASS: diagnostic snapshot displayed cached commands and last radio state')


if __name__ == '__main__':
    main()
