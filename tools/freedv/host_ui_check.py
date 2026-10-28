#!/usr/bin/env python3
"""Exercise Settings in a freshly started host simulator using its local FIFOs.

Requires tesseract, Pillow and the simulator's local screen WebSocket on port 50002.
This is a host-only test; it must not be run against a physical radio.
"""
import argparse
import errno
import importlib.util
import json
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


def screen_text(client, image_path, panel=False):
    ws = client.WS()
    ws.s.settimeout(3)
    try:
        deadline = time.monotonic() + 5
        for _ in range(100):
            if time.monotonic() >= deadline:
                break
            opcode, payload = ws.frame()
            if opcode == 2 and len(payload) > 10:
                width, height, _ = struct.unpack('<HHI', payload[:8])
                if (width, height) == (800, 480) and payload[8:10] == b'\xff\xd8':
                    image_path.write_bytes(payload[8:])
                    ocr_path = image_path
                    options = []
                    button_text = []
                    env = dict(os.environ, OMP_THREAD_LIMIT='1')
                    if not panel:
                        # Segment Settings' fixed header, navigation and two
                        # content columns before OCR. Jammy's Tesseract 4
                        # otherwise merges highlighted labels with unrelated
                        # waterfall text and loses visible GENERAL/entry text.
                        # Check the same literal labels on the actual screen;
                        # neither input navigation nor expected text changes.
                        from PIL import Image, ImageOps
                        texts = []
                        with Image.open(image_path) as full:
                            regions = ((0, 60, 180, 116), (0, 118, 158, 416),
                                       (160, 118, 480, 416), (480, 118, 795, 416))
                            for i, box in enumerate(regions):
                                crop = full.crop(box).convert('L')
                                crop = ImageOps.autocontrast(crop.resize((crop.width * 3, crop.height * 3)))
                                crop_path = image_path.with_name(image_path.stem + f'.region{i}.png')
                                crop.save(crop_path)
                                result = subprocess.run(['tesseract', str(crop_path), 'stdout', '--psm', '6'],
                                                        capture_output=True, text=True, check=True, env=env, timeout=5)
                                texts.append(result.stdout)
                        return '\n'.join(texts).upper()
                    if panel:
                        # The diagnostic panel uses14px text beside the normal
                        # waterfall. Isolate and enlarge it; no label substitutions.
                        from PIL import Image, ImageOps
                        with Image.open(image_path) as full:
                            crop = full.crop((405, 20, 795, 460))
                            crop = crop.resize((crop.width * 3, crop.height * 3))
                            ocr_path = image_path.with_suffix('.ocr.png')
                            crop.save(ocr_path)
                            # White button text needs separate segmentation
                            # from the black-on-white status. Single-word OCR
                            # resolves Sony14's q correctly without fuzzy labels.
                            for i in range(6):
                                x, y = 415 + (i % 3) * 125, 305 + (i // 3) * 38
                                button = full.crop((x + 8, y + 3, x + 112, y + 30)).convert('L')
                                button = ImageOps.autocontrast(ImageOps.invert(button.resize((624, 162))))
                                button_path = image_path.with_name(image_path.stem + f'.button{i}.png')
                                button.save(button_path)
                                word = subprocess.run(['tesseract', str(button_path), 'stdout', '--psm', '8'],
                                                      capture_output=True, text=True, check=True, env=env, timeout=5)
                                button_text.append(word.stdout)
                        options = ['--psm', '6']
                    result = subprocess.run(['tesseract', str(ocr_path), 'stdout', *options],
                                            capture_output=True, text=True, check=True, env=env, timeout=5)
                    return (result.stdout + '\n' + '\n'.join(button_text)).upper()
        raise RuntimeError('no simulator screen frame')
    finally:
        ws.s.close()


def wait_for(client, image_path, expected, absent=(), panel=False):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        text = screen_text(client, image_path, panel=panel)
        print(f'Looking for {expected}: {text!r}', flush=True)
        if all(value in text for value in expected) and not any(value in text for value in absent):
            return text
        time.sleep(0.4)
    raise RuntimeError(f'simulator screen did not show {expected}: {text}')


def idle_state(client):
    ws = client.WS()
    ws.s.settimeout(3)
    try:
        ws.text('panel 1')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            opcode, payload = ws.frame()
            if opcode == 1 and payload.startswith(b'state '):
                state = json.loads(payload[6:])
                if state['tx']:
                    raise RuntimeError('host UI navigation unexpectedly entered TX')
                return state
        raise RuntimeError('host simulator did not report its idle radio state')
    finally:
        ws.s.close()


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
    parser.add_argument('--data', type=Path, help='host data directory (default: HOST_DATA_DIR in CMakeCache.txt)')
    parser.add_argument('--output', type=Path, help='retain screenshots and check summary in this directory')
    parser.add_argument('--snapshot', action='store_true', help='open and check the opt-in diagnostic panel using local MFK input')
    args = parser.parse_args()
    cache = args.build.resolve() / 'CMakeCache.txt'
    values = dict(line.split('=', 1) for line in cache.read_text().splitlines()
                  if '=' in line and not line.startswith(('#', '//')))
    if values.get('HOST_BUILD:BOOL') != 'ON':
        parser.error('UI checks require a host simulator build, never a physical radio')
    data = args.data.resolve() if args.data else Path(values.get('HOST_DATA_DIR:PATH', str(args.build.resolve() / 'data')))
    run = data / 'run'
    if not (run / 'keypad').is_fifo() or not (run / 'mfk').is_fifo():
        parser.error('expected host-simulator FIFOs; start a fresh host build first')
    drive = load('host_test_drive', ROOT / 'tools/screenshots/x6drive.py')
    drive.emit = emit_fifo
    drive.KEYPAD = str(run / 'keypad')
    drive.KNOBS['mfk'] = (str(run / 'mfk'), 2, -1)
    client = load('host_test_ws', ROOT / 'tools/tci/tci_client_test.py')
    client.PORT = 50002
    with tempfile.TemporaryDirectory(prefix='freedv-ui-check-') as temporary:
        output = args.output.resolve() if args.output else Path(temporary)
        if args.output:
            output.mkdir(parents=True, exist_ok=False)
        image_path = output / 'settings.jpg'
        states = [idle_state(client)]
        observations = ['settings opened by local front keys']
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
            # Info's focusable controls are Check, Update, Reset, diagnostics.
            # Clamp to its first item (wrap is disabled), then reach the entry
            # through the SAME encoder group used by the radio's physical MFK.
            # The only click here is the diagnostic entry; no other Info action.
            drive.turn('mfk', -20)
            drive.turn('mfk', 3)
            image_path = output / 'diagnostic-entry.jpg'
            wait_for(client, image_path, ('OPEN FREEDV DIAGNOSTICS',))
            drive.key('MFK', 100)
            image_path = output / 'diagnostic-panel.jpg'
            panel_text = wait_for(client, image_path,
                                  ('FREEDV DIAGNOSTICS', 'IDLE', 'ACQUIRE', 'ARM', 'RUN', 'STOP'), panel=True)
            if not ('PACKET UNKNOWN' in panel_text or
                    ('CMD R' in panel_text and 'RX #' in panel_text and 'AGE' in panel_text)):
                raise RuntimeError('diagnostic panel did not distinguish packet status: ' + panel_text)
            states.append(idle_state(client))
            observations += ['diagnostic entry focused and clicked by local MFK',
                             'Acquire Arm Run Stop controls visible', 'idle packet status visible']
            print('PASS: local MFK opened diagnostics with idle packet status and Acquire/Arm/Run/Stop controls')
            # The initial focus is the case selector. One reverse detent wraps
            # to Close; never focus/click Acquire, Arm or Run during this check.
            drive.turn('mfk', -1)
            drive.key('MFK', 100)
            wait_for(client, output / 'diagnostic-closed.jpg', (),
                     ('FREEDV DIAGNOSTICS', 'CAPTURE 10S', 'ACQUIRE'), panel=True)
            states.append(idle_state(client))
            observations.append('local MFK Close returned to ordinary screen')
            print('PASS: local MFK closed diagnostics safely without acquiring, arming or running a case')
        (output / 'summary.json').write_text(json.dumps({
            'status': 'PASS', 'host_simulator_only': True, 'diagnostic_panel': args.snapshot,
            'input_transport': 'local evdev-format FIFOs', 'data_directory': str(data),
            'acquire_arm_run_actions': 0, 'idle_radio_states': states, 'observations': observations,
        }, indent=2) + '\n')


if __name__ == '__main__':
    main()
