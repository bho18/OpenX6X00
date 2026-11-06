#!/usr/bin/env python3
"""Real-FIFO tests for the clock consumer; no PulseAudio or codec required."""
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from usb_fifo_clock import SCOPE, consume


HELPER = Path(__file__).with_name('usb_fifo_clock.py')


class FifoClockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='usb-fifo-clock-')
        self.root = Path(self.directory.name)
        self.fifo = self.root / 'audio.fifo'
        self.raw = self.root / 'audio.raw'
        self.timing = self.root / 'timing.jsonl'
        self.child = None
        self.writer = None

    def tearDown(self):
        if self.writer is not None:
            os.close(self.writer)
        if self.child is not None and self.child.poll() is None:
            self.child.kill()
            self.child.communicate(timeout=2)
        self.directory.cleanup()

    def launch(self, channels=2, width=3, rate=1000, **limits):
        options = {'startup-timeout': .5, 'no-data-timeout': .3, 'max-seconds': 2}
        options.update(limits)
        command = [sys.executable, str(HELPER), '--fifo', str(self.fifo),
                   '--output', str(self.raw), '--timing', str(self.timing),
                   '--rate', str(rate), '--channels', str(channels), '--width', str(width),
                   '--period-ms', '10']
        for name, value in options.items():
            command.extend(('--' + name, str(value)))
        self.child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def open_writer(self):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                self.writer = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
                return
            except OSError as error:
                if error.errno != errno.ENXIO:
                    raise
                time.sleep(.005)
        self.fail('FIFO reader did not open within one second')

    def close_writer(self):
        os.close(self.writer)
        self.writer = None

    def finish(self, expected):
        stdout, stderr = self.child.communicate(timeout=3)
        self.assertEqual(self.child.returncode, expected, (stdout, stderr))
        rows = [json.loads(line) for line in self.timing.read_text().splitlines()]
        self.assertEqual([row['kind'] for row in rows].count('session'), 1)
        self.assertEqual(rows[-1]['kind'], 'terminal')
        self.assertEqual(rows[0]['scope'], SCOPE)
        self.assertEqual(rows[-1]['scope'], SCOPE)
        self.assertEqual(rows[-1]['raw_bytes'], self.raw.stat().st_size)
        return rows[0], [row for row in rows if row['kind'] == 'block'], rows[-1]

    def wait_for_block(self):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if self.timing.exists():
                lines = self.timing.read_text().splitlines()
                if any('"kind":"block"' in line for line in lines):
                    return
            if self.child.poll() is not None:
                self.fail('FIFO consumer exited before its first block')
            time.sleep(.005)
        self.fail('FIFO consumer did not record a block within one second')

    def test_fragmented_stereo24_preserves_bytes_and_sample_deadlines(self):
        os.mkfifo(self.fifo)
        self.launch()
        self.open_writer()
        frames = [b'\0' * 6 for _ in range(60)]
        frames[3] = b'\x01\0\0\0\0\0'
        frames[57] = b'\0\0\0\xff\xff\xff'
        payload = b''.join(frames)
        # Unaligned writes force the consumer to retain partial frames. The
        # complete payload is shorter than pipe capacity, so no writer blocks.
        offset = 0
        for size in (1, 2, 5, 7, 11, 19, 23):
            self.assertEqual(os.write(self.writer, payload[offset:offset + size]), size)
            offset += size
            time.sleep(.003)
        self.assertEqual(os.write(self.writer, payload[offset:]), len(payload) - offset)
        self.close_writer()
        header, blocks, terminal = self.finish(0)
        self.assertEqual(self.raw.read_bytes(), payload)
        self.assertEqual(terminal['status'], 'COMPLETE')
        self.assertEqual(terminal['frames'], 60)
        self.assertEqual(terminal['partial_frame_bytes'], 0)
        self.assertEqual(blocks[0]['frame_start'], 0)
        self.assertEqual(blocks[-1]['frame_end'], 60)
        self.assertEqual(sum(block['bytes_read'] for block in blocks), len(payload))
        self.assertEqual(min(block['first_nonzero_frame'] for block in blocks
                             if block['first_nonzero_frame'] is not None), 3)
        self.assertEqual(max(block['last_nonzero_frame'] for block in blocks
                             if block['last_nonzero_frame'] is not None), 57)
        previous = 0
        for block in blocks:
            self.assertEqual(block['frame_start'], previous)
            previous = block['frame_end']
            self.assertLessEqual(block['frame_end'], block['period'] * 10)
            self.assertLessEqual(block['scheduled_last_ns'], block['before_ns'])
            self.assertLessEqual(block['before_ns'], block['after_ns'])
            self.assertEqual(block['lateness_ns'],
                             block['after_ns'] - block['scheduled_last_ns'])
        self.assertGreaterEqual(blocks[-1]['after_ns'] - header['clock_origin_ns'], 59_000_000)

    def test_incomplete_eof_retains_partial_bytes_and_reports_failure(self):
        os.mkfifo(self.fifo)
        self.launch(channels=2, width=2)
        self.open_writer()
        payload = b'\x01\0\x02\0\x03'
        os.write(self.writer, payload)
        self.close_writer()
        _, blocks, terminal = self.finish(1)
        self.assertEqual(self.raw.read_bytes(), payload)
        self.assertEqual(terminal['reason'], 'partial-frame-at-eof')
        self.assertEqual(terminal['frames'], 1)
        self.assertEqual(terminal['partial_frame_bytes'], 1)
        self.assertEqual(blocks[0]['bytes_read'], 5)

    def test_fractional_period_frame_counts_do_not_read_ahead(self):
        os.mkfifo(self.fifo)
        self.launch(channels=1, width=2, rate=11025)
        self.open_writer()
        payload = b'\x01\0' * 353
        os.write(self.writer, payload)
        self.close_writer()
        _, blocks, terminal = self.finish(0)
        self.assertEqual(self.raw.read_bytes(), payload)
        self.assertEqual(terminal['frames'], 353)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            self.assertLessEqual(block['frame_end'], 11025 * block['period'] // 100)
            self.assertLessEqual(block['scheduled_last_ns'], block['before_ns'])

    def test_missing_fifo_has_bounded_failure_with_receipt(self):
        started = time.monotonic()
        self.launch(**{'startup-timeout': .08})
        header, blocks, terminal = self.finish(1)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(terminal['reason'], 'missing-fifo-startup-timeout')
        self.assertIsNone(header['clock_origin_ns'])
        self.assertEqual(blocks, [])
        self.assertEqual(terminal['raw_bytes'], 0)

    def test_no_data_failure_preserves_an_unfinished_block(self):
        os.mkfifo(self.fifo)
        self.launch(**{'no-data-timeout': .08})
        self.open_writer()
        os.write(self.writer, b'\x01\0\0\0\0\0')
        # Keep the writer open: missing later data must time out, not look like
        # a successful EOF. Even this unfinished ten-frame block is recorded.
        _, blocks, terminal = self.finish(1)
        self.assertEqual(terminal['reason'], 'FIFO-no-data-timeout')
        self.assertEqual(terminal['frames'], 1)
        self.assertEqual(blocks[0]['frame_end'], 1)
        self.assertEqual(blocks[0]['bytes_read'], 6)

    def test_sigterm_with_open_writer_preserves_partial_and_exits_cleanly(self):
        os.mkfifo(self.fifo)
        self.launch()
        self.open_writer()
        payload = b'\x01\0\0\0\0\0' * 10 + b'\x02'
        os.write(self.writer, payload)
        self.wait_for_block()
        # Let the next read retain the extra incomplete frame, then request a
        # normal supervisor stop while the producer still holds the FIFO open.
        time.sleep(.025)
        self.child.send_signal(signal.SIGTERM)
        _, blocks, terminal = self.finish(0)
        self.assertEqual(terminal['status'], 'STOPPED')
        self.assertEqual(terminal['reason'], 'signal-' + str(signal.SIGTERM))
        self.assertEqual(self.raw.read_bytes(), payload)
        self.assertEqual(terminal['partial_frame_bytes'], 1)
        self.assertEqual(sum(block['bytes_read'] for block in blocks), len(payload))

    def test_sigterm_before_first_data_has_one_receipt_and_no_fake_origin(self):
        os.mkfifo(self.fifo)
        self.launch()
        self.open_writer()
        self.child.send_signal(signal.SIGTERM)
        header, blocks, terminal = self.finish(0)
        self.assertEqual(terminal['status'], 'STOPPED')
        self.assertIsNone(header['clock_origin_ns'])
        self.assertEqual(blocks, [])

    def test_absolute_runtime_deadline_bounds_an_open_producer(self):
        os.mkfifo(self.fifo)
        self.launch(**{'max-seconds': .12, 'no-data-timeout': 1})
        self.open_writer()
        os.write(self.writer, b'\x01\0\0\0\0\0')
        _, blocks, terminal = self.finish(1)
        self.assertEqual(terminal['reason'], 'FIFO-runtime-deadline')
        self.assertEqual(blocks[0]['frame_end'], 1)

    def test_non_fifo_input_is_rejected_without_consuming_it(self):
        self.fifo.write_bytes(b'keep this data')
        self.launch()
        _, _, terminal = self.finish(1)
        self.assertEqual(self.fifo.read_bytes(), b'keep this data')
        self.assertEqual(terminal['reason'], 'Input path is not a real FIFO')
        self.assertEqual(terminal['raw_bytes'], 0)

    def test_infinite_time_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            consume(self.fifo, self.raw, self.timing, 1000, 2, 2, max_seconds=float('inf'))
        self.assertFalse(self.raw.exists())


if __name__ == '__main__':
    unittest.main()
