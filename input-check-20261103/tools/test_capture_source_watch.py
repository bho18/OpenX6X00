import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from capture_source_watch import record

class ReferenceCaptureChecks(unittest.TestCase):
    def run_capture(self,script):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);p=Path(temp.name)
        return p,record([sys.executable,'-c',script],p,.3)
    def test_bounded_paced_input_accounts_for_every_byte(self):
        p,r=self.run_capture('import os,time\nwhile True:\n os.write(1,b"\\x95\\0"*441);time.sleep(.01)')
        self.assertEqual(r['status'],'COLLECTED')
        with wave.open(str(p/'capture.wav')) as w:
            self.assertEqual(w.getnframes(),r['samples']);self.assertEqual(w.readframes(w.getnframes()),(p/'capture.raw').read_bytes())
        reads=[json.loads(x) for x in (p/'reads.jsonl').read_text().splitlines()]
        self.assertEqual(sum(x['bytes'] for x in reads),r['bytes'])
    def test_early_exit_is_not_success(self):
        _,r=self.run_capture('import os;os.write(1,b"\\0\\0"*10)')
        self.assertEqual(r['status'],'INCONCLUSIVE')
    def test_empty_source_is_not_success(self):
        _,r=self.run_capture('pass');self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual(r['samples'],0)
    def test_odd_final_sample_preserves_raw(self):
        p,r=self.run_capture('import os;os.write(1,b"abc")')
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual((p/'capture.raw').read_bytes(),b'abc');self.assertFalse((p/'capture.wav').exists())
    def test_unpaced_overflow_stops_at_bounded_budget(self):
        _,r=self.run_capture('import os\nwhile True:os.write(1,b"\\0"*65536)')
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertLess(r['bytes'],44100*2*2.3+4097)

if __name__=='__main__':unittest.main()
