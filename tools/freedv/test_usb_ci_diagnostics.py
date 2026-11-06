#!/usr/bin/env python3
"""Failure artifacts remain useful even when the CI artifact service is down."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from usb_ci_diagnostics import diagnose


class FailureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.matrix = self.root / 'usb-controller-matrix';self.matrix.mkdir()
        self.failed = self.matrix / 'repeat-01/pa-delay';self.failed.mkdir(parents=True)
        self.good = self.matrix / 'repeat-01/normal';self.good.mkdir()
        (self.failed/'test.log').write_text('Fault injector did not exercise requested path\n')
        (self.failed/'case-result.json').write_text(json.dumps({'error':'Missing phase injection'}))
        (self.failed/'radio-output.raw').write_bytes(b'failed-fixture')
        (self.good/'radio-output.raw').write_bytes(b'successful-fixture')
        self.record = {'status':'FAIL','cases':{
            'normal':{'runs':[{'repeat':1,'status':'PASS'}]},
            'pa-delay':{'runs':[{'repeat':1,'status':'FAIL'}]}}}
        self.save()

    def save(self):
        (self.matrix/'matrix-result.json').write_text(json.dumps(self.record))

    def run_diagnostics(self, **kwargs):
        text=io.StringIO()
        with contextlib.redirect_stdout(text):
            result=diagnose(self.root,self.root/'output',**kwargs)
        return result,text.getvalue()

    def test_only_failed_public_fixture_and_reason_are_retained(self):
        record,text=self.run_diagnostics()
        self.assertIn('Missing phase injection',text)
        self.assertIn('Fault injector did not exercise requested path',text)
        self.assertEqual([x['source'] for x in record['copied']],['repeat-01/pa-delay/radio-output.raw'])
        self.assertFalse((self.root/'output/repeat-01/normal').exists())

    def test_size_limits_are_explicit_without_truncating_raw_audio(self):
        record,_=self.run_diagnostics(max_file=4,max_total=8)
        self.assertEqual(record['copied_bytes'],0)
        self.assertEqual(record['omitted'][0]['reason'],'per_file_byte_limit')
        self.assertFalse((self.root/'output/repeat-01/pa-delay/radio-output.raw').exists())

    def test_total_budget_omission_is_recorded(self):
        record,_=self.run_diagnostics(max_file=100,max_total=4)
        self.assertEqual(record['copied_bytes'],0)
        self.assertEqual(record['omitted'][0]['reason'],'total_byte_limit')

    def test_symlink_cannot_copy_unrelated_file(self):
        secret=self.root/'unrelated';secret.write_bytes(b'unrelated-content')
        (self.failed/'outside.raw').symlink_to(secret)
        record,_=self.run_diagnostics()
        self.assertTrue(any(x['reason']=='unsafe_or_nonregular_path' for x in record['omitted']))
        self.assertFalse((self.root/'output/repeat-01/pa-delay/outside.raw').exists())

    def test_interrupted_case_is_located_from_progress(self):
        self.record={'status':'INTERRUPTED','cases':{},'started':['pa-delay@1'],'executed':[]}
        (self.matrix/'matrix-result.json').unlink()
        (self.matrix/'progress.json').write_text(json.dumps(self.record))
        record,_=self.run_diagnostics()
        self.assertEqual(record['failed_cases'],[{'case':'pa-delay','repeat':1}])
        self.assertEqual(len(record['copied']),1)

    def test_failed_fifo_monitor_and_indexed_capture_are_retained_with_existing_bounds(self):
        (self.failed/'result').mkdir()
        expected = {
            'fifo-modem.raw': b'paced-fifo-stereo-fixture',
            'fifo-modem-mono.raw': b'paced-fifo-mono-fixture',
            'radio-output.raw': b'passive-monitor-fixture',
            'headset-output.raw': b'passive-headset-fixture',
            'result/mic-observer.pcm': b'indexed-microphone-fixture'}
        for name, payload in expected.items():
            (self.failed/name).write_bytes(payload)
        (self.failed/'operator-attachment.pcm').write_bytes(b'not-a-known-generated-fixture')
        (self.good/'fifo-modem.raw').write_bytes(b'successful-fifo-fixture')
        record,_=self.run_diagnostics()
        self.assertEqual({item['source'] for item in record['copied']},
                         {'repeat-01/pa-delay/'+name for name in expected})
        self.assertEqual(record['copied_bytes'],sum(map(len,expected.values())))
        for name, payload in expected.items():
            self.assertEqual((self.root/'output/repeat-01/pa-delay'/name).read_bytes(),payload)
        self.assertFalse((self.root/'output/repeat-01/normal').exists())
        self.assertFalse((self.root/'output/repeat-01/pa-delay/operator-attachment.pcm').exists())

    def test_indexed_pcm_cannot_evade_per_file_limit(self):
        (self.failed/'result').mkdir()
        (self.failed/'result/mic-observer.pcm').write_bytes(b'12345')
        record,_=self.run_diagnostics(max_file=4,max_total=8)
        self.assertEqual(record['copied_bytes'],0)
        self.assertIn({'source':'repeat-01/pa-delay/result/mic-observer.pcm',
                       'reason':'per_file_byte_limit','bytes':5},record['omitted'])


if __name__ == '__main__':
    unittest.main()
