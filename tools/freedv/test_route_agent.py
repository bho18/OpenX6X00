import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import route_agent as a


class AgentTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.patches=[patch.object(a,'CONTROL',self.root),patch.object(a,'STAGES',self.root/'journal'),patch.object(a.time,'sleep',self.ack)]
  for p in self.patches:p.start()
  self.status()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def status(self,**values):
  x={'state':1,'monotonic_ms':time.monotonic()*1000};x.update(values)
  (self.root/'status.json').write_text(json.dumps(x))
 def ack(self,_):
  fields=list(map(int,(self.root/'stage').read_text().split()))
  self.assertEqual(fields[0],2)
  selected=dict(zip(('case','route','microphone_index','capture_channel','mixer_choice','repeat'),fields[2:]))
  self.status(stage_request_id=fields[1],stage_accepted=True,selected=selected)
 def test_staging_waits_for_exact_ack_without_execution(self):
  r=a.stage('modem-waveform',False,{'firmware_commit':'one','gui_sha256':'gui'})
  self.assertFalse(r['rf_started']);self.assertEqual(r['selected']['case'],20)
  self.assertEqual(r['selected']['repeat'],1)
  self.assertTrue((self.root/'journal'/f"{r['request_id']}.json").is_file())
  self.assertFalse((self.root/'repeat-five').exists())
  with self.assertRaises(ValueError):a.stage('modem-waveform',True)
  self.assertEqual(a.stage('modem-prekey-gain',False)['local_action'],'Arm then Run')
  self.assertEqual(a.stage('record-then-modem',False)['selected']['case'],28)
  with self.assertRaises(ValueError):a.stage('record-then-modem',True)
 def test_only_record_cases_repeat(self):
  self.assertEqual(a.stage('record-ordinary-zero',True)['selected']['repeat'],5)
  self.status(state=3)
  with self.assertRaises(ValueError):a.stage('record-hand-zero',True)
 def test_stale_status_cannot_stage(self):
  self.status(monotonic_ms=time.monotonic()*1000-3000)
  with self.assertRaisesRegex(ValueError,'stale'):a.stage('record-hand-zero',False)
  self.assertFalse((self.root/'stage').exists())
 def test_wrong_ack_is_rejected(self):
  def wrong(delay):
   self.ack(delay);x=json.loads((self.root/'status.json').read_text());x['selected']['route']=7
   self.status(**x)
  with patch.object(a.time,'sleep',wrong),self.assertRaisesRegex(ValueError,'different case'):
   a.stage('record-hand-zero',False)
 def test_gui_rejection_does_not_report_staged(self):
  def reject(delay):
   self.ack(delay);x=json.loads((self.root/'status.json').read_text());x['stage_accepted']=False;x['stage_rejection']='busy'
   self.status(**x)
  with patch.object(a.time,'sleep',reject),self.assertRaisesRegex(ValueError,'busy'):
   a.stage('record-hand-zero',False)
 def test_no_heartbeat_is_incompatible(self):
  (self.root/'status.json').write_text('{"state":1}')
  with self.assertRaisesRegex(ValueError,'stale'):a.read_status()
 def test_incomplete_never_passes(self):
  self.assertEqual(a.next_case({})['status'],'INCONCLUSIVE')
  self.assertEqual(a.next_case({'issues':['missing capture']})['status'],'INCONCLUSIVE')
  self.assertEqual(a.next_case({'cases':[{}]})['status'],'INCONCLUSIVE')

if __name__=='__main__':unittest.main()
