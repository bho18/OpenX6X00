#!/usr/bin/env python3
"""Read USB/ALSA/Pulse topology only; never opens PCM or controls the radio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import selectors
import subprocess
import time


EXPECTED_GUI = '5fff9870e32f4ce10d4fe84982f2c1b7b40d3461085acb387859a0e8947caf76'
COMMAND_LIMIT = 1024 * 1024
TEXT_LIMIT = 128 * 1024


def read_text(path, limit=TEXT_LIMIT):
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(limit + 1)
        return {'text': data[:limit].decode('utf-8', errors='replace'),
                'truncated': len(data) > limit, 'error': None}
    except OSError as error:
        return {'text': '', 'truncated': False, 'error': str(error)}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def command(args, timeout=5, limit=COMMAND_LIMIT):
    """Bound both output and elapsed time, including commands that fail."""
    started = time.monotonic()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    timed_out = truncated = False
    environment = dict(os.environ, LC_ALL='C', LANG='C')
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment)
    except OSError as error:
        return {'command': args, 'exit_code': None, 'stdout': '', 'stderr': str(error),
                'timed_out': False, 'output_truncated': False}
    try:
        with selectors.DefaultSelector() as poll:
            for name in output:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                poll.register(stream, selectors.EVENT_READ, name)
            while poll.get_map():
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in poll.select(min(.1, remaining)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        poll.unregister(key.fileobj)
                        continue
                    available = limit - sum(len(value) for value in output.values())
                    output[key.data].extend(data[:max(0, available)])
                    if len(data) > available:
                        truncated = True
                        break
                if truncated:
                    break
            if not timed_out and not truncated:
                remaining = timeout - (time.monotonic() - started)
                try:
                    process.wait(timeout=max(.001, remaining))
                except subprocess.TimeoutExpired:
                    timed_out = True
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
    return {'command': args, 'exit_code': process.returncode,
            'stdout': output['stdout'].decode('utf-8', errors='replace'),
            'stderr': output['stderr'].decode('utf-8', errors='replace'),
            'timed_out': timed_out, 'output_truncated': truncated}


def parse_alsa_cards(text):
    return [{'number': int(match[1]), 'id': match[2].strip(), 'description': match[3].strip()}
            for match in re.finditer(r'^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*(.*)$', text, re.M)]


def parse_pcm(text):
    result = []
    for line in text.splitlines():
        match = re.match(r'\s*(\d+)-(\d+):\s*(.*)', line)
        if not match:
            continue
        playback = re.search(r'\bplayback\s+(\d+)\b', match[3])
        capture = re.search(r'\bcapture\s+(\d+)\b', match[3])
        result.append({'card': int(match[1]), 'device': int(match[2]), 'description': match[3],
                       'playback_substreams': int(playback[1]) if playback else 0,
                       'capture_substreams': int(capture[1]) if capture else 0})
    return result


def usb_ancestor(card_device, sysroot=Path('/sys'), maximum=24):
    """Find the closest real USB device; ALSA/Pulse names alone are insufficient."""
    try:
        target = Path(card_device).resolve(strict=True)
        boundary = Path(sysroot).resolve(strict=True)
        if not target.is_relative_to(boundary):
            return None
        for depth in range(maximum):
            vendor = read_text(target / 'idVendor', 64)
            product = read_text(target / 'idProduct', 64)
            if (not vendor['error'] and not product['error'] and
                    re.fullmatch(r'[0-9a-fA-F]{4}', vendor['text'].strip()) and
                    re.fullmatch(r'[0-9a-fA-F]{4}', product['text'].strip())):
                return {'path': str(target), 'id_vendor': vendor['text'].strip().lower(),
                        'id_product': product['text'].strip().lower(),
                        'product': read_text(target / 'product', 512)['text'].strip(),
                        'manufacturer': read_text(target / 'manufacturer', 512)['text'].strip(),
                        'ancestor_depth': depth}
            if target == boundary:
                break
            target = target.parent
    except (OSError, RuntimeError):
        return None
    return None


def parse_pulse(text, kind):
    result = []
    chunks = re.split(r'(?m)^' + re.escape(kind) + r' #\d+\s*$', text)
    for chunk in chunks[1:]:
        fields = dict(re.findall(r'^\s*([^\n:=]+):\s*([^\n]*)$', chunk, re.M))
        fields = {key.strip(): value.strip() for key, value in fields.items()}
        properties = {}
        for match in re.finditer(r'^\s*([\w.-]+)\s*=\s*(?:"([^"\n]*)"|([^\n]*))$', chunk, re.M):
            properties[match[1]] = match[2] if match[2] is not None else match[3].strip()
        card = properties.get('alsa.card', '')
        monitor_of = fields.get('Monitor of Sink', '')
        monitor = (kind == 'Source' and (
            fields.get('Name', '').endswith('.monitor') or
            properties.get('device.class') == 'monitor' or
            monitor_of.lower() not in ('', 'n/a', 'none')))
        # Remap/virtual sources can inherit their master's ALSA properties.
        # Require the owning ALSA module, not just device.api/alsa.card tags.
        alsa_driver = fields.get('Driver', '').startswith('module-alsa-')
        physical = (bool(fields.get('Name', '').strip()) and alsa_driver and
                    properties.get('device.class', 'sound') == 'sound' and not monitor)
        result.append({'name': fields.get('Name'), 'alsa_card': int(card) if card.isdigit() else None,
                       'monitor': monitor, 'physical_alsa': physical,
                       'sample_specification': fields.get('Sample Specification'),
                       'channel_map': fields.get('Channel Map'), 'mute': fields.get('Mute'),
                       'state': fields.get('State'), 'properties': properties})
    return result


def assess(cards, pcms, sources, sinks):
    usb_cards = []
    for original in cards:
        if not original.get('usb'):
            continue
        card = dict(original)
        number = card['number']
        card_pcms = [pcm for pcm in pcms if pcm['card'] == number]
        capture = sum(pcm['capture_substreams'] for pcm in card_pcms)
        playback = sum(pcm['playback_substreams'] for pcm in card_pcms)
        physical_sources = [s for s in sources if s['alsa_card'] == number and s['physical_alsa']]
        physical_sinks = [s for s in sinks if s['alsa_card'] == number and s['physical_alsa']]
        monitors = [s['name'] for s in sources if s['alsa_card'] == number and s['monitor']]
        limitations = []
        if not capture:
            limitations.append('No ALSA capture PCM was listed for this USB card; a monitor source is not a microphone.')
        elif not physical_sources:
            limitations.append('ALSA capture exists but no active physical Pulse source was found; inspect the card profile before concluding hardware lacks capture.')
        if not playback:
            limitations.append('No ALSA playback PCM was listed for this USB card.')
        elif not physical_sinks:
            limitations.append('ALSA playback exists but no active physical Pulse sink was found; inspect the card profile.')
        candidate = bool(capture and playback and physical_sources and physical_sinks)
        card.update({'alsa_capture_substreams': capture, 'alsa_playback_substreams': playback,
                     'pcm_devices': card_pcms, 'pulse_capture_sources': physical_sources,
                     'pulse_playback_sinks': physical_sinks, 'monitor_sources_excluded': monitors,
                     'status': 'USB_DUPLEX_CANDIDATE' if candidate else 'USB_AUDIO_INCOMPLETE',
                     'limitations': limitations + ['Enumeration does not verify concurrent audio, microphone activity, listening, latency or RF behavior.']})
        usb_cards.append(card)
    status = ('NO_USB_AUDIO_DEVICE' if not usb_cards else
              'USB_DUPLEX_CANDIDATE' if any(c['status'] == 'USB_DUPLEX_CANDIDATE' for c in usb_cards)
              else 'USB_AUDIO_INCOMPLETE')
    return {'status': status, 'usb_audio_cards': usb_cards,
            'hardware_qualified': False, 'rf_tested': False,
            'duplex_streams_opened': False, 'routes_changed': False}


def usb_devices(sysroot):
    result = []
    directory = sysroot / 'bus/usb/devices'
    try:
        paths = sorted(directory.iterdir())[:256]
    except OSError:
        return result
    for path in paths:
        attributes = {name: read_text(path / name, 512)['text'].strip() for name in
                      ('idVendor', 'idProduct', 'product', 'manufacturer', 'speed',
                       'bDeviceClass', 'bInterfaceClass', 'bInterfaceSubClass', 'bInterfaceProtocol')}
        if not any(attributes.values()):
            continue
        driver = path / 'driver'
        try:
            bound_driver = driver.resolve(strict=True).name if driver.is_symlink() else None
        except (OSError, RuntimeError):
            bound_driver = None
        result.append({'node': path.name, 'attributes': attributes, 'bound_driver': bound_driver})
    return result


def collect(proc=Path('/proc'), sysroot=Path('/sys'), gui=Path('/usr/sbin/x6100_gui'),
            expected_gui=EXPECTED_GUI, run_command=command):
    if sha256(gui) != expected_gui:
        raise ValueError('Installed GUI does not match the verified .2 candidate.')
    card_text = read_text(proc / 'asound/cards')
    pcm_text = read_text(proc / 'asound/pcm')
    if any(item['error'] or item['truncated'] for item in (card_text, pcm_text)):
        raise ValueError('ALSA card/PCM inventory is unavailable or exceeds its bound.')
    cards = parse_alsa_cards(card_text['text'])
    if len(cards) > 32:
        raise ValueError('Unexpected ALSA card count.')
    for card in cards:
        number = card['number']
        device = sysroot / 'class/sound' / f'card{number}' / 'device'
        card['usb'] = usb_ancestor(device, sysroot)
        directory = proc / 'asound' / f'card{number}'
        streams = sorted(directory.glob('stream*'))[:8]
        card['usb_stream_descriptions'] = {path.name: read_text(path, 32768) for path in streams if path.is_file()}
    queries = {'sources': ['pactl', 'list', 'sources'], 'sinks': ['pactl', 'list', 'sinks'],
               'cards': ['pactl', 'list', 'cards'], 'modules': ['pactl', 'list', 'modules'],
               'info': ['pactl', 'info']}
    pulse = {name: run_command(args) for name, args in queries.items()}
    failed = [name for name, item in pulse.items() if item['exit_code'] != 0 or
              item.get('timed_out') or item.get('output_truncated') or not item['stdout'].strip()]
    result = assess(cards, parse_pcm(pcm_text['text']),
                    parse_pulse(pulse['sources']['stdout'], 'Source'),
                    parse_pulse(pulse['sinks']['stdout'], 'Sink'))
    if failed:
        result['status'] = 'BLOCKED'
        result['prerequisite_errors'] = ['Pulse inventory incomplete: ' + ', '.join(failed)]
    result.update({'schema': 'x6200.usb-headset-inventory.v1',
                   'collected_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'gui_sha256': expected_gui, 'kernel': platform.release(),
                   'alsa_cards_raw': card_text, 'alsa_pcm_raw': pcm_text,
                   'all_alsa_cards': cards, 'pulse': pulse, 'usb_devices': usb_devices(sysroot),
                   'driver_evidence': {'proc_modules': read_text(proc / 'modules'),
                                       'snd_usb_audio_in_sys_modules': (sysroot / 'module/snd_usb_audio').is_dir()},
                   'next': 'Review endpoint identities before any microphone, playback or RF test.'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new JSON evidence file')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; preserve previous evidence and choose a new file.')
    try:
        result = collect()
    except (OSError, ValueError) as error:
        result = {'schema': 'x6200.usb-headset-inventory.v1', 'status': 'BLOCKED',
                  'reason': str(error), 'hardware_qualified': False, 'rf_tested': False,
                  'duplex_streams_opened': False, 'routes_changed': False}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'usb_audio_cards': [
        {'number': c['number'], 'description': c['description'], 'status': c['status'],
         'capture_sources': [s['name'] for s in c['pulse_capture_sources']],
         'playback_sinks': [s['name'] for s in c['pulse_playback_sinks']],
         'limitations': c['limitations']} for c in result.get('usb_audio_cards', [])],
        'reason': result.get('reason'), 'prerequisite_errors': result.get('prerequisite_errors', []),
        'evidence_file': str(args.output), 'hardware_qualified': False, 'rf_tested': False}, indent=2))
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
