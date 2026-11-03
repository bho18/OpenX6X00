import random
import struct
import unittest
from check_capture_continuity import compare


class ContinuityChecks(unittest.TestCase):
    def setUp(self):
        r = random.Random(149)
        self.source = b''.join(struct.pack('<h', 149 + r.randrange(-2, 3)) for _ in range(60000))

    def test_contiguous_low_level_adc_noise(self):
        result = compare(self.source, self.source[12000:80000])
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['reference_start_sample'], 6000)

    def test_known_25520_sample_deletion(self):
        capture = self.source[12000:22000] + self.source[73040:110000]
        result = compare(self.source, capture)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['missing_samples'], 25520)
        self.assertLessEqual(result['splice_capture_sample_min'], 5000)
        self.assertGreaterEqual(result['splice_capture_sample_max'], 5000)

    def test_ambiguous_constant_is_inconclusive(self):
        self.assertEqual(compare(b'\x95\0' * 60000, b'\x95\0' * 30000)['status'], 'INCONCLUSIVE')

    def test_additional_corruption_cannot_be_called_single_deletion(self):
        capture = bytearray(self.source[12000:22000] + self.source[73040:110000])
        capture[15000:15002] = b'\0\x7f'
        self.assertEqual(compare(self.source, capture)['status'], 'INCONCLUSIVE')

    def test_repeated_speech_insertion_is_not_a_contiguous_pass(self):
        capture = self.source[12000:30000] + self.source[20000:70000]
        self.assertEqual(compare(self.source, capture)['status'], 'INCONCLUSIVE')


if __name__ == '__main__': unittest.main()
