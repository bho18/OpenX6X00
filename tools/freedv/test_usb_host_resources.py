#!/usr/bin/env python3
"""Resource admission, immutable data and bounded cache-hint regressions."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import usb_host_resources as subject


class ResourceContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.group=self.root/'cgroup';self.group.mkdir()
        self.write('memory.current',12*subject.GIB)
        self.write('memory.max',16*subject.GIB)
        self.write('memory.high','max')
        self.events={'high':0,'max':102134,'oom':0,'oom_kill':0}
        self.stats={'anon':50*1024**2,'file':10*subject.GIB,'shmem':2*subject.GIB,
                    'file_dirty':0,'file_writeback':0,'pgscan_direct':4269437,'pgsteal_direct':4245025}
        self.write_counters()
        self.write('cpu.max','400000 100000')
        self.write('cpu.stat','usage_usec 10000\nnr_throttled 12\nthrottled_usec 100')

    def write(self,name,value):
        (self.group/name).write_text(str(value)+'\n')

    def write_counters(self):
        self.write('memory.events','\n'.join(k+' '+str(v) for k,v in self.events.items()))
        self.write('memory.stat','\n'.join(k+' '+str(v) for k,v in self.stats.items()))

    def preflight(self,**kwargs):
        return subject.run_resource_preflight(cgroup_root=self.group,**kwargs)

    def test_known_bounded_contract_and_cpu_quota(self):
        report=self.preflight()
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['headroom_bytes'],4*subject.GIB)
        self.assertEqual(report['after']['cpu']['quota_cores'],4)
        self.assertEqual(report['after']['memory']['events']['max'],102134)
        self.assertEqual(report['configuration']['min_end_headroom_bytes'],subject.GIB)

    def test_unknown_or_unbounded_memory_never_qualifies(self):
        for value in ('max','broken'):
            with self.subTest(value=value):
                self.write('memory.max',value)
                self.assertEqual(self.preflight()['status'],'BLOCKED')

    def test_memory_high_is_a_real_admission_limit(self):
        self.write('memory.high',13*subject.GIB)
        report=self.preflight()
        self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(report['headroom_bytes'],subject.GIB)

    def test_regression_pressure_is_recorded_not_promoted(self):
        self.write('memory.current',16*subject.GIB-50*1024**2)
        report=self.preflight(profile='regression')
        self.assertEqual(report['status'],'RECORDED_NONPROMOTABLE')
        self.assertEqual(report['assessment'],'BLOCKED')
        self.assertFalse(report['promotion_eligible'])

    def test_lowered_qualification_threshold_is_not_a_bypass(self):
        report=self.preflight(min_headroom_bytes=1,min_end_headroom_bytes=1)
        self.assertEqual(report['status'],'BLOCKED')

    def test_bounded_artifact_growth_uses_the_separate_end_reserve(self):
        start=self.preflight()
        self.write('memory.current',14*subject.GIB)
        end=subject.observe_resource_end(start,cgroup_root=self.group)
        self.assertEqual(end['status'],'PASS')
        self.assertEqual(end['headroom_bytes'],2*subject.GIB)
        self.write('memory.current',16*subject.GIB-512*1024**2)
        self.assertEqual(subject.observe_resource_end(start,cgroup_root=self.group)['status'],'BLOCKED')

    def test_new_pressure_and_counter_reset_cannot_hide_behind_headroom(self):
        for component,key in (('events','max'),('events','oom_kill'),('stats','pgscan_direct'),('stats','pgsteal_direct')):
            with self.subTest(component=component,key=key):
                start=self.preflight()
                counters=getattr(self,component);counters[key]+=1;self.write_counters()
                end=subject.observe_resource_end(start,cgroup_root=self.group)
                self.assertEqual(end['status'],'BLOCKED')
                self.assertTrue(end['new_memory_pressure'])
        start=self.preflight();self.events['max']=0;self.write_counters()
        self.assertEqual(subject.observe_resource_end(start,cgroup_root=self.group)['status'],'BLOCKED')

    def test_a_regression_preflight_cannot_be_promoted_at_end(self):
        start=self.preflight(profile='regression')
        end=subject.observe_resource_end(start,profile='qualification',cgroup_root=self.group)
        self.assertEqual(end['status'],'BLOCKED')
        self.assertFalse(end['promotion_eligible'])

    def test_limits_changing_midcampaign_are_not_same_environment(self):
        start=self.preflight();self.write('cpu.max','200000 100000')
        end=subject.observe_resource_end(start,cgroup_root=self.group)
        self.assertEqual(end['status'],'BLOCKED')
        self.assertTrue(any('limits changed' in reason for reason in end['reasons']))


class ScopedCacheHintTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.owned=self.root/'owned';self.owned.mkdir()
        self.data=self.owned/'artifact.raw';self.data.write_bytes(b'public fixture\0'*100)
        self.history=self.owned/'history.json';self.history.write_text('{"status":"FAIL","preserve":true}\n')

    def test_real_readonly_advice_preserves_data_hashes_history_and_metadata(self):
        before={path.name:(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns,path.stat().st_ino)
                for path in (self.data,self.history)}
        outside=self.root/'outside.raw';outside.write_bytes(b'unrelated')
        (self.owned/'outside-link.raw').symlink_to(outside)
        os.link(self.data,self.owned/'duplicate.raw')
        report=subject._reclaim_direct([self.owned],2,100,1024**2,0,0)
        self.assertEqual(report['status'],'COMPLETE')
        self.assertEqual(report['advised_files'],2)
        self.assertEqual(report['skipped_duplicate_inodes'],1)
        self.assertEqual(report['skipped_nonregular'],1)
        self.assertFalse(report['metadata_changes'])
        for path in (self.data,self.history):
            self.assertEqual((hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns,path.stat().st_ino),before[path.name])
        self.assertEqual(outside.read_bytes(),b'unrelated')

    def test_broad_or_symlink_roots_are_rejected(self):
        alias=self.root/'alias';alias.symlink_to(self.owned,target_is_directory=True)
        for path in (Path('/tmp'),Path('/workspace'),alias):
            with self.subTest(path=path):
                report=subject.reclaim_task_cache([path])
                self.assertEqual(report['status'],'BLOCKED')
                self.assertEqual(report['advised_files'],0)

    def test_parent_traversal_cannot_disguise_a_broad_root(self):
        report=subject.reclaim_task_cache([Path('/tmp/..')])
        self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(report['advised_files'],0)

    def test_size_and_count_limits_are_explicit(self):
        report=subject._reclaim_direct([self.owned],2,100,1024**2,1024**2,0)
        self.assertEqual(report['skipped_small_files'],2)
        self.assertEqual(report['advised_files'],0)
        report=subject._reclaim_direct([self.owned],2,1,1024**2,0,0)
        self.assertEqual(report['status'],'PARTIAL')
        self.assertEqual(report['stop_reason'],'file_count_limit')
        self.assertEqual(report['advised_files'],1)

    def test_a_stuck_advisory_call_has_a_hard_worker_deadline(self):
        with mock.patch.object(subject.os,'posix_fadvise',side_effect=lambda *args:time.sleep(10)):
            before=time.monotonic()
            report=subject.reclaim_task_cache([self.owned],max_seconds=.05,min_file_bytes=0,min_file_age_seconds=0)
        self.assertEqual(report['status'],'BLOCKED')
        self.assertTrue(report['timed_out'])
        self.assertFalse(report['worker_still_running'])
        self.assertLess(time.monotonic()-before,2)


if __name__=='__main__':
    unittest.main()
