#!/usr/bin/env python3
"""Run production WR12 notification callbacks without hardware or worker timing."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_remote_ack import ROOT, function


class Wr12Reconciliation(unittest.TestCase):
    def test_coalesced_external_inhibit(self):
        source = (ROOT/'src/radio.c').read_text()
        functions = '\n'.join(function(source, marker) for marker in
            ('static void wr12_changed_cb(void *arg) {', 'static bool wr12_notify(void) {',
             'static void wr12_reconcile_tick(void) {'))
        template = (ROOT/'tools/freedv/wr12_reconcile_harness.c.in').read_text().replace('@WR12_FUNCTIONS@', functions)
        with tempfile.TemporaryDirectory(prefix='freedv-wr12-reconcile-') as directory:
            build = Path(directory)
            harness = build/'wr12.c'; harness.write_text(template)
            executable = build/'wr12'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-DENABLE_FREEDV_USB_VOICE=1', '-DENABLE_FREEDV_VOICE=1', '-I'+str(ROOT), str(harness),
                            str(ROOT/'src/freedv/writer_gate.c'), '-pthread', '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
