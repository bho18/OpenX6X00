import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import route_agent as a
class AgentTest(unittest.TestCase):
 def test_staging_has_no_execution(self):
  with tempfile.TemporaryDirectory() as t,patch.object(a,'CONTROL',Path(t)):
   (Path(t)/'status.json').write_text('{"state":1}')
   r=a.stage('modem-waveform',False)
   self.assertFalse(r['rf_started']);self.assertEqual((Path(t)/'stage').read_text(),'1 20 6 0 0 0\n')
   with self.assertRaises(ValueError):a.stage('modem-waveform',True)
   self.assertEqual(a.stage('modem-prekey-gain',False)['local_action'],'Arm then Run')
 def test_only_record_cases_repeat(self):
  with tempfile.TemporaryDirectory() as t,patch.object(a,'CONTROL',Path(t)):
   (Path(t)/'status.json').write_text('{"state":1}')
   a.stage('record-ordinary-zero',True)
   self.assertEqual((Path(t)/'repeat-five').read_text(),'5\n')
   (Path(t)/'status.json').write_text('{"state":3}')
   with self.assertRaises(ValueError):a.stage('record-hand-zero',True)
 def test_incomplete_never_passes(self):
  self.assertEqual(a.next_case({})['status'],'BLOCKED')
  self.assertEqual(a.next_case({'cases':[{}]})['status'],'INCONCLUSIVE')
if __name__=='__main__':unittest.main()
