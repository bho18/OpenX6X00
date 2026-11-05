import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import usb_headset_inventory as inventory


def pulse_block(kind, name, card, monitor=False, api='alsa', driver='module-alsa-card.c'):
    monitor_line = ('\tMonitor of Sink: ' + ('0' if monitor else 'n/a') + '\n') if kind == 'Source' else ''
    return (f'{kind} #1\n\tState: IDLE\n\tName: {name}\n\tDriver: {driver}\n'
            '\tSample Specification: s16le 2ch 48000Hz\n\tChannel Map: front-left,front-right\n'
            + monitor_line + '\tProperties:\n'
            + f'\t\talsa.card = "{card}"\n\t\tdevice.api = "{api}"\n'
            + f'\t\tdevice.class = "{"monitor" if monitor else "sound"}"\n')


def usb_card(number):
    return {'number': number, 'id': 'Headset', 'description': 'USB Audio',
            'usb': {'path': f'/sys/devices/usb3/3-{number}', 'id_vendor': '1234', 'id_product': '5678'}}


def pcm(card, capture, playback):
    return {'card': card, 'device': 0, 'capture_substreams': capture,
            'playback_substreams': playback, 'description': 'synthetic USB PCM'}


class AssessmentTests(unittest.TestCase):
    def test_monitor_is_never_microphone_even_with_usb_alsa_card(self):
        sources = inventory.parse_pulse(pulse_block('Source', 'alsa_output.usb_headset.monitor', 1, True), 'Source')
        sinks = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_headset', 1), 'Sink')
        result = inventory.assess([usb_card(1)], [pcm(1, 1, 1)], sources, sinks)
        self.assertEqual(result['status'], 'USB_AUDIO_INCOMPLETE')
        card = result['usb_audio_cards'][0]
        self.assertFalse(card['pulse_capture_sources'])
        self.assertEqual(card['monitor_sources_excluded'], ['alsa_output.usb_headset.monitor'])
        self.assertTrue(any('active physical Pulse source' in line for line in card['limitations']))

    def test_different_usb_cards_are_not_falsely_combined(self):
        sources = inventory.parse_pulse(pulse_block('Source', 'alsa_input.usb_mic', 1), 'Source')
        sinks = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_dac', 2), 'Sink')
        result = inventory.assess([usb_card(1), usb_card(2)], [pcm(1, 1, 0), pcm(2, 0, 1)], sources, sinks)
        self.assertEqual(result['status'], 'USB_AUDIO_INCOMPLETE')
        self.assertTrue(all(c['status'] == 'USB_AUDIO_INCOMPLETE' for c in result['usb_audio_cards']))

    def test_same_usb_card_duplex_endpoints_are_candidate_only(self):
        sources = inventory.parse_pulse(pulse_block('Source', 'alsa_input.usb_headset', 1) +
                                        pulse_block('Source', 'alsa_output.usb_headset.monitor', 1, True), 'Source')
        sinks = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_headset', 1), 'Sink')
        result = inventory.assess([usb_card(1)], [pcm(1, 1, 1)], sources, sinks)
        self.assertEqual(result['status'], 'USB_DUPLEX_CANDIDATE')
        self.assertEqual(len(result['usb_audio_cards'][0]['pulse_capture_sources']), 1)
        self.assertFalse(result['hardware_qualified'])
        self.assertFalse(result['duplex_streams_opened'])
        self.assertFalse(result['rf_tested'])

    def test_alsa_capture_absence_distinct_from_inactive_pulse_profile(self):
        sink = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_headset', 1), 'Sink')
        absent = inventory.assess([usb_card(1)], [pcm(1, 0, 1)], [], sink)
        inactive = inventory.assess([usb_card(1)], [pcm(1, 1, 1)], [], sink)
        self.assertTrue(any('No ALSA capture PCM' in s for s in absent['usb_audio_cards'][0]['limitations']))
        self.assertTrue(any('ALSA capture exists' in s for s in inactive['usb_audio_cards'][0]['limitations']))

    def test_virtual_source_is_not_physical_usb_microphone(self):
        source = inventory.parse_pulse(pulse_block('Source', 'remapped', 1, api='alsa',
                                                  driver='module-remap-source.c'), 'Source')
        sink = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_headset', 1), 'Sink')
        result = inventory.assess([usb_card(1)], [pcm(1, 1, 1)], source, sink)
        self.assertEqual(result['status'], 'USB_AUDIO_INCOMPLETE')

    def test_endpoint_without_name_cannot_be_used(self):
        text = pulse_block('Source', 'alsa_input.usb_headset', 1)
        text = text.replace('\tName: alsa_input.usb_headset\n', '')
        source = inventory.parse_pulse(text, 'Source')
        sink = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_headset', 1), 'Sink')
        self.assertFalse(source[0]['physical_alsa'])
        result = inventory.assess([usb_card(1)], [pcm(1, 1, 1)], source, sink)
        self.assertEqual(result['status'], 'USB_AUDIO_INCOMPLETE')

    def test_no_actual_usb_ancestry_is_not_usb_audio(self):
        card = {'number': 0, 'id': 'radio', 'description': 'sun8i-a33-audio', 'usb': None}
        source = inventory.parse_pulse(pulse_block('Source', 'alsa_input.usb_misleading_name', 0), 'Source')
        sink = inventory.parse_pulse(pulse_block('Sink', 'alsa_output.usb_misleading_name', 0), 'Sink')
        result = inventory.assess([card], [pcm(0, 1, 1)], source, sink)
        self.assertEqual(result['status'], 'NO_USB_AUDIO_DEVICE')

    def test_pcm_parser_handles_capture_and_playback_on_separate_pcm_devices(self):
        parsed = inventory.parse_pcm('01-00: USB Audio : USB Audio : playback 1\n01-01: USB Audio : USB Audio : capture 1\n')
        self.assertEqual([(p['device'], p['capture_substreams'], p['playback_substreams']) for p in parsed],
                         [(0, 0, 1), (1, 1, 0)])


class FilesystemAndProcessTests(unittest.TestCase):
    def test_nearest_usb_ancestor_and_bounded_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'sys'
            usb = root / 'devices/usb3/3-1'
            usb.mkdir(parents=True)
            (usb / 'idVendor').write_text('1234\n')
            (usb / 'idProduct').write_text('5678\n')
            (usb / 'product').write_text('Headset\n')
            interface = usb / '3-1:1.0'
            interface.mkdir()
            device = root / 'class/sound/card1/device'
            device.parent.mkdir(parents=True)
            device.symlink_to(interface)
            result = inventory.usb_ancestor(device, root)
            self.assertEqual(result['path'], str(usb))
            self.assertEqual(result['id_vendor'], '1234')
            self.assertIsNone(inventory.usb_ancestor(device, root, maximum=1))
            outside = Path(temp) / 'outside'
            outside.mkdir()
            device.unlink()
            device.symlink_to(outside)
            self.assertIsNone(inventory.usb_ancestor(device, root))

    def test_text_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'stream0'
            path.write_bytes(b'a' * 100)
            result = inventory.read_text(path, 16)
            self.assertEqual(result['text'], 'a' * 16)
            self.assertTrue(result['truncated'])

    def test_command_output_limit_and_timeout(self):
        large = inventory.command([sys.executable, '-c', 'print("x"*100000)'], limit=128)
        self.assertTrue(large['output_truncated'])
        self.assertLessEqual(len(large['stdout']) + len(large['stderr']), 128)
        slow = inventory.command([sys.executable, '-c', 'import time;time.sleep(3)'], timeout=.05)
        self.assertTrue(slow['timed_out'])
        missing = inventory.command(['/not/an/installed/program'])
        self.assertIsNone(missing['exit_code'])

    def test_hash_mismatch_blocks_before_pulse_queries(self):
        with tempfile.TemporaryDirectory() as temp:
            gui = Path(temp) / 'gui'
            gui.write_bytes(b'not the candidate')
            def forbidden(_):
                self.fail('A prerequisite failure reached an external command.')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                inventory.collect(gui=gui, run_command=forbidden)

    def test_main_saves_prerequisite_failure_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'evidence.json'
            with patch.object(sys, 'argv', ['inventory', '--output', str(output)]), \
                    patch.object(inventory, 'collect', side_effect=ValueError('synthetic missing prerequisite')):
                self.assertEqual(inventory.main(), 1)
            report = json.loads(output.read_text())
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertFalse(report['hardware_qualified'])
            self.assertFalse(report['routes_changed'])


if __name__ == '__main__':
    unittest.main()
