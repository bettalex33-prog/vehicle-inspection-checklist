"""All backup, archive attack, and restore tests use injected temporary paths."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

import data_backup as db
from checklist import create_default_data
from inspection_history import create_inspection_record, create_default_findings


def record():
    return create_inspection_record("Unit", "123", "notes", create_default_data(),
                                   findings=create_default_findings(), assessment="Ready for service")


class TestBackup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = db.RuntimePaths(*(self.root / n for n in db.NAMES))
        self.mapping = self.paths.mapping()
        self.stamp = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.samples = {
            db.NAMES[0]: json.dumps(create_default_data(), indent=4).encode(),
            db.NAMES[1]: json.dumps({'schema_version': 2, 'inspections': [record()]}, indent=3).encode(),
            db.NAMES[2]: b'{"schema_version": 1, "profiles": []}\n',
            db.NAMES[3]: b'{"schema_version": 1, "tasks": []}\n'}

    def populate(self, names=None):
        for name in self.samples if names is None else names:
            self.mapping[name].write_bytes(self.samples[name])

    def manifest(self, payloads):
        return dict(manifest_version=1, application='vehicle-maintenance-checklist', release='1.6.0',
                    created_at=self.stamp.isoformat(), files=[dict(filename=n, size=len(p), sha256=hashlib.sha256(p).hexdigest()) for n, p in sorted(payloads.items())])

    def archive(self, payloads=None, manifest=None, extras=(), compression=zipfile.ZIP_DEFLATED):
        payloads = self.samples if payloads is None else payloads
        path = self.root / 'input.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w', compression=compression) as archive:
                archive.writestr('manifest.json', json.dumps(self.manifest(payloads) if manifest is None else manifest))
                for name, payload in payloads.items():
                    archive.writestr(name, payload)
                for name, payload in extras:
                    archive.writestr(name, payload)
        return path

    def test_all_subsets_roundtrip_and_omissions(self):
        for mask in range(1, 16):
            with self.subTest(mask=mask):
                for path in self.mapping.values():
                    path.unlink(missing_ok=True)
                names = [n for i, n in enumerate(db.NAMES) if mask & (1 << i)]
                self.populate(names)
                output_dir = self.root / str(mask); output_dir.mkdir()
                archive = db.create_backup(self.paths, output_dir, timestamp=self.stamp)
                validated = db.validate_backup(archive)
                self.assertEqual(dict(validated.files), {n: self.samples[n] for n in names})
                with zipfile.ZipFile(archive) as z:
                    self.assertEqual(set(z.namelist()), {'manifest.json', *names})
                for n, path in self.mapping.items():
                    path.write_bytes(b'original-' + n.encode())
                result = db.restore_backup(validated, self.paths, confirmed=True)
                self.assertEqual(set(result.restored), set(names))
                for n, path in self.mapping.items():
                    self.assertEqual(path.read_bytes(), self.samples[n] if n in names else b'original-' + n.encode())

    def test_v170_metadata_and_four_release_compatibility(self):
        self.populate()
        backup = db.create_backup(self.paths, self.root, timestamp=self.stamp)
        with zipfile.ZipFile(backup) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['release'], '1.7.0')
        self.assertEqual(manifest['manifest_version'], 1)
        self.assertEqual({entry['filename'] for entry in manifest['files']}, set(db.NAMES))
        for release in ('1.4.0', '1.5.0', '1.6.0', '1.7.0'):
            payloads = {n: p for n, p in self.samples.items()
                        if release != '1.4.0' or n != 'maintenance_schedule.json'}
            manifest = self.manifest(payloads); manifest['release'] = release
            validated = db.validate_backup(self.archive(payloads, manifest))
            for path in self.mapping.values():
                path.write_bytes(b'original')
            db.restore_backup(validated, self.paths, confirmed=True)
            for name, path in self.mapping.items():
                self.assertEqual(path.read_bytes(), payloads.get(name, b'original'))

    def test_v170_and_v160_accept_schema_two_without_weakening_older_rules(self):
        from test_fleet_dashboard import task, service
        payloads = dict(self.samples)
        payloads['maintenance_schedule.json'] = json.dumps({'schema_version': 2,
            'tasks': [task(enabled=False, archived=True, services=[service(labor_cost='62.37')])]}, indent=4).encode()
        for release in ('1.4.0', '1.5.0', '1.6.0', '1.7.0'):
            manifest = self.manifest(payloads); manifest['release'] = release
            if release in ('1.4.0', '1.5.0'):
                with self.assertRaises(ValueError):
                    db.validate_backup(self.archive(payloads, manifest))
            else:
                validated = db.validate_backup(self.archive(payloads, manifest))
                self.assertEqual(dict(validated.files), payloads)

    def test_schema_bytes_preserved(self):
        for version in (1, 2, 3):
            r = record()
            if version == 1:
                r.pop('findings'); r.pop('assessment')
            payload = json.dumps({'schema_version': version, 'inspections': [r]}, indent=7).encode() + b'\n\n'
            validated = db.validate_backup(self.archive({db.NAMES[1]: payload}))
            db.restore_backup(validated, self.paths, confirmed=True)
            self.assertEqual(self.paths.history.read_bytes(), payload)
            self.assertFalse(self.paths.checklist.exists())
            self.assertFalse(self.paths.profiles.exists())

    def test_backup_no_empty_no_overwrite_no_unrelated(self):
        with self.assertRaises(ValueError):
            db.create_backup(self.paths, self.root)
        self.populate()
        (self.root / 'source.py').write_text('secret')
        archive = db.create_backup(self.paths, self.root, timestamp=self.stamp)
        before = archive.read_bytes()
        with self.assertRaises(FileExistsError):
            db.create_backup(self.paths, self.root, timestamp=self.stamp)
        self.assertEqual(archive.read_bytes(), before)
        self.paths.checklist.write_text('invalid')
        with self.assertRaises(ValueError):
            db.create_backup(self.paths, self.root)

    def test_confirmation_and_revalidation(self):
        self.populate()
        originals = {n: p.read_bytes() for n, p in self.mapping.items()}
        with self.assertRaises(ValueError):
            db.restore_backup(db.ValidatedBackup('', tuple(self.samples.items())), self.paths)
        for files in ((), ((db.NAMES[0], b'{}'),), ((db.NAMES[0], self.samples[db.NAMES[0]]),) * 2,
                      (('../evil', b'{}'),)):
            with self.assertRaises(ValueError):
                db.restore_backup(db.ValidatedBackup('', files), self.paths, confirmed=True)
        self.assertEqual({n: p.read_bytes() for n, p in self.mapping.items()}, originals)

    def test_member_rejections(self):
        for name in ('../evil', '/evil', 'nested/file', 'nested\\file', 'unknown.json', 'folder/', db.NAMES[0], 'manifest.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                db.validate_backup(self.archive(extras=[(name, b'{}')]))
        info = zipfile.ZipInfo('vehicle_profiles.json')
        info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            db.validate_backup(self.archive({db.NAMES[0]: self.samples[db.NAMES[0]]}, extras=[(info, b'target')]))
        with self.assertRaises(ValueError):
            db.validate_backup(self.archive(compression=zipfile.ZIP_BZIP2))
        with self.assertRaises(ValueError):
            db.validate_backup(self.archive({}))
        path = self.root / 'bad.zip'; path.write_bytes(b'not ZIP')
        with self.assertRaises(ValueError):
            db.validate_backup(path)

    def test_manifest_rejections(self):
        base = self.manifest(self.samples)
        variants = []
        for key, value in [('manifest_version', 2), ('manifest_version', True), ('application', 'other'),
                           ('created_at', 'naive'), ('created_at', None), ('release', '9'), ('files', {})]:
            m = deepcopy(base); m[key] = value; variants.append(m)
        m = deepcopy(base); m['extra'] = 1; variants.append(m)
        m = deepcopy(base); m['files'].append(deepcopy(m['files'][0])); variants.append(m)
        m = deepcopy(base); m['files'].pop(); variants.append(m)
        for key, value in [('sha256', '0' * 64), ('size', True), ('size', -1), ('filename', '../evil'), ('filename', [])]:
            m = deepcopy(base); m['files'][0][key] = value; variants.append(m)
        for m in variants:
            with self.subTest(manifest=m), self.assertRaises(ValueError):
                db.validate_backup(self.archive(manifest=m))

    def test_invalid_json_and_size_limits(self):
        for name in db.NAMES:
            for payload in (b'not json', b'\xff', b'{"x":1,"x":2}', b'NaN', b'{}'):
                with self.subTest(name=name, payload=payload), self.assertRaises(ValueError):
                    db.validate_backup(self.archive({name: payload}))
        archive = self.archive()
        for constant in ('MAX_ARCHIVE', 'MAX_FILE', 'MAX_MANIFEST', 'MAX_TOTAL'):
            with patch.object(db, constant, 1), self.assertRaises(ValueError):
                db.validate_backup(archive)

    def test_destination_symlinks_special_and_aliases(self):
        self.populate()
        self.paths.profiles.unlink(); self.paths.profiles.symlink_to(self.paths.checklist)
        with self.assertRaises(ValueError):
            db.create_backup(self.paths, self.root)
        self.paths.profiles.unlink(); os.link(self.paths.checklist, self.paths.profiles)
        with self.assertRaises(ValueError):
            db.create_backup(self.paths, self.root)
        self.paths.profiles.unlink(); self.paths.profiles.mkdir()
        with self.assertRaises(ValueError):
            db.create_backup(self.paths, self.root)
        with self.assertRaises(ValueError):
            db.reject_output_alias(self.paths.checklist, self.mapping.values())

    def test_replacement_failures_rollback_originals_and_absence(self):
        for failing_index in (1, 2, 3, 4):
            for originally_present in (False, True):
                with self.subTest(index=failing_index, present=originally_present):
                    for p in self.mapping.values():
                        p.unlink(missing_ok=True)
                    if originally_present:
                        self.populate()
                    originals = {n: p.read_bytes() if p.exists() else None for n, p in self.mapping.items()}
                    replacement = dict(self.samples)
                    replacement[db.NAMES[2]] = b'{"profiles":[],"schema_version":1}'
                    actual = db.os.replace
                    calls = 0
                    def fail_once(src, dst):
                        nonlocal calls
                        calls += 1
                        if calls == failing_index:
                            raise OSError('injected replace failure')
                        return actual(src, dst)
                    with patch.object(db.os, 'replace', side_effect=fail_once), self.assertRaises(db.RestoreError):
                        db.restore_backup(db.ValidatedBackup('', tuple(replacement.items())), self.paths, confirmed=True)
                    self.assertEqual({n: p.read_bytes() if p.exists() else None for n, p in self.mapping.items()}, originals)
                    self.assertFalse(list(self.root.glob('.*.tmp')))
                    self.assertFalse(list(self.root.glob('.*.recovery')))

    def test_staging_fsync_and_conflict_failures(self):
        self.populate()
        originals = {n: p.read_bytes() for n, p in self.mapping.items()}
        backup = db.ValidatedBackup('', tuple(self.samples.items()))
        for target in ('_stage', '_sync_dir'):
            with patch.object(db, target, side_effect=OSError('failure')), self.assertRaises(db.RestoreError):
                db.restore_backup(backup, self.paths, confirmed=True)
            self.assertEqual({n: p.read_bytes() for n, p in self.mapping.items()}, originals)
        actual = db._stage
        def change_after_stage(path, payload, suffix='.tmp'):
            result = actual(path, payload, suffix)
            self.paths.checklist.write_bytes(b'changed concurrently')
            return result
        with patch.object(db, '_stage', side_effect=change_after_stage), self.assertRaises(db.RestoreError):
            db.restore_backup(backup, self.paths, confirmed=True)
        self.assertEqual(self.paths.checklist.read_bytes(), b'changed concurrently')

    def test_failed_rollback_retains_and_reports_all_recovery_paths(self):
        self.populate()
        actual = db.os.replace
        count = 0
        def failing(src, dst):
            nonlocal count
            count += 1
            if count >= 2:
                raise OSError('disk unavailable')
            return actual(src, dst)
        with patch.object(db.os, 'replace', side_effect=failing), self.assertRaises(db.RestoreError) as caught:
            db.restore_backup(db.ValidatedBackup('', tuple(self.samples.items())), self.paths, confirmed=True)
        recovery = list(self.root.glob('.*.recovery'))
        self.assertEqual(len(recovery), 4)
        for path in recovery:
            self.assertIn(str(path), str(caught.exception))
        self.assertIn('rollback incomplete', str(caught.exception))

    def test_corrupt_crc_encrypted_and_truncated_archives(self):
        import struct
        path = self.archive(compression=zipfile.ZIP_STORED)
        raw = path.read_bytes()
        # Change a payload without updating either CRC or SHA-256.
        modified = bytearray(raw)
        position = raw.index(self.samples[db.NAMES[0]])
        modified[position] ^= 1
        path.write_bytes(modified)
        with self.assertRaises(ValueError):
            db.validate_backup(path)
        # Mark the first central-directory member as encrypted.
        modified = bytearray(raw)
        central = raw.index(b'PK\x01\x02')
        flags = struct.unpack_from('<H', modified, central + 8)[0]
        struct.pack_into('<H', modified, central + 8, flags | 1)
        path.write_bytes(modified)
        with self.assertRaises(ValueError):
            db.validate_backup(path)
        path.write_bytes(raw[:-30])
        with self.assertRaises(ValueError):
            db.validate_backup(path)

    def test_duplicate_manifest_json_keys_and_missing_manifest(self):
        path = self.root / 'input.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', '{"manifest_version":1,"manifest_version":1}')
            archive.writestr(db.NAMES[0], self.samples[db.NAMES[0]])
        with self.assertRaises(ValueError):
            db.validate_backup(path)
        with zipfile.ZipFile(path, 'w') as archive:
            for n, p in self.samples.items():
                archive.writestr(n, p)
        with self.assertRaises(ValueError):
            db.validate_backup(path)

    def test_stage_fsync_failures_clean_temporary_files(self):
        self.populate()
        originals = {n: p.read_bytes() for n, p in self.mapping.items()}
        actual = db.os.fsync
        for failing_call in range(1, 9):
            count = 0
            def fsync(descriptor):
                nonlocal count
                count += 1
                if count == failing_call:
                    raise OSError('injected fsync failure')
                return actual(descriptor)
            with patch.object(db.os, 'fsync', side_effect=fsync), self.assertRaises(db.RestoreError):
                db.restore_backup(db.ValidatedBackup('', tuple(self.samples.items())), self.paths, confirmed=True)
            self.assertEqual({n: p.read_bytes() for n, p in self.mapping.items()}, originals)
            self.assertFalse(list(self.root.glob('.*.tmp')))
            self.assertFalse(list(self.root.glob('.*.recovery')))

    def test_archive_source_alias_rejected_on_restore(self):
        validated = db.validate_backup(self.archive())
        aliased = db.RuntimePaths(validated.source_path, self.paths.history, self.paths.profiles)
        before = validated.source_path.read_bytes()
        with self.assertRaises(ValueError):
            db.restore_backup(validated, aliased, confirmed=True)
        self.assertEqual(validated.source_path.read_bytes(), before)

    def test_legacy_release_retains_maintenance_and_rejects_new_member(self):
        self.populate()
        legacy = {name: payload for name, payload in self.samples.items() if name != db.NAMES[3]}
        manifest = self.manifest(legacy); manifest['release'] = '1.4.0'
        archive = self.archive(legacy, manifest)
        before = self.paths.maintenance if self.paths.maintenance else self.mapping[db.NAMES[3]]
        original = before.read_bytes()
        backup = db.validate_backup(archive)
        result = db.restore_backup(backup, self.paths, confirmed=True)
        self.assertIn(db.NAMES[3], result.retained)
        self.assertEqual(before.read_bytes(), original)
        manifest = self.manifest(self.samples); manifest['release'] = '1.4.0'
        with self.assertRaises(ValueError):
            db.validate_backup(self.archive(manifest=manifest))

    def test_invalid_maintenance_and_total_limit(self):
        malformed = {db.NAMES[3]: b'{"schema_version":1,"tasks":[{}]}'}
        with self.assertRaises(ValueError):
            db.validate_backup(self.archive(malformed))
        self.populate()
        with patch.object(db, 'MAX_TOTAL', 1), self.assertRaises(ValueError):
            db.create_backup(self.paths, self.root)

    def test_custom_maintenance_path_and_output_alias(self):
        custom = self.root / 'custom-maintenance.json'
        paths = db.RuntimePaths(self.paths.checklist, self.paths.history, self.paths.profiles, custom)
        custom.write_bytes(self.samples[db.NAMES[3]])
        archive = db.create_backup(paths, self.root, timestamp=self.stamp)
        validated = db.validate_backup(archive)
        self.assertEqual(dict(validated.files), {db.NAMES[3]: custom.read_bytes()})
        with self.assertRaises(ValueError):
            db.reject_output_alias(custom, paths.mapping().values())
        db.restore_backup(validated, paths, confirmed=True)
        self.assertFalse(self.mapping[db.NAMES[3]].exists())

    def test_snapshot_and_archived_history_bytes_roundtrip_without_profile(self):
        from maintenance_schedule import create_task, record_service, archive_task
        from vehicle_profiles import create_profile
        from test_maintenance_schedule import details, TODAY
        profile = create_profile(self.paths.profiles, 'Original', '', '', '', '42000')
        task = create_task(self.mapping[db.NAMES[3]], profile['profile_id'], details(),
                           profiles_path=self.paths.profiles, today=TODAY)
        task = record_service(self.mapping[db.NAMES[3]], task['task_id'],
                              dict(completed_date='2026-10-22', mileage=46500, notes='é done'),
                              profiles_path=self.paths.profiles, expected_task=task, today=TODAY)
        archive_task(self.mapping[db.NAMES[3]], task['task_id'], expected_task=task)
        self.paths.profiles.unlink()
        original = self.mapping[db.NAMES[3]].read_bytes()
        archive = db.create_backup(self.paths, self.root, timestamp=self.stamp)
        self.mapping[db.NAMES[3]].unlink()
        db.restore_backup(db.validate_backup(archive), self.paths, confirmed=True)
        self.assertEqual(self.mapping[db.NAMES[3]].read_bytes(), original)
        self.assertFalse(self.paths.profiles.exists())

    def test_work_order_exact_roundtrip_and_legacy_backups_without_fabrication(self):
        import maintenance_schedule as ms
        from vehicle_profiles import create_profile
        from test_maintenance_schedule import details, TODAY
        profile = create_profile(self.paths.profiles, 'Unit', '', '', '', '43000')
        schedule_path = self.mapping[db.NAMES[3]]
        task = ms.create_task(schedule_path, profile['profile_id'], details(), profiles_path=self.paths.profiles, today=TODAY)
        legacy = dict(service_id='00000000-0000-4000-8000-000000000099', completed_date='2026-10-21', mileage=46000, notes='Legacy')
        task['services'] = [legacy]
        legacy_bytes = json.dumps({'schema_version': 1, 'tasks': [task]}, indent=4).encode()
        for release in ('1.5.0', '1.6.0'):
            payloads = {db.NAMES[3]: legacy_bytes}
            manifest = self.manifest(payloads); manifest['release'] = release
            backup = db.validate_backup(self.archive(payloads, manifest))
            db.restore_backup(backup, self.paths, confirmed=True)
            self.assertEqual(schedule_path.read_bytes(), legacy_bytes)
            self.assertEqual(ms.load_schedule(schedule_path)['tasks'][0]['services'], [legacy])
        values = ms.validate_service_details('2026-10-22', '48001', 'é work', today=TODAY,
                            provider='Shop', invoice_reference='INV-1', labor_cost='10.01', parts_cost='0.00')
        updated = ms.record_service(schedule_path, task['task_id'], values, profiles_path=self.paths.profiles,
                                    expected_task=task, today=TODAY)
        ms.archive_task(schedule_path, task['task_id'], expected_task=updated)
        original = schedule_path.read_bytes()
        archive = db.create_backup(self.paths, self.root, timestamp=self.stamp)
        backup = db.validate_backup(archive)
        schedule_path.unlink()
        db.restore_backup(backup, self.paths, confirmed=True)
        self.assertEqual(schedule_path.read_bytes(), original)
        services = ms.load_schedule(schedule_path)['tasks'][0]['services']
        self.assertEqual(services[0], legacy)
        self.assertEqual(services[1]['total_cost_cents'], 1001)
        self.assertIsNone(services[1]['tax_cents'])
        self.assertEqual(services[1]['parts_cost_cents'], 0)
        payloads = {db.NAMES[3]: original}
        manifest = self.manifest(payloads); manifest['release'] = '1.5.0'
        with self.assertRaises(ValueError):
            db.validate_backup(self.archive(payloads, manifest))
