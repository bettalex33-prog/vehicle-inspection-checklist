"""Read-only health assessment: synthetic archives and temporary paths only."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

import backup_health as bh
import data_backup as db
from checklist import create_default_data


class TestBackupHealth(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.mapping = {name: self.root / name for name in db.NAMES}
        self.paths = db.RuntimePaths(*(self.mapping[name] for name in db.NAMES))
        self.samples = {
            db.NAMES[0]: json.dumps(create_default_data(), indent=2).encode(),
            db.NAMES[1]: b'{"schema_version": 2, "inspections": []}\n',
            db.NAMES[2]: b'{"schema_version": 1, "profiles": []}\n',
            db.NAMES[3]: b'{"schema_version": 1, "tasks": []}\n'}
        for name, raw in self.samples.items():
            self.mapping[name].write_bytes(raw)
        self.archive_path = self.root / 'explicit.zip'

    def manifest(self, payloads):
        return dict(manifest_version=1, application='vehicle-maintenance-checklist', release='1.9.0',
            created_at='2026-10-22T12:34:56.123456+00:00', files=[dict(filename=name,
                size=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in sorted(payloads.items())])

    def archive(self, payloads=None, manifest=None, manifest_raw=None, extras=(),
                include_manifest=True, compression=zipfile.ZIP_DEFLATED):
        payloads = self.samples if payloads is None else payloads
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.archive_path, 'w', compression=compression) as stream:
                if include_manifest:
                    raw = manifest_raw if manifest_raw is not None else json.dumps(
                        self.manifest(payloads) if manifest is None else manifest).encode()
                    stream.writestr('manifest.json', raw)
                for name, raw in payloads.items():
                    stream.writestr(name, raw)
                for name, raw in extras:
                    stream.writestr(name, raw)
        return self.archive_path

    def assess(self, archive=None):
        return bh.assess_backup_health(self.archive_path if archive is None else archive, self.paths)

    def stores(self, model):
        return {row['name']: row for row in model['stores']}

    def tree(self):
        return {str(path.relative_to(self.root)): ('directory', path.stat().st_mtime_ns, path.stat().st_mode)
                if path.is_dir() else (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                for path in self.root.rglob('*')}

    def test_no_archive_supplied_is_unknown_not_never_backed_up(self):
        model = bh.assess_backup_health(None, self.paths).model
        self.assertEqual(model['coverage'], 'unknown')
        self.assertEqual(model['unavailable_reason'], 'no_archive_supplied')
        self.assertEqual(model['archive']['presence'], 'Not supplied')
        self.assertIsNone(model['archive']['zip_valid'])
        self.assertFalse(model['healthy'])
        self.assertTrue(all(row['current_readability'] == 'Readable' for row in model['stores']))
        self.assertTrue(all(row['exact_byte_match'] is None for row in model['stores']))

    def test_missing_archive_is_not_invalid(self):
        model = self.assess().model
        self.assertEqual(model['archive']['presence'], 'Missing')
        self.assertIsNone(model['archive']['zip_valid'])
        self.assertEqual(model['coverage'], 'unknown')
        self.assertEqual(model['unavailable_reason'], 'archive_missing')
        self.assertFalse(model['healthy'])

    def test_valid_complete_exact_bytes_and_recorded_timestamp(self):
        self.archive()
        os.utime(self.archive_path, ns=(1234567890, 1234567890))
        model = self.assess().model
        self.assertEqual(model['coverage'], 'complete')
        self.assertTrue(model['healthy'])
        for key in ('zip_valid', 'members_safe', 'manifest_valid', 'compatible',
                    'timestamp_valid', 'checksum_valid', 'payload_valid'):
            self.assertIs(model['archive'][key], True, key)
        self.assertEqual(model['archive']['created_at'], self.manifest(self.samples)['created_at'])
        self.assertEqual(model['archive']['release'], '1.9.0')
        self.assertEqual(model['archive']['included_stores'], sorted(db.NAMES))
        self.assertTrue(all(row['exact_byte_match'] is True for row in model['stores']))

    def test_valid_archive_changed_current_bytes_are_not_corruption(self):
        self.archive()
        path = self.mapping[db.NAMES[2]]
        path.write_bytes(b'{ "profiles": [], "schema_version": 1 }\n')
        model = self.assess().model
        self.assertEqual(model['coverage'], 'changed')
        self.assertTrue(model['archive']['zip_valid'])
        self.assertTrue(model['archive']['checksum_valid'])
        self.assertFalse(model['healthy'])
        self.assertFalse(self.stores(model)[db.NAMES[2]]['exact_byte_match'])

    def test_valid_partial_archive_does_not_claim_complete(self):
        self.archive({db.NAMES[0]: self.samples[db.NAMES[0]]})
        model = self.assess().model
        self.assertEqual(model['coverage'], 'partial')
        self.assertFalse(model['healthy'])
        self.assertTrue(model['archive']['checksum_valid'])
        for name, row in self.stores(model).items():
            self.assertEqual(row['archived'], name == db.NAMES[0])
            self.assertIs(row['exact_byte_match'], True if name == db.NAMES[0] else None)

    def test_missing_current_store_is_unknown_not_empty_match(self):
        self.archive()
        self.mapping[db.NAMES[1]].unlink()
        model = self.assess().model
        row = self.stores(model)[db.NAMES[1]]
        self.assertEqual(row['current_presence'], 'Missing')
        self.assertIsNone(row['exact_byte_match'])
        self.assertEqual(model['coverage'], 'unknown')
        self.assertFalse(model['healthy'])

    def test_empty_current_store_is_present_and_different(self):
        self.archive()
        self.mapping[db.NAMES[1]].write_bytes(b'')
        row = self.stores(self.assess().model)[db.NAMES[1]]
        self.assertEqual(row['current_presence'], 'Present')
        self.assertEqual(row['current_readability'], 'Readable')
        self.assertIs(row['exact_byte_match'], False)

    def test_corrupt_and_truncated_zip(self):
        for raw in (b'not a zip', b'PK\x03\x04', b''):
            with self.subTest(raw=raw):
                self.archive_path.write_bytes(raw)
                model = self.assess().model
                self.assertIs(model['archive']['zip_valid'], False)
                self.assertEqual(model['coverage'], 'invalid')
                self.assertFalse(model['healthy'])
        self.archive()
        self.archive_path.write_bytes(self.archive_path.read_bytes()[:-30])
        self.assertFalse(self.assess().model['archive']['zip_valid'])

    def test_invalid_json_manifest_and_duplicate_json_keys(self):
        for raw in (b'invalid', b'\xff', b'{"x":1,"x":2}', b'NaN'):
            with self.subTest(raw=raw):
                self.archive(manifest_raw=raw)
                model = self.assess().model
                self.assertTrue(model['archive']['zip_valid'])
                self.assertFalse(model['archive']['manifest_valid'])
                self.assertEqual(model['archive']['manifest_reason'], 'invalid_manifest_json')

    def test_unsupported_schema_application_release_separate_from_zip(self):
        for key, value in (('manifest_version', 2), ('manifest_version', True),
                           ('application', 'other-application'), ('release', '2.0.0')):
            with self.subTest(key=key, value=value):
                manifest = self.manifest(self.samples)
                manifest[key] = value
                self.archive(manifest=manifest)
                model = self.assess().model
                self.assertTrue(model['archive']['zip_valid'])
                self.assertFalse(model['archive']['compatible'])
                self.assertEqual(model['coverage'], 'invalid')
                self.assertFalse(model['healthy'])

    def test_manifest_membership_structure_and_entry_validation(self):
        variants = []
        for field, value in (('files', []), ('files', {}), ('files', [None])):
            manifest = self.manifest(self.samples); manifest[field] = value; variants.append(manifest)
        for field, value in (('filename', []), ('filename', '../escape'), ('size', True),
                             ('size', -1), ('sha256', 'invalid')):
            manifest = self.manifest(self.samples); manifest['files'][0][field] = value; variants.append(manifest)
        manifest = self.manifest(self.samples); manifest['files'].pop(); variants.append(manifest)
        manifest = self.manifest(self.samples); manifest['files'][1] = manifest['files'][0]; variants.append(manifest)
        manifest = self.manifest(self.samples); manifest['extra'] = 1; variants.append(manifest)
        for manifest in variants + [[], None, 'bad']:
            with self.subTest(manifest=manifest):
                self.archive(manifest_raw=json.dumps(manifest).encode())
                model = self.assess().model
                self.assertFalse(model['archive']['manifest_valid'])
                self.assertEqual(model['coverage'], 'invalid')

    def test_missing_manifest(self):
        self.archive(include_manifest=False)
        model = self.assess().model
        self.assertTrue(model['archive']['zip_valid'])
        self.assertEqual(model['archive']['manifest_reason'], 'missing_manifest')
        self.assertEqual(model['coverage'], 'invalid')

    def test_duplicate_manifest_and_store_members(self):
        for name in ('manifest.json', db.NAMES[0]):
            with self.subTest(name=name):
                self.archive({db.NAMES[0]: self.samples[db.NAMES[0]]}, extras=[(name, b'{}')])
                model = self.assess().model
                self.assertFalse(model['archive']['members_safe'])
                self.assertEqual(model['coverage'], 'invalid')

    def test_unexpected_traversal_absolute_directory_and_nul_members(self):
        for name in ('unexpected.json', '../escape', '/absolute', 'nested/file', 'nested\\file', 'directory/'):
            with self.subTest(name=name):
                self.archive({db.NAMES[0]: self.samples[db.NAMES[0]]}, extras=[(name, b'{}')])
                model = self.assess().model
                self.assertFalse(model['archive']['members_safe'])
                self.assertFalse(model['healthy'])
        # A NUL is preserved in the archive metadata, not accepted as a truncated allowed name.
        self.archive(compression=zipfile.ZIP_STORED)
        self.archive_path.write_bytes(self.archive_path.read_bytes().replace(
            b'vehicle_profiles.json', b'vehicle_profiles\x00json'))
        self.assertEqual(self.assess().model['coverage'], 'invalid')

    def test_symlink_like_archive_member(self):
        info = zipfile.ZipInfo(db.NAMES[2])
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.archive({db.NAMES[0]: self.samples[db.NAMES[0]]}, extras=[(info, b'target')])
        self.assertFalse(self.assess().model['archive']['members_safe'])

    def test_member_archive_manifest_and_total_size_bounds(self):
        self.archive()
        for constant in ('MAX_FILE', 'MAX_ARCHIVE', 'MAX_MANIFEST', 'MAX_TOTAL'):
            with self.subTest(constant=constant), patch.object(bh, constant, 1):
                model = self.assess().model
                self.assertEqual(model['coverage'], 'invalid')
                self.assertFalse(model['healthy'])

    def test_unsafe_compression_methods_flags_and_sizes(self):
        self.archive(compression=zipfile.ZIP_BZIP2)
        self.assertEqual(self.assess().model['archive']['zip_reason'], 'unsafe_compression_metadata')
        self.archive()
        original = self.archive_path.read_bytes()
        central = original.index(b'PK\x01\x02')
        for offset, value, fmt in ((8, 1, '<H'), (8, 0x20, '<H'), (20, 0, '<I'), (6, 63, '<H')):
            with self.subTest(offset=offset, value=value):
                raw = bytearray(original)
                struct.pack_into(fmt, raw, central + offset, value)
                self.archive_path.write_bytes(raw)
                self.assertEqual(self.assess().model['coverage'], 'invalid')

    def test_deflate_cannot_hide_more_bytes_than_declared_length(self):
        self.archive()
        raw = bytearray(self.archive_path.read_bytes())
        central = raw.index(b'PK\x01\x02')
        # Forge both headers to claim a one-byte member with a matching prefix
        # CRC. ZipExtFile can truncate to declared size; assess the whole stream
        # with an explicit bounded decoder instead.
        struct.pack_into('<I', raw, 14, bh.zlib.crc32(b'{'))
        struct.pack_into('<I', raw, 22, 1)
        struct.pack_into('<I', raw, central + 16, bh.zlib.crc32(b'{'))
        struct.pack_into('<I', raw, central + 24, 1)
        self.archive_path.write_bytes(raw)
        self.assertFalse(self.assess().model['archive']['zip_valid'])

    def test_high_compression_ratio_with_bounded_valid_bytes_is_supported(self):
        # Existing writer/format allows highly compressible, bounded JSON.
        payloads = dict(self.samples)
        payloads[db.NAMES[2]] = self.samples[db.NAMES[2]] + b' ' * (2 * 1024 * 1024)
        self.mapping[db.NAMES[2]].write_bytes(payloads[db.NAMES[2]])
        self.archive(payloads)
        self.assertEqual(self.assess().model['coverage'], 'complete')

    def test_excess_directory_entries_rejected_before_zipinfo_allocation(self):
        self.archive()
        raw = bytearray(self.archive_path.read_bytes())
        end = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', raw, end + 8, 60000, 60000)
        self.archive_path.write_bytes(raw)
        with patch.object(bh.zipfile, 'ZipFile', side_effect=AssertionError('Must preflight first')):
            model = self.assess().model
        self.assertEqual(model['coverage'], 'invalid')

    def test_underreported_central_directory_count_is_rejected_before_allocation(self):
        self.archive()
        raw = bytearray(self.archive_path.read_bytes())
        end = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', raw, end + 8, 1, 1)
        self.archive_path.write_bytes(raw)
        with patch.object(bh.zipfile, 'ZipFile', side_effect=AssertionError('Must preflight first')):
            model = self.assess().model
        self.assertFalse(model['archive']['zip_valid'])

    def test_inconsistent_local_and_overlapping_member_headers(self):
        self.archive(compression=zipfile.ZIP_STORED)
        original = self.archive_path.read_bytes()
        raw = bytearray(original)
        struct.pack_into('<H', raw, 6, 8)
        self.archive_path.write_bytes(raw)
        self.assertEqual(self.assess().model['coverage'], 'invalid')
        raw = bytearray(original)
        first = raw.index(b'PK\x01\x02')
        second = raw.index(b'PK\x01\x02', first + 4)
        struct.pack_into('<I', raw, second + 42, 0)
        self.archive_path.write_bytes(raw)
        self.assertEqual(self.assess().model['coverage'], 'invalid')

    def test_manifest_checksum_and_size_mismatch_per_store(self):
        for field, value in (('sha256', '0' * 64), ('size', 999)):
            with self.subTest(field=field):
                manifest = self.manifest(self.samples)
                bad_name = manifest['files'][0]['filename']
                manifest['files'][0][field] = value
                self.archive(manifest=manifest)
                model = self.assess().model
                self.assertTrue(model['archive']['zip_valid'])
                self.assertTrue(model['archive']['manifest_valid'])
                self.assertFalse(model['archive']['checksum_valid'])
                self.assertEqual(model['coverage'], 'invalid')
                self.assertEqual(model['coverage_reason'], 'checksum_mismatch')
                self.assertFalse(self.stores(model)[bad_name]['archive_checksum_valid'])
                self.assertTrue(all(row['archive_checksum_valid'] for name, row in self.stores(model).items()
                                    if name != bad_name))
                self.assertTrue(all(row['exact_byte_match'] is None for row in model['stores']))

    def test_crc_corruption_is_zip_failure(self):
        self.archive(compression=zipfile.ZIP_STORED)
        raw = bytearray(self.archive_path.read_bytes())
        raw[raw.index(self.samples[db.NAMES[0]])] ^= 1
        self.archive_path.write_bytes(raw)
        self.assertFalse(self.assess().model['archive']['zip_valid'])

    def test_invalid_archived_store_bytes_do_not_become_valid_through_matching(self):
        payloads = dict(self.samples, **{db.NAMES[2]: b'not json'})
        self.mapping[db.NAMES[2]].write_bytes(b'not json')
        self.archive(payloads)
        model = self.assess().model
        self.assertTrue(model['archive']['checksum_valid'])
        self.assertFalse(model['archive']['payload_valid'])
        self.assertEqual(model['coverage'], 'invalid')
        self.assertFalse(model['healthy'])

    def test_malformed_naive_and_non_utc_recorded_timestamps(self):
        for stamp in (None, 123, 'bad', '2026-10-22T12:00:00', '2026-10-22T12:00:00+01:00'):
            with self.subTest(stamp=stamp):
                manifest = self.manifest(self.samples); manifest['created_at'] = stamp
                self.archive(manifest=manifest)
                model = self.assess().model
                self.assertFalse(model['archive']['timestamp_valid'])
                self.assertEqual(model['archive']['manifest_reason'], 'invalid_recorded_timestamp')
                self.assertTrue(model['archive']['checksum_valid'])
                self.assertFalse(model['healthy'])

    def test_utc_z_timestamp_consistent_on_both_python_versions(self):
        manifest = self.manifest(self.samples); manifest['created_at'] = '2026-10-22T12:00:00Z'
        self.archive(manifest=manifest)
        model = self.assess().model
        self.assertEqual(model['coverage'], 'complete')
        self.assertEqual(model['archive']['created_at'], manifest['created_at'])

    def test_legacy_backup_format_release_compatibility(self):
        for release in bh.BACKUP_RELEASES:
            with self.subTest(release=release):
                payloads = {n: p for n, p in self.samples.items() if release != '1.4.0' or n != db.NAMES[3]}
                manifest = self.manifest(payloads); manifest['release'] = release
                self.archive(payloads, manifest=manifest)
                self.assertTrue(self.assess().model['archive']['compatible'])
                self.assertEqual(dict(db.validate_backup(self.archive_path).files), payloads)
        manifest = self.manifest(self.samples); manifest['release'] = '1.4.0'
        self.archive(manifest=manifest)
        self.assertEqual(self.assess().model['archive']['compatibility_reason'], 'release_payload_incompatible')
        payloads = dict(self.samples, **{db.NAMES[3]: b'{"schema_version": 2, "tasks": []}'})
        manifest = self.manifest(payloads); manifest['release'] = '1.5.0'
        self.archive(payloads, manifest=manifest)
        self.assertEqual(self.assess().model['archive']['compatibility_reason'], 'release_payload_incompatible')

    def test_shared_v190_writer_format_is_accepted_without_modification(self):
        from datetime import datetime, timezone
        path = db.create_backup(self.paths, self.root, timestamp=datetime(2026, 10, 22, tzinfo=timezone.utc))
        before = self.tree()
        self.assertEqual(self.assess(path).model['coverage'], 'complete')
        self.assertEqual(self.tree(), before)

    def test_archive_replacement_even_with_identical_bytes_and_mtime(self):
        self.archive()
        assessment = self.assess()
        info = self.archive_path.stat()
        replacement = self.root / 'replacement.zip'
        replacement.write_bytes(self.archive_path.read_bytes())
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        replacement.replace(self.archive_path)
        with self.assertRaises(bh.BackupHealthChangedError): assessment.revalidate()
        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_archive_mutation_even_with_restored_mtime(self):
        self.archive()
        assessment = self.assess()
        info = self.archive_path.stat()
        self.archive_path.write_bytes(self.archive_path.read_bytes() + b'changed')
        os.utime(self.archive_path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_current_store_replacement_and_same_size_mutation(self):
        self.archive()
        path = self.mapping[db.NAMES[2]]
        for replace in (False, True):
            with self.subTest(replace=replace):
                path.write_bytes(self.samples[db.NAMES[2]])
                assessment = self.assess()
                info = path.stat()
                if replace:
                    new = self.root / 'replacement.json'
                    new.write_bytes(path.read_bytes())
                    os.utime(new, ns=(info.st_atime_ns, info.st_mtime_ns))
                    new.replace(path)
                else:
                    path.write_bytes(path.read_bytes().replace(b'1', b'2'))
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                with self.assertRaises(bh.BackupHealthChangedError): assessment.revalidate()
                with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_missing_input_appearance_and_deletion_invalidate_evidence(self):
        assessment = self.assess()
        self.archive()
        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model
        assessment = self.assess()
        self.mapping[db.NAMES[1]].unlink()
        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_missing_parent_appearance_and_parent_replacement_invalidate_evidence(self):
        parent = self.root / 'new-parent'
        paths = db.RuntimePaths(*(parent / name for name in db.NAMES))
        assessment = bh.assess_backup_health(None, paths)
        parent.mkdir()
        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model
        for name, raw in self.samples.items(): (parent / name).write_bytes(raw)
        assessment = bh.assess_backup_health(None, paths)
        old = self.root / 'old-parent'; parent.rename(old); parent.mkdir()
        # Move the very same file inodes back under a different directory inode.
        for name in db.NAMES: (old / name).rename(parent / name)
        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_replacement_during_capture_is_detected(self):
        self.archive()
        original = bh._read
        changed = False
        def read(path, limit):
            nonlocal changed
            result = original(path, limit)
            if path == self.archive_path and not changed:
                changed = True
                path.write_bytes(path.read_bytes() + b'changed')
            return result
        with patch.object(bh, '_read', side_effect=read), self.assertRaises(bh.BackupHealthChangedError):
            self.assess()

    def test_replacement_while_descriptor_is_open_is_detected(self):
        self.archive()
        original = os.open
        replacement = self.root / 'replacement.zip'
        replacement.write_bytes(self.archive_path.read_bytes())
        def opened(path, flags, *args, **kwargs):
            descriptor = original(path, flags, *args, **kwargs)
            if path == self.archive_path:
                replacement.replace(path)
            return descriptor
        with patch.object(bh.os, 'open', side_effect=opened), self.assertRaises(bh.BackupHealthChangedError):
            self.assess()

    def test_archive_current_store_and_current_current_aliases(self):
        self.archive()
        for alias in ('same_path', 'hardlink', 'parent_alias'):
            with self.subTest(alias=alias):
                target = self.archive_path
                if alias == 'hardlink':
                    target = self.root / 'alias.zip'; os.link(self.archive_path, target)
                elif alias == 'parent_alias':
                    parent = self.root / 'linked'; parent.symlink_to(self.root, target_is_directory=True)
                    target = parent / self.archive_path.name
                paths = db.RuntimePaths(target, self.paths.history, self.paths.profiles, self.paths.maintenance)
                with self.assertRaises(bh.BackupHealthInputError): bh.assess_backup_health(self.archive_path, paths)
                if alias == 'hardlink': target.unlink()
        paths = db.RuntimePaths(self.paths.checklist, self.paths.checklist, self.paths.profiles, self.paths.maintenance)
        with self.assertRaises(bh.BackupHealthInputError): bh.assess_backup_health(self.archive_path, paths)

    def test_symlink_hardlink_directory_and_fifo_inputs_are_not_read(self):
        self.archive()
        path = self.mapping[db.NAMES[2]]
        for kind in ('symlink', 'hardlink', 'directory', 'fifo'):
            with self.subTest(kind=kind):
                path.unlink()
                external = self.root / 'external'
                external.write_bytes(self.samples[db.NAMES[2]])
                if kind == 'symlink': path.symlink_to(external)
                elif kind == 'hardlink': os.link(external, path)
                elif kind == 'directory': path.mkdir()
                else: os.mkfifo(path)
                row = self.stores(self.assess().model)[db.NAMES[2]]
                self.assertEqual(row['current_readability'], 'Unsafe')
                self.assertIsNone(row['exact_byte_match'])
                if kind == 'directory': path.rmdir()
                else: path.unlink()
                path.write_bytes(self.samples[db.NAMES[2]])
                external.unlink()

    def test_symlink_archive_is_invalid_not_followed(self):
        self.archive()
        real = self.root / 'real.zip'; self.archive_path.rename(real)
        self.archive_path.symlink_to(real)
        model = self.assess().model
        self.assertEqual(model['archive']['readability'], 'Unsafe')
        self.assertEqual(model['coverage'], 'invalid')

    def test_unreadable_archive_and_store_are_unknown_evidence(self):
        self.archive()
        original = os.open
        for target in (self.archive_path, self.paths.profiles):
            with self.subTest(target=target):
                def denied(path, flags, *args, **kwargs):
                    if path == target: raise PermissionError(errno.EACCES, 'synthetic permission failure')
                    return original(path, flags, *args, **kwargs)
                with patch.object(bh.os, 'open', side_effect=denied):
                    assessment = self.assess()
                    model = assessment.model
                    self.assertEqual(model['coverage'], 'unknown')
                    self.assertFalse(model['healthy'])
                with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_oversized_current_store_is_unknown_not_a_match(self):
        self.archive()
        self.mapping[db.NAMES[2]].write_bytes(b'X' * 10000)
        with patch.object(bh, 'MAX_FILE', 9999):
            model = self.assess().model
        self.assertEqual(model['coverage'], 'unknown')
        self.assertEqual(self.stores(model)[db.NAMES[2]]['current_readability'], 'Oversized')
        self.assertIsNone(self.stores(model)[db.NAMES[2]]['exact_byte_match'])

    def test_explicit_paths_custom_names_and_no_runtimepaths_fallback(self):
        self.archive()
        args = dict(checklist_path=self.paths.checklist, history_path=self.paths.history,
                    profiles_path=self.paths.profiles, maintenance_path=self.paths.maintenance)
        self.assertEqual(bh.assess_backup_health(self.archive_path, **args).model['coverage'], 'complete')
        with self.assertRaises(bh.BackupHealthInputError): bh.assess_backup_health(self.archive_path)
        with self.assertRaises(bh.BackupHealthInputError): bh.assess_backup_health(self.archive_path, self.paths, **args)
        incomplete = db.RuntimePaths(self.paths.checklist, self.paths.history, self.paths.profiles)
        with self.assertRaises(bh.BackupHealthInputError): bh.assess_backup_health(self.archive_path, incomplete)
        custom = self.root / 'custom-store'
        self.paths.maintenance.rename(custom)
        args['maintenance_path'] = custom
        self.assertEqual(bh.assess_backup_health(self.archive_path, **args).model['coverage'], 'complete')

    def test_returned_data_independently_owned_and_snapshot_immutable(self):
        self.archive()
        assessment = self.assess()
        first = assessment.model
        before = deepcopy(first)
        first['archive']['included_stores'].clear()
        first['stores'][0]['exact_byte_match'] = False
        self.assertEqual(assessment.model, before)
        with self.assertRaises(FrozenInstanceError): assessment.fingerprints = ()

    def test_deterministic_order_independent_of_zip_manifest_order(self):
        self.archive()
        first = self.assess().model
        manifest = self.manifest(self.samples); manifest['files'].reverse()
        self.archive(dict(reversed(list(self.samples.items()))), manifest=manifest)
        second = self.assess().model
        self.assertEqual(first, second)
        self.assertEqual([row['name'] for row in first['stores']], sorted(db.NAMES))

    def test_assessment_and_revalidation_preserve_bytes_mtimes_and_membership(self):
        self.archive()
        for index, path in enumerate((self.archive_path, *self.mapping.values())):
            path.chmod(0o600 if index % 2 else 0o640)
        before = self.tree()
        original_open = os.open
        def readonly_open(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return original_open(path, flags, *args, **kwargs)
        with patch.object(db, 'restore_backup', side_effect=AssertionError('restore forbidden')), \
             patch.object(db, 'create_backup', side_effect=AssertionError('create forbidden')), \
             patch.object(bh.os, 'open', side_effect=readonly_open), \
             patch.object(bh.os, 'chmod', side_effect=AssertionError('chmod forbidden')), \
             patch.object(Path, 'glob', side_effect=AssertionError('search forbidden')), \
             patch.object(Path, 'rglob', side_effect=AssertionError('search forbidden')), \
             patch.object(Path, 'iterdir', side_effect=AssertionError('search forbidden')), \
             patch.object(zipfile.ZipFile, 'extract', side_effect=AssertionError('extract forbidden')), \
             patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('extract forbidden')), \
             patch.object(tempfile, 'TemporaryDirectory', side_effect=AssertionError('temp extraction forbidden')):
            assessment = self.assess()
            _ = assessment.model
            assessment.revalidate()
        self.assertEqual(self.tree(), before)

    def test_missing_inputs_do_not_create_files_or_parents(self):
        absent = self.root / 'absent' / 'nested'
        paths = db.RuntimePaths(*(absent / name for name in db.NAMES))
        before = self.tree()
        assessment = bh.assess_backup_health(absent / 'backup.zip', paths)
        self.assertEqual(assessment.model['coverage'], 'unknown')
        assessment.revalidate()
        self.assertEqual(self.tree(), before)
        self.assertFalse(absent.parent.exists())

    def test_descriptor_mode_is_rejected_in_every_header_and_layout(self):
        for variant in ('central_only', 'local_only', 'both', 'missing', 'forged',
                        'overlap_member', 'overlap_directory', 'previous_poc'):
            with self.subTest(variant=variant):
                self.archive()
                raw = bytearray(self.archive_path.read_bytes())
                with zipfile.ZipFile(self.archive_path) as archive:
                    infos = archive.infolist()
                central = raw.index(b'PK\x01\x02')
                for info in infos:
                    start = info.header_offset
                    if variant != 'central_only':
                        struct.pack_into('<H', raw, start + 6, info.flag_bits | 8)
                    if variant != 'local_only':
                        struct.pack_into('<H', raw, central + 8, info.flag_bits | 8)
                    if variant == 'previous_poc':
                        struct.pack_into('<III', raw, start + 14, 0x12345678, 0xffffffff, 0xffffffff)
                    a, b, c = struct.unpack_from('<HHH', raw, central + 28)
                    central += 46 + a + b + c
                descriptor = struct.pack('<IIII', 0x08074b50, 0x12345678, 1, 1)
                if variant == 'forged':
                    # Insert an actual forged descriptor before the directory.
                    end = raw.rfind(b'PK\x05\x06')
                    offset = struct.unpack_from('<I', raw, end + 16)[0]
                    raw[offset:offset] = descriptor
                    struct.pack_into('<I', raw, end + len(descriptor) + 16, offset + len(descriptor))
                elif variant in ('overlap_member', 'overlap_directory'):
                    # The advertised descriptor would consume the next header.
                    offset = (infos[1].header_offset if variant == 'overlap_member'
                              else raw.index(b'PK\x01\x02'))
                    raw[offset:offset + len(descriptor)] = descriptor
                self.archive_path.write_bytes(raw)
                model = self.assess().model
                self.assertEqual(model['coverage'], 'invalid')
                self.assertFalse(model['healthy'])
                self.assertIsNot(model['archive']['members_safe'], True)

    def test_local_crc_and_both_lengths_must_match_central_header(self):
        self.archive()
        original = self.archive_path.read_bytes()
        for offset in (14, 18, 22):
            with self.subTest(offset=offset):
                raw = bytearray(original)
                struct.pack_into('<I', raw, offset, struct.unpack_from('<I', raw, offset)[0] ^ 1)
                self.archive_path.write_bytes(raw)
                model = self.assess().model
                self.assertEqual(model['coverage'], 'invalid')
                self.assertFalse(model['healthy'])
                self.assertFalse(model['archive']['members_safe'])

    def test_timestamp_grammar_matrix(self):
        accepted = ('2026-10-22T12:34:56+00:00', '2026-10-22T12:34:56Z',
                    '2026-10-22T12:34:56.1Z', '2026-10-22T12:34:56.123456789+00:00')
        rejected = ('20261022T123456+00:00', '2026-W43-4T12:34:56+00:00',
                    '2026-295T12:34:56Z', '2026-10-22 12:34:56Z',
                    '2026-10-22t12:34:56Z', '2026-10-22T12:34:56z',
                    '2026-10-22T12:34:56', '2026-10-22T12:34:56+01:00',
                    '2026-10-22T12:34:56-00:00', '2026-10-22T12:34:56+0000',
                    '2026-10-22T12:34:56.Z', '2026-10-22T12:34:56,1Z',
                    '2026-02-30T12:34:56Z', '2026-10-22T24:00:00Z',
                    '2026-10-22T12:34:60Z', '2026-10-22T12:34Z',
                    '2026-10-22T12:34:56Z\n')
        for stamp in accepted + rejected:
            with self.subTest(stamp=stamp):
                manifest = self.manifest(self.samples)
                manifest['created_at'] = stamp
                self.archive(manifest=manifest)
                model = self.assess().model
                self.assertEqual(model['archive']['created_at'], stamp)
                self.assertEqual(model['archive']['timestamp_valid'], stamp in accepted)
                self.assertEqual(model['coverage'], 'complete' if stamp in accepted else 'invalid')

    def test_permission_failure_stages_and_freshness_transitions(self):
        self.archive()
        for target in (self.archive_path, self.paths.profiles):
            for stage in ('lstat', 'stat', 'fstat', 'open', 'read'):
                for code in (errno.EACCES, errno.EPERM):
                    with self.subTest(target=target, stage=stage, code=code):
                        readable = self.assess()
                        failure = PermissionError(code, 'synthetic permission failure')
                        original_lstat, original_stat = Path.lstat, Path.stat
                        original_open, original_fdopen, original_fstat = os.open, os.fdopen, os.fstat
                        descriptors = set()
                        def opened(path, *args, **kwargs):
                            if path == target and stage == 'open': raise failure
                            fd = original_open(path, *args, **kwargs)
                            if path == target: descriptors.add(fd)
                            return fd
                        def metadata(path, *args, **kwargs):
                            if path == target: raise failure
                            original = original_lstat if stage == 'lstat' else original_stat
                            return original(path, *args, **kwargs)
                        def fstat(fd):
                            if fd in descriptors: raise failure
                            return original_fstat(fd)
                        class Stream:
                            def __init__(self, stream): self.stream = stream
                            def __enter__(self): return self
                            def __exit__(self, *args): self.stream.close()
                            def fileno(self): return self.stream.fileno()
                            def read(self, limit): raise failure
                        def fdopen(fd, *args):
                            stream = original_fdopen(fd, *args)
                            return Stream(stream) if fd in descriptors else stream
                        from contextlib import ExitStack
                        with ExitStack() as stack:
                            if stage in ('lstat', 'stat'):
                                stack.enter_context(patch.object(Path, stage, metadata))
                                if stage == 'stat':
                                    # 3.10 implements lstat via stat; 3.15 calls
                                    # os.lstat directly. Exercise that metadata
                                    # adapter explicitly on both interpreters.
                                    stack.enter_context(patch.object(Path, 'lstat',
                                        lambda path: path.stat(follow_symlinks=False)))
                            else:
                                stack.enter_context(patch.object(bh.os, 'open', opened))
                                if stage == 'fstat': stack.enter_context(patch.object(bh.os, 'fstat', fstat))
                                if stage == 'read': stack.enter_context(patch.object(bh.os, 'fdopen', fdopen))
                            with self.assertRaises(bh.BackupHealthChangedError): readable.revalidate()
                            assessment = self.assess()
                            model = assessment.model
                            self.assertEqual(model['coverage'], 'unknown')
                            self.assertFalse(model['healthy'])
                            evidence = (model['archive'] if target == self.archive_path
                                        else self.stores(model)[db.NAMES[2]])
                            key = 'readability' if target == self.archive_path else 'current_readability'
                            self.assertEqual(evidence[key], 'Unreadable')
                            assessment.revalidate()
                        with self.assertRaises(bh.BackupHealthChangedError): assessment.revalidate()
                        with self.assertRaises(bh.BackupHealthChangedError): _ = assessment.model

    def test_descriptor_ownership_on_constructor_read_and_success(self):
        self.archive()
        for mode in ('oserror', 'interrupt', 'already_closed', 'read_error', 'success'):
            with self.subTest(mode=mode):
                original_open, original_fdopen, original_close = os.open, os.fdopen, os.close
                descriptors, closed = [], []
                failure = KeyboardInterrupt('probe') if mode == 'interrupt' else OSError(errno.EIO, 'probe')
                def opened(*args, **kwargs):
                    fd = original_open(*args, **kwargs); descriptors.append(fd); return fd
                def close(fd):
                    closed.append(fd); return original_close(fd)
                class Stream:
                    def __init__(self, stream): self.stream = stream
                    def __enter__(self): return self
                    def __exit__(self, *args): self.stream.close()
                    def fileno(self): return self.stream.fileno()
                    def read(self, limit): raise failure
                def fdopen(fd, *args):
                    if mode == 'already_closed':
                        original_close(fd)
                        raise OSError(errno.EBADF, 'constructor already closed descriptor')
                    if mode in ('oserror', 'interrupt'): raise failure
                    stream = original_fdopen(fd, *args)
                    return Stream(stream) if mode == 'read_error' else stream
                with patch.object(bh.os, 'open', opened), patch.object(bh.os, 'fdopen', fdopen), \
                     patch.object(bh.os, 'close', close):
                    if mode == 'interrupt':
                        with self.assertRaises(KeyboardInterrupt) as caught: bh._read(self.archive_path, bh.MAX_ARCHIVE)
                        self.assertIs(caught.exception, failure)
                    else:
                        token, raw = bh._read(self.archive_path, bh.MAX_ARCHIVE)
                        self.assertEqual(token[1], 'Readable' if mode == 'success' else 'Unreadable')
                        if mode == 'already_closed': self.assertEqual(token[2], 'os_error_' + str(errno.EBADF))
                    self.assertEqual(closed, descriptors if mode in ('oserror', 'interrupt', 'already_closed') else [])
                for fd in descriptors:
                    with self.assertRaises(OSError) as caught: os.fstat(fd)
                    self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_import_has_no_io_or_startup(self):
        with patch('builtins.open', side_effect=AssertionError('No import IO')), \
             patch.object(os, 'open', side_effect=AssertionError('No import IO')):
            importlib.reload(bh)


if __name__ == '__main__':
    unittest.main()
