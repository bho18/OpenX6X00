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


if __name__ == '__main__':
    unittest.main()
