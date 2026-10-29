#!/usr/bin/env python3
"""Exercise graceful shutdown, ignored TERM, and child ownership boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HELPER = Path(__file__).with_name('stop_host_process.py')


class StopHostProcessTest(unittest.TestCase):
    def launch(self, ignore_term=False):
        source = ('import signal,time; ' +
                  ('signal.signal(signal.SIGTERM, signal.SIG_IGN); ' if ignore_term else '') +
                  'print("ready", flush=True); time.sleep(30)')
        child = subprocess.Popen([sys.executable, '-c', source], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.reap, child)
        self.assertEqual(child.stdout.readline(), 'ready\n')
        return child

    @staticmethod
    def reap(child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)
        if child.stdout:
            child.stdout.close()

    def invoke(self, pid):
        return subprocess.run([sys.executable, str(HELPER), str(pid), '--term-seconds', '0.2', '--kill-seconds', '1'],
                              capture_output=True, text=True, timeout=3)

    def test_term_exits_and_is_reapable(self):
        child = self.launch()
        result = self.invoke(child.pid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['shutdown'], 'term')
        self.assertEqual(child.wait(timeout=1), -15)

    def test_ignored_term_escalates_within_deadline(self):
        child = self.launch(ignore_term=True)
        result = self.invoke(child.pid)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['shutdown'], 'kill')
        self.assertLess(data['elapsed_seconds'], 1.5)
        self.assertEqual(child.wait(timeout=1), -9)

    def test_refuses_parent_process(self):
        result = self.invoke(os.getpid())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not owned by the calling shell', result.stderr)

    def test_already_exited_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(7)'])
        self.addCleanup(self.reap, child)
        self.assertEqual(child.wait(timeout=2), 7)
        result = self.invoke(child.pid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['shutdown'], 'already-gone')
        self.assertEqual(child.returncode, 7)  # helper does not erase the failure


if __name__ == '__main__':
    unittest.main()
