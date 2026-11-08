#!/usr/bin/env python3
"""Adversarial checks for exact-source release and fork-only boundaries."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_operational_release as build
import operational_clock_probe as clock
import publish_operational_release as publish
import release_cache


class ReleaseBoundaries(unittest.TestCase):
    def test_cache_retirement_preserves_evidence_inputs_and_linked_binaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_root = root/'build'
            build_root.mkdir()
            (build_root/'CMakeCache.txt').write_text('fixture')
            (build_root/'build.ninja').write_text('build generated.a: C_STATIC_LIBRARY_LINKER x.o\n')
            obj = build_root/'CMakeFiles/example.dir/main.c.o'
            obj.parent.mkdir(parents=True)
            obj.write_bytes(b'generated object')
            (build_root/'generated.a').write_bytes(b'generated archive')
            (build_root/'input.a').write_bytes(b'prebuilt dependency input')
            binary = root/'attempts/current/binaries/gui'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'linked program')
            log = root/'attempts/current/command.log'
            log.write_text('immutable evidence')
            record = release_cache.retire_intermediates(root, {'gui': {'path': str(binary), 'sha256': release_cache.sha(binary)}})
            self.assertEqual(record['status'], 'PASS')
            self.assertFalse(obj.exists())
            self.assertFalse((build_root/'generated.a').exists())
            self.assertEqual((build_root/'input.a').read_bytes(), b'prebuilt dependency input')
            self.assertEqual(binary.read_bytes(), b'linked program')
            self.assertEqual(log.read_text(), 'immutable evidence')

    def test_upstream_publication_rejected_before_any_subprocess(self):
        with patch.object(publish.subprocess, 'run') as run, patch.object(publish.subprocess, 'check_output') as output:
            with self.assertRaisesRegex(ValueError, 'restricted'):
                publish.publish(Path('/absent'), 'Guru-RF/OpenX6X00', 'freedv-operational-beta-20261108.1',
                                'a'*40, Path('/notes'), Path('/download'))
            run.assert_not_called()
            output.assert_not_called()

    def test_release_checksums_reject_extras_duplicates_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in publish.ASSETS-{'SHA256SUMS'}:
                (root/name).write_bytes(name.encode())
            sums = ''.join(publish.sha(root/name)+'  '+name+'\n' for name in sorted(publish.ASSETS-{'SHA256SUMS'}))
            (root/'SHA256SUMS').write_text(sums)
            self.assertEqual(len(publish.checksums(root)), 8)
            for altered in (sums+sums.splitlines()[0]+'\n', sums+'a'*64+'  ../secret\n', sums.replace(sums[:64], 'a'*64, 1)):
                (root/'SHA256SUMS').write_text(altered)
                with self.assertRaises(ValueError):
                    publish.checksums(root)

    def test_source_archive_must_match_every_canonical_byte(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {'main.c': hashlib.sha256(b'expected').hexdigest()}
            for member_name, payload, success in (('OpenX6X00/main.c', b'expected', True),
                    ('OpenX6X00/main.c', b'wrong', False), ('OpenX6X00/../secret', b'expected', False)):
                path = root/'source.tar.xz'
                with tarfile.open(path, 'w:xz') as archive:
                    entry = tarfile.TarInfo(member_name)
                    entry.size = len(payload)
                    archive.addfile(entry, io.BytesIO(payload))
                if success:
                    publish.validate_source_archive(path, files)
                else:
                    with self.assertRaises(ValueError):
                        publish.validate_source_archive(path, files)

    def test_clock_window_requires_full_coverage_and_exact_budget(self):
        metadata = {'schema': 'x6200.operational-clock.v1', 'period_ns': clock.PERIOD_NS,
                    'max_allowed_gap_ns': clock.MAX_GAP_NS}
        record = {**metadata, 'samples': [1, 170_000_001, 340_000_001]}
        self.assertEqual(clock.grade_window(record, 2, 340_000_000)['max_gap_ns'], 170_000_000)
        for start, end in ((0, 3), (2, 340_000_002), (10, 9)):
            with self.assertRaises(ValueError):
                clock.grade_window(record, start, end)
        with self.assertRaises(ValueError):
            clock.grade_window({**metadata, 'samples': [1, 170_000_002, 340_000_001]}, 2, 340_000_000)
        # Compile-time gaps outside the declared qualification window give no
        # qualification failure credit and cannot hide an in-window overrun.
        result = clock.grade_window({**metadata, 'samples': [1, 1_000_000_001, 1_020_000_001, 1_040_000_001]},
                                    1_000_000_002, 1_039_000_000)
        self.assertEqual(result['max_gap_ns'], 20_000_000)
        for key in ('schema', 'period_ns', 'max_allowed_gap_ns'):
            altered = dict(record)
            altered[key] = 'wrong'
            with self.assertRaises(ValueError):
                clock.grade_window(altered, 2, 340_000_000)

    def test_neutral_observer_must_use_reserved_cpu_and_frozen_source(self):
        identity = {'source_commit': 'a'*40, 'source_tree_sha256': 'b'*64,
                    'source_files': {'tools/freedv/operational_clock_probe.py': 'c'*64}}
        matching = {'source_commit': 'a'*40, 'source_tree_sha256': 'b'*64, 'build_id': 'test'}
        record = {**matching, 'stop': {**matching, 'ended_monotonic_ns': 20},
                  'cpu_affinity': [2], 'cpu_max': '400000 100000', 'probe_script_sha256': 'c'*64}
        matrix = {'identity': {'host_resource_configuration': {'cpu_scheduling': {
            'selected_affinity': [0, 1], 'inherited_affinity': [0, 1, 2], 'quota_us': 400000, 'period_us': 100000}}},
            'host_resources': {'end': {'after': {'observed_monotonic_ns': 19}}}}
        build.validate_probe_context(record, matrix, identity, 'test')
        for key, value in (('cpu_affinity', [1]), ('cpu_max', '200000 100000'), ('probe_script_sha256', 'e'*64)):
            altered = dict(record)
            altered[key] = value
            with self.assertRaises(ValueError):
                build.validate_probe_context(altered, matrix, identity, 'test')

    def ci_fixture(self, root, dirty=False):
        identity = {'source_commit': 'a'*40, 'source_tree_sha256': 'b'*64, 'source_files': {'main.c': 'c'*64}}
        run = {'id': 123, 'status': 'completed', 'conclusion': 'success', 'head_sha': 'd'*40,
               'repository': {'id': 987, 'full_name': build.REPOSITORY},
               'head_repository': {'id': 987, 'full_name': build.REPOSITORY},
               'url': 'https://api.github.com/repos/'+build.REPOSITORY+'/actions/runs/123',
               'html_url': 'https://github.com/'+build.REPOSITORY+'/actions/runs/123'}
        jobs = [{'name': job, 'run_id': 123, 'status': 'completed', 'conclusion': 'success'} for job in sorted(build.JOBS)]
        result = {'run': run, 'jobs': jobs, 'tested_source_receipts': {}, 'artifact_downloads': {}, 'api_evidence': {}}
        artifacts = []
        for index, (name, (filename, job)) in enumerate(sorted(build.CI_ARTIFACTS.items()), 1):
            if job:
                receipt = {'schema': 'x6200.usb.ci-tested-source.v1', **identity,
                           'expected_source_commit': identity['source_commit'], 'source_file_count': 1,
                           'source_worktree_dirty': dirty, 'workflow_commit': 'd'*40, 'job': job}
            else:
                receipt = {'sha256': 'e'*64, 'expected_sha256': 'e'*64, 'tested_source_commit': 'a'*40,
                           'workflow_commit': 'd'*40, 'product_source_modified': False, 'unchanged_opus_self_verification': True}
            path = root/filename
            path.write_text(json.dumps(receipt))
            entry = {'path': str(path), 'sha256': build.sha(path), 'receipt': receipt}
            if job:
                result['tested_source_receipts'][job] = entry
            else:
                result['model_acquisition_receipt'] = entry
            archive = root/(name+'.zip')
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.write(path, filename)
            digest = build.sha(archive)
            result['artifact_downloads'][name] = {'path': str(archive), 'sha256': digest,
                'github_api_digest': 'sha256:'+digest, 'artifact_id': index}
            artifacts.append({'id': index, 'name': name, 'expired': False, 'size_in_bytes': archive.stat().st_size,
                'digest': 'sha256:'+digest, 'archive_download_url': 'https://api.github.com/repos/'+build.REPOSITORY+'/actions/artifacts/'+str(index)+'/zip',
                'workflow_run': {'id': 123, 'head_sha': 'd'*40, 'repository_id': 987}})
        for name, receipt in (('run', run), ('jobs', {'total_count': 3, 'jobs': jobs}),
                              ('artifacts', {'total_count': 4, 'artifacts': artifacts})):
            path = root/(name+'-api.json')
            path.write_text(json.dumps(receipt))
            result['api_evidence'][name] = {'path': str(path), 'sha256': build.sha(path), 'receipt': receipt}
        return identity, result

    def test_ci_requires_exact_clean_source_receipts_and_model_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity, value = self.ci_fixture(Path(temporary))
            original_read = build.read
            def fake_read(path):
                if str(path).endswith('/tools/freedv/sources.json'):
                    return {'opus': {'model_sha256': 'e'*64}}
                return original_read(path)
            with patch.object(build, 'read', side_effect=fake_read):
                self.assertEqual(build.validate_ci(value, identity)['status'], 'PASS')
                value['run']['url'] = 'https://github.com/Guru-RF/OpenX6X00/actions/runs/123'
                with self.assertRaises(ValueError):
                    build.validate_ci(value, identity)
        with tempfile.TemporaryDirectory() as temporary:
            identity, value = self.ci_fixture(Path(temporary), dirty=True)
            with patch.object(build, 'read', side_effect=fake_read):
                with self.assertRaises(ValueError):
                    build.validate_ci(value, identity)

    def test_ci_handwritten_pass_without_actual_downloads_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity, value = self.ci_fixture(Path(temporary))
            value.pop('artifact_downloads')
            with self.assertRaisesRegex(ValueError, 'four actual downloaded'):
                build.validate_ci(value, identity)

    def test_ci_download_zip_must_match_actual_api_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity, value = self.ci_fixture(Path(temporary))
            entry = value['artifact_downloads']['tested-source-windows-runner']
            archive = Path(entry['path'])
            with archive.open('ab') as stream:
                stream.write(b'tampering')
            entry['sha256'] = build.sha(archive)
            with self.assertRaisesRegex(ValueError, 'API/ZIP binding'):
                build.validate_ci(value, identity)

    def test_ci_receipt_from_a_different_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity, value = self.ci_fixture(Path(temporary))
            value['tested_source_receipts']['windows-runner'] = value['tested_source_receipts']['linux-inventory']
            with self.assertRaisesRegex(ValueError, 'not the member'):
                build.validate_ci(value, identity)

    def test_ci_artifacts_from_a_different_run_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity, value = self.ci_fixture(Path(temporary))
            api = value['api_evidence']['artifacts']
            api['receipt']['artifacts'][0]['workflow_run']['id'] = 124
            Path(api['path']).write_text(json.dumps(api['receipt']))
            api['sha256'] = build.sha(api['path'])
            with self.assertRaisesRegex(ValueError, 'API/ZIP binding'):
                build.validate_ci(value, identity)

    def test_build_matrix_preserves_operational_and_full_qualification_flags(self):
        args = argparse.Namespace(work=Path('/work'), build_id='test', jobs=4,
            control_headers=Path('/headers'), host_codec=Path('/host-codec'), fixtures=Path('/fixtures'),
            sdk=Path('/sdk'), codec=Path('/codec'), x6100_prefix=Path('/x6100'), runtime_inputs=Path('/runtime-inputs.json'))
        commands = build.build_commands(args)
        self.assertEqual(set(commands), set(build.VARIANTS))
        self.assertIn('--usb-voice', commands['arm'])
        self.assertIn('--runtime-inputs', commands['arm'])
        self.assertIn('--usb-test', commands['host-native'])
        self.assertNotIn('--usb-test', commands['normal'])
        self.assertNotIn('--only', commands['host-native'])


if __name__ == '__main__':
    unittest.main()
