import json
import os
from pathlib import Path
import tempfile
import unittest

import mic_ptt_input_watch as watch


class InputWatchTests(unittest.TestCase):
    def test_fragmented_events_preserve_press_release_and_repeat(self):
        frames = b''.join(watch.EVENT.pack(12, i, 1, 707, v)
                          for i, v in enumerate((1, 2, 0)))
        decoder = watch.EventDecoder()
        events = []
        for byte in frames:
            events.extend(decoder.feed(bytes([byte])))
        self.assertEqual([e['value'] for e in events], [1, 2, 0])
        self.assertEqual([e['code'] for e in events], [707] * 3)
        self.assertFalse(decoder.pending)

    def run_observation(self, frames, close_writer=False):
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        try:
            os.write(write_fd, frames)
            if close_writer:
                os.close(write_fd)
                write_fd = None
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                result = watch.observe([('synthetic-pipe', read_fd)], out, seconds=.02)
                rows = [json.loads(line) for line in (out / 'events.jsonl').read_text().splitlines()]
                return result, rows
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_real_descriptor_observation_does_not_promote_mapping(self):
        result, rows = self.run_observation(watch.EVENT.pack(1, 0, 1, 707, 1) +
                                            watch.EVENT.pack(1, 1, 1, 707, 0))
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['events_by_device']['synthetic-pipe'], 2)
        self.assertEqual(result['mic_ptt_mapping'], 'UNASSESSED')
        self.assertFalse(result['hardware_qualified'])
        self.assertEqual([r['value'] for r in rows if r['kind'] == 'input_event'], [1, 0])

    def test_kernel_queue_overflow_is_incomplete(self):
        result, _ = self.run_observation(watch.EVENT.pack(1, 0, 0, 3, 0))
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertIn('SYN_DROPPED', result['issues'][0])

    def test_disconnect_and_truncated_event_are_incomplete(self):
        result, _ = self.run_observation(watch.EVENT.pack(1, 0, 1, 707, 0)[:-1], True)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertTrue(any('stream ended' in issue for issue in result['issues']))
        self.assertTrue(any('Partial input_event' in issue for issue in result['issues']))


if __name__ == '__main__':
    unittest.main()
