#!/usr/bin/env python3
"""Collect worker-count evidence without relabeling execution as qualification."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from pipeline_evidence import interrupted_samples
from validate_rade_acq_sdk import acquisition_callees


class InterruptionTests(unittest.TestCase):
    def test_window_intersections_use_whole_stream_position(self):
        for start, duration, expected in [(1.08, 14.04, 16000), (3, 26.04, 32000),
                                          (11, 14, 24000), (0, 10, 0), (10, 1, 8000),
                                          (12, 10, 0), (22, 2, 16000)]:
            with self.subTest(start=start, duration=duration):
                self.assertEqual(interrupted_samples(round(start * 1e9), round(duration * 1e9)), expected)

    def test_rejects_fractional_modem_sample(self):
        with self.assertRaises(ValueError): interrupted_samples(1, 120_000_000)


class TraversalTests(unittest.TestCase):
    def test_only_bounded_serial_and_neon_helpers(self):
        asm = ''' bl 0 <rade_acq_pool_detect@plt>
 bl 1 <rade_acq_detect_pilots_serial@plt>
 b.w 2 <rade_acq_grid_neon.constprop.0+0x8>
 blx 3 <rade_acq_correlate4.isra.2>
 bl 4 <opus_neon>
 bl 5 <rade_acq_grid_neon_unrelated>
 bl 6 <rade_acq_detect_pilots_serial_evil>
 b 7 <rade_acq_grid_neon+nothex>'''
        self.assertEqual(acquisition_callees(asm), ['rade_acq_correlate4.isra.2',
                                                   'rade_acq_detect_pilots_serial',
                                                   'rade_acq_grid_neon.constprop.0', 'rade_acq_pool_detect'])


class CollectorTests(unittest.TestCase):
    def invoke(self, *, execution=True, workers=2, effective=2, exit_code=0):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); scripts = root / 'tools/freedv'; scripts.mkdir(parents=True)
        shutil.copy2(Path(__file__).with_name('collect_benchmark.py'), scripts / 'collect_benchmark.py')
        (scripts / 'sources.json').write_text('{}')
        fixture = root / 'silence.raw'; fixture.write_bytes(bytes(16))
        exe = root / 'fake-benchmark'
        result = {'experiment': 'rade-rx-two-worker', 'execution': 'parallel', 'pacing': 'unpaced',
                  'workload': 'continuous', 'radio_io': False, 'full_pipeline_qualified': False,
                  'acquisition_workers': {'requested': workers, 'effective': effective}}
        exe.write_text('#!' + sys.executable + '\nimport json,sys\nr=' + repr(result) +
                       '\nr["test_argv"]=sys.argv[1:]\nprint(json.dumps(r))\nsys.exit(' + str(exit_code) + ')\n')
        exe.chmod(0o755); output = root / 'results'
        command = [sys.executable, str(scripts / 'collect_benchmark.py'), '--mode', 'RADE-V1',
                   '--direction', 'rx', '--benchmark', str(exe), '--fixture', str(fixture),
                   '--seconds', '3', '--pacing', 'unpaced', '--warmup', '1', '--output', str(output),
                   '--acquisition-workers', str(workers)]
        if execution: command += ['--execution', 'parallel']
        done = subprocess.run(command, capture_output=True, text=True)
        return done, output, fixture

    def test_explicit_worker_count_forwarded_and_recorded(self):
        done, output, fixture = self.invoke()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads((output / 'result.json').read_text())['test_argv'],
                         ['parallel', str(fixture), '3', 'unpaced', '1', 'continuous', '--acquisition-workers', '2'])
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(manifest['acquisition_workers_requested'], 2)
        self.assertTrue(manifest['acquisition_workers_include_caller'])

    def test_worker_option_requires_pipeline(self):
        done, output, _ = self.invoke(execution=False)
        self.assertEqual(done.returncode, 2)
        self.assertFalse(output.exists())

    def test_no_silent_worker_downgrade(self):
        done, output, _ = self.invoke(effective=1)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('worker count differs', done.stderr)
        self.assertTrue((output / 'benchmark.stdout').is_file())
        self.assertFalse((output / 'result.json').exists())

    def test_structured_failure_is_retained(self):
        done, output, _ = self.invoke(exit_code=3)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue((output / 'result.json').is_file())
        self.assertEqual(json.loads((output / 'events.json').read_text())[-1]['return_code'], 3)


if __name__ == '__main__': unittest.main()
