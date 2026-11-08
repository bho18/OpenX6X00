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
import subprocess
import sqlite3
import stat
import unittest
from unittest.mock import patch
import zipfile

import build_operational_release as build
import operational_clock_probe as clock
import publish_operational_release as publish
import release_cache
import stage_operational_publish as transport
from release_guide import render_guide, validate_guide_copies


class ReleaseBoundaries(unittest.TestCase):
    def test_package_stage_bootstraps_observer_before_source_and_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ['build_operational_release.py', '--stage', 'package', '--expected-commit', 'a'*40,
                    '--build-id', 'fixture', '--observer-provenance', str(root/'observer.json')]
            for name in ('work', 'output', 'sdk', 'codec', 'host-codec', 'fixtures', 'baseline', 'control-headers', 'x6100-prefix'):
                argv += ['--'+name, str(root/name)]
            order = []
            identity = {'source_commit': 'a'*40, 'source_tree_sha256': 'b'*64,
                        'source_files': {'main.c': 'c'*64}, 'source_file_count': 1}
            def observer(path):
                self.assertEqual(path, root/'observer.json')
                order.append('observer')
            def source(expected):
                order.append('source')
                return identity
            with patch.object(sys, 'argv', argv), patch.object(build, 'validate_observer', side_effect=observer), \
                    patch.object(build, 'source_identity', side_effect=source), \
                    patch.object(build, 'package', side_effect=lambda args: order.append('package')):
                build.main()
            self.assertEqual(order, ['observer', 'source', 'package'])

    def test_working_gui_database_is_writable_without_changing_sealed_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'sealed-params.db'
            with sqlite3.connect(source) as database:
                database.execute('CREATE TABLE defaults(value INTEGER)')
                database.execute('INSERT INTO defaults VALUES(42)')
            source.chmod(0o555)
            original = build.sha(source)
            directory = root/'working'
            directory.mkdir()
            fixture = build.prepare_host_smoke_database({'path': str(source), 'sha256': original}, directory)
            working = directory/'params.db'
            self.assertEqual(fixture['status'], 'PASS')
            self.assertEqual(build.sha(working), original)
            self.assertEqual(stat.S_IMODE(working.stat().st_mode), 0o600)
            with sqlite3.connect(working) as database:
                database.execute('CREATE TABLE migration_probe(value INTEGER)')
                database.execute('INSERT INTO migration_probe VALUES(7)')
            self.assertNotEqual(build.sha(working), original)
            self.assertEqual(build.sha(source), original)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o555)
    def test_guide_render_changes_current_release_and_preserves_fallback(self):
        template = (b'Use the `freedv-operational-beta-20261107.3` GitHub release.\n'
                    b'Keep `freedv-operational-beta-20261107.2` as fallback.\n'
                    b'[Strategy](usb-test-strategy.md)\n')
        actual = render_guide(template, 'freedv-operational-beta-20261108.1')
        self.assertIn(b'Use the `freedv-operational-beta-20261108.1`', actual)
        self.assertIn(b'Keep `freedv-operational-beta-20261107.2`', actual)
        self.assertIn(b'/blob/freedv-operational-beta-20261108.1/docs/freedv/usb-test-strategy.md', actual)
        with self.assertRaises(ValueError):
            render_guide(template, 'bad\nidentity')
        with self.assertRaises(ValueError):
            render_guide(b'No current release marker', 'freedv-operational-beta-20261108.1')

    def kit_fixture(self, root, guide=None):
        tag = 'freedv-operational-beta-20261108.1'
        image = {'software_gates_passed': True, 'usb_rf_test_ready': True, 'on_device_readiness_required': True,
                 'usb_startup_contract_version': 4, 'usb_output_contract_version': 1,
                 'usb_capture_open_contract_version': 1, 'usb_refinement_contract_version': 2,
                 'gui_sha256': 'a'*64, 'runner_sha256': 'b'*64, 'rade_sha256': 'c'*64,
                 'image_sha256': 'd'*64, 'firmware_commit': 'e'*40, 'build_id': tag}
        manifest = root/'image-manifest.json'
        manifest.write_text(json.dumps(image))
        command = [sys.executable, str(Path(publish.__file__).with_name('build_usb_rf_kit.py')),
                   '--image-manifest', str(manifest), '--output', str(root/'usb-rf-test-kit.zip')]
        if guide is not None:
            command += ['--guide', str(guide)]
        outcome = subprocess.run(command, capture_output=True)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        return image

    def test_real_kit_builder_uses_rendered_guide_and_exact_override(self):
        raw = (publish.ROOT/'docs/freedv/usb-rf-end-to-end.md').read_bytes()
        expected = render_guide(raw, 'freedv-operational-beta-20261108.1')
        for override in (False, True):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                guide = root/'rendered-guide.md'
                guide.write_bytes(expected)
                self.kit_fixture(root, guide if override else None)
                with zipfile.ZipFile(root/'usb-rf-test-kit.zip') as zipped:
                    manifest = json.loads(zipped.read('rf-test-manifest.json'))
                    self.assertEqual(zipped.read('START-HERE.md'), expected)
                    self.assertEqual(manifest['files']['START-HERE.md'], hashlib.sha256(expected).hexdigest())
                    for name, digest in manifest['files'].items():
                        self.assertEqual(hashlib.sha256(zipped.read(name)).hexdigest(), digest)

    def test_valid_self_consistent_kit_with_stale_guide_is_rejected(self):
        raw = (publish.ROOT/'docs/freedv/usb-rf-end-to-end.md').read_bytes()
        expected = render_guide(raw, 'freedv-operational-beta-20261108.1')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.kit_fixture(root)
            (root/'START-HERE.md').write_bytes(expected)
            with zipfile.ZipFile(root/'x6200-usb-rf-windows.zip', 'w') as image:
                image.writestr('START-HERE.md', expected)
            self.assertEqual(validate_guide_copies(root), hashlib.sha256(expected).hexdigest())
            kit = root/'usb-rf-test-kit.zip'
            with zipfile.ZipFile(kit) as zipped:
                payloads = {name: zipped.read(name) for name in zipped.namelist()}
            payloads['START-HERE.md'] = raw
            manifest = json.loads(payloads['rf-test-manifest.json'])
            manifest['files']['START-HERE.md'] = hashlib.sha256(raw).hexdigest()
            payloads['rf-test-manifest.json'] = json.dumps(manifest).encode()
            with zipfile.ZipFile(kit, 'w') as zipped:
                for name, data in payloads.items():
                    zipped.writestr(name, data)
            with self.assertRaisesRegex(ValueError, 'guide differs'):
                validate_guide_copies(root)

    def test_transport_reconstructs_exact_assets_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root/'assets'
            assets.mkdir()
            for name in publish.ASSETS:
                (assets/name).write_bytes((name+' fixture').encode())
            hashes = {name: publish.sha(assets/name) for name in publish.ASSETS}
            notes = root/'notes.md'
            notes.write_text('Verified fixture notes')
            with patch.object(transport, 'validate_assets', return_value={'asset_sha256': hashes}), patch.object(transport, 'CHUNK_BYTES', 7):
                transport.stage(assets, 'freedv-operational-beta-20261108.1', 'a'*40, notes, root/'transport')
            assembled = root/'reconstructed'
            result = subprocess.run([sys.executable, str(root/'transport/assemble.py'), str(assembled)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({name: publish.sha(assembled/name) for name in publish.ASSETS}, hashes)
            part = root/'transport/assets/part-0000'
            part.write_bytes(b'tampered')
            result = subprocess.run([sys.executable, str(root/'transport/assemble.py'), str(root/'tampered')], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

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

    def test_publish_preflights_download_before_creating_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notes = root/'notes.md'
            notes.write_text('Verified notes')
            existing = root/'downloads'
            existing.mkdir()
            with patch.object(publish.subprocess, 'run') as run, patch.object(publish, 'validate_assets') as validate:
                with self.assertRaisesRegex(ValueError, 'fresh download'):
                    publish.publish(root/'assets', build.REPOSITORY, 'freedv-operational-beta-20261108.1', 'a'*40, notes, existing)
                run.assert_not_called()
                validate.assert_not_called()

    def test_standalone_publisher_rejects_feature_disabled_builds(self):
        import build_native_development
        tag = 'freedv-operational-beta-20261108.1'
        verification = {'source_tree_sha256': 'b'*64, 'source_files': {'main.c': 'c'*64}, 'build_records': {}}
        for name, variant in build.VARIANTS.items():
            verification['build_records'][name] = {'variant': variant, 'source_commit': 'a'*40, 'source_tree_sha256': 'b'*64,
                'source_files': verification['source_files'], 'build_id': tag, 'usb_voice_enabled': True}
        with patch.object(build_native_development, 'validate_build_record', side_effect=lambda record, **kwargs: record):
            publish.validate_operational_build_records(verification, tag, 'a'*40)
            for name in ('arm', 'host-native'):
                verification['build_records'][name]['usb_voice_enabled'] = False
                with self.assertRaisesRegex(ValueError, 'feature was not enabled'):
                    publish.validate_operational_build_records(verification, tag, 'a'*40)
                verification['build_records'][name]['usb_voice_enabled'] = True

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
