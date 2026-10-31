"""Dashboard capture tests use disposable fixtures only, never live stores."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, datetime
from itertools import combinations
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dashboard_sources as ds
import maintenance_insights_sources as shared
from checklist import create_default_data
from data_backup import RuntimePaths
from test_maintenance_insights import AS_OF, positive_task, schedule, history, inspection, profile
from test_fleet_dashboard import PID


class TestDashboardSources(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = RuntimePaths(*(self.root / name for name in
            ('checklist.json', 'history.json', 'profiles.json', 'maintenance.json')))
        self.files = tuple(self.paths.mapping().values())
        self.values = (create_default_data(), history([inspection()]),
                       {'schema_version': 1, 'profiles': [profile()]}, schedule([positive_task()]))
        for path, value in zip(self.files, self.values):
            path.write_text(json.dumps(value), encoding='utf-8')

    def capture(self, **options):
        return ds.capture_dashboard_sources(self.paths, as_of=AS_OF, **options)

    def test_all_stores_valid_and_explicit_paths_equivalent(self):
        model = self.capture().model
        self.assertTrue(all(s['availability'] == 'Available' for s in model['stores'].values()))
        self.assertEqual(model['insights']['availability'], 'Available')
        self.assertEqual([s['source_kind'] for s in model['insights']['data']['sources']],
                         ['maintenance', 'profiles', 'inspections'])
        self.assertEqual(model['presentation']['shared_inspection_progress']['total'], 7)
        for kind, value in zip(ds.KINDS, self.values):
            self.assertEqual(model['stores'][kind]['data'], value)
        explicit = ds.capture_dashboard_sources(
            checklist_path=self.files[0], history_path=self.files[1], profiles_path=self.files[2],
            maintenance_path=self.files[3], as_of=AS_OF).model
        self.assertEqual(explicit, model)

    def test_missing_checklist_does_not_block_insights(self):
        self.files[0].unlink()
        model = self.capture().model
        self.assertEqual(model['stores']['checklist']['availability'], 'Missing')
        self.assertEqual(model['insights']['availability'], 'Available')
        self.assertEqual(model['presentation']['shared_inspection_progress']['availability'], 'Unavailable')
        self.assertFalse(self.files[0].exists())

    def test_each_missing_analytical_store_blocks_insights_not_checklist(self):
        for index in (1, 2, 3):
            path = self.files[index]
            raw = path.read_bytes()
            path.unlink()
            model = self.capture().model
            self.assertEqual(model['stores'][ds.KINDS[index]]['availability'], 'Missing')
            self.assertEqual(model['stores']['checklist']['availability'], 'Available')
            self.assertEqual(model['insights']['blocked_sources'], [ds.KINDS[index]])
            self.assertIsNone(model['insights']['data'])
            self.assertIsNone(model['presentation'])
            self.assertFalse(path.exists())
            path.write_bytes(raw)

    def test_all_missing_creates_no_files(self):
        for path in self.files:
            path.unlink()
        snapshot = self.capture()
        snapshot.revalidate()
        self.assertTrue(all(s['availability'] == 'Missing' for s in snapshot.model['stores'].values()))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_json_is_invalid_not_missing(self):
        for raw in (b'{', b'\xff', b'{"schema_version":1,"schema_version":1}',
                    b'{"schema_version":NaN}', b'[' * 140 + b'0' + b']' * 140):
            self.files[3].write_bytes(raw)
            state = self.capture().model
            self.assertEqual(state['stores']['maintenance']['availability'], 'Invalid')
            self.assertTrue(state['stores']['maintenance']['error'])
            self.assertIsNone(state['insights']['data'])

    def test_unsupported_schema_and_invalid_checklist_are_independent(self):
        self.files[1].write_text(json.dumps({'schema_version': 99, 'inspections': []}))
        model = self.capture().model
        self.assertEqual(model['stores']['history']['availability'], 'Invalid')
        self.assertEqual(model['stores']['checklist']['availability'], 'Available')
        self.files[1].write_text(json.dumps(self.values[1]))
        self.files[0].write_text('{}')
        model = self.capture().model
        self.assertEqual(model['stores']['checklist']['availability'], 'Invalid')
        self.assertEqual(model['insights']['availability'], 'Available')
        self.assertEqual(model['presentation']['shared_inspection_progress']['availability'], 'Unavailable')

    def test_oversized_sources_rejected_using_shared_limit(self):
        limit = max(path.stat().st_size for path in self.files) + 10
        for index in range(4):
            with self.subTest(index=index):
                raw = self.files[index].read_bytes()
                self.files[index].write_bytes(b' ' * (limit + 1))
                with patch.object(shared, 'MAX_FILE', limit), self.assertRaisesRegex(ValueError, 'exceeds'):
                    self.capture()
                self.files[index].write_bytes(raw)

    def test_aliases_including_missing_paths_are_rejected(self):
        for present in (True, False):
            if not present:
                self.files[0].unlink()
            paths = RuntimePaths(self.files[0], self.files[0], self.files[2], self.files[3])
            with self.assertRaises(ValueError):
                ds.capture_dashboard_sources(paths, as_of=AS_OF)

    def test_symlinks_hardlinks_and_directories_rejected(self):
        target = self.root / 'target'
        target.write_bytes(self.files[0].read_bytes())
        self.files[0].unlink()
        self.files[0].symlink_to(target)
        with self.assertRaises(ValueError):
            self.capture()
        self.files[0].unlink()
        os.link(target, self.files[0])
        with self.assertRaises(ValueError):
            self.capture()
        self.files[0].unlink()
        self.files[0].mkdir()
        with self.assertRaises(ValueError):
            self.capture()

    def test_changes_after_capture_invalidate_every_source(self):
        for path in self.files:
            snapshot = self.capture()
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaises(ValueError):
                snapshot.revalidate()
            with self.assertRaises(ValueError):
                _ = snapshot.model

    def test_same_bytes_replacement_is_stale(self):
        snapshot = self.capture()
        path = self.files[0]
        replacement = self.root / 'replacement'
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        with self.assertRaises(ValueError):
            snapshot.revalidate()

    def test_deleted_or_newly_appearing_source_is_stale(self):
        snapshot = self.capture()
        raw = self.files[2].read_bytes()
        self.files[2].unlink()
        with self.assertRaises(ValueError):
            snapshot.revalidate()
        missing = self.capture()
        self.files[2].write_bytes(raw)
        with self.assertRaises(ValueError):
            missing.revalidate()

    def test_source_change_during_capture_is_rejected(self):
        original = ds.read_source
        def changing_read(path):
            token = original(path)
            if path == self.files[3]:
                self.files[0].write_bytes(self.files[0].read_bytes() + b'\n')
            return token
        with patch.object(ds, 'read_source', side_effect=changing_read), self.assertRaises(ValueError):
            self.capture()

    def test_data_is_independent_and_snapshot_is_frozen(self):
        snapshot = self.capture()
        first = snapshot.model
        expected = deepcopy(first)
        first['stores']['maintenance']['data']['tasks'][0]['title'] = 'changed'
        first['insights']['data']['tasks'][0]['task']['services'][0]['notes'] = 'changed'
        first['presentation']['vehicles'].clear()
        self.assertEqual(snapshot.model, expected)
        self.assertEqual(first['insights']['data']['vehicles'], expected['insights']['data']['vehicles'])
        with self.assertRaises(FrozenInstanceError):
            snapshot.as_of = AS_OF

    def test_reads_preserve_exact_bytes_mtimes_and_directory_members(self):
        def inventory():
            return {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.root.iterdir()}
        before = inventory()
        snapshot = self.capture()
        snapshot.model
        snapshot.revalidate()
        self.assertEqual(inventory(), before)

    def test_orphans_and_legacy_services_preserved(self):
        self.files[2].write_text(json.dumps({'schema_version': 1, 'profiles': []}))
        legacy = deepcopy(self.values[3])
        for record in legacy['tasks'][0]['services']:
            for key in list(record):
                if key not in ('service_id', 'completed_date', 'mileage', 'notes'):
                    del record[key]
        legacy['schema_version'] = 1
        self.files[3].write_text(json.dumps(legacy))
        model = self.capture().model
        self.assertEqual(model['stores']['maintenance']['data'], legacy)
        facts = model['insights']['data']
        self.assertEqual(facts['vehicles'][0]['profile_id'], PID)
        self.assertTrue(facts['vehicles'][0]['profile_missing'])
        self.assertEqual(facts['costs']['legacy_service_count'], 2)
        self.assertEqual(len(facts['service_history']), 2)

    def test_evaluation_inputs_retained_without_inference(self):
        model = self.capture(selected_profile_id=PID, explicit_current_mileage=10400,
                             cost_start_date='2026-10-05').model['insights']['data']
        self.assertEqual(model['selected_profile_id'], PID)
        self.assertEqual(model['explicit_current_mileage'], 10400)
        self.assertEqual(model['cost_start_date'], '2026-10-05')
        invalid = self.capture(selected_profile_id=PID, explicit_current_mileage='bad').model
        self.assertEqual(invalid['insights']['availability'], 'Available')
        self.assertTrue(invalid['insights']['data']['input_error'])

    def test_invalid_path_or_mutable_input_contracts_rejected(self):
        with self.assertRaises(ValueError):
            ds.capture_dashboard_sources(as_of=AS_OF)
        with self.assertRaises(ValueError):
            ds.capture_dashboard_sources(self.paths, checklist_path=self.files[0], as_of=AS_OF)
        with self.assertRaises(ValueError):
            self.capture(explicit_current_mileage=[])
        with self.assertRaises(ValueError):
            self.capture(cost_start_date=[])

    def fixture_inventory(self):
        """Include directory membership and source bytes/mtimes, excluding atime."""
        return {str(path.relative_to(self.root)): (
            path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
            for path in self.root.rglob('*')}

    def assert_stale_capture(self, snapshot):
        for action in (snapshot.revalidate, lambda: snapshot.model):
            with self.assertRaises(ValueError):
                action()

    def test_complete_alias_matrix_with_resolved_parent_spellings(self):
        child = self.root / 'child'
        child.mkdir()
        parent_alias = self.root / 'parent-alias'
        parent_alias.symlink_to(self.root, target_is_directory=True)
        for left, right in combinations(range(4), 2):
            original = self.files[left]
            spellings = (original, child / '..' / original.name,
                         parent_alias / original.name)
            for spelling in spellings:
                with self.subTest(pair=(ds.KINDS[left], ds.KINDS[right]), path=spelling):
                    paths = list(self.files)
                    paths[right] = spelling
                    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in self.files]
                    with patch.object(ds, 'read_source') as reader:
                        with self.assertRaisesRegex(ValueError, 'aliases'):
                            ds.capture_dashboard_sources(RuntimePaths(*paths), as_of=AS_OF)
                        reader.assert_not_called()
                    self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns)
                                      for p in self.files], before)

    def test_complete_hardlink_alias_matrix(self):
        for left, right in combinations(range(4), 2):
            with self.subTest(pair=(ds.KINDS[left], ds.KINDS[right])):
                alias = self.root / 'hardlink'
                os.link(self.files[left], alias)
                try:
                    paths = list(self.files)
                    paths[right] = alias
                    before = self.fixture_inventory()
                    with patch.object(ds, 'read_source') as reader:
                        with self.assertRaisesRegex(ValueError, 'aliases'):
                            ds.capture_dashboard_sources(RuntimePaths(*paths), as_of=AS_OF)
                        reader.assert_not_called()
                    self.assertEqual(self.fixture_inventory(), before)
                finally:
                    alias.unlink()

    def test_every_store_replacement_blocks_revalidation_and_model(self):
        for path in self.files:
            with self.subTest(store=path.name):
                snapshot = self.capture()
                original = path.stat()
                replacement = self.root / 'replacement'
                replacement.write_bytes(path.read_bytes())
                os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
                os.replace(replacement, path)
                self.assertNotEqual(path.stat().st_ino, original.st_ino)
                self.assertEqual(path.stat().st_mtime_ns, original.st_mtime_ns)
                before = self.fixture_inventory()
                self.assert_stale_capture(snapshot)
                self.assertEqual(self.fixture_inventory(), before)

    def test_parent_replacement_with_same_file_inodes_is_stale(self):
        active = self.root / 'active'
        active.mkdir()
        paths = tuple(active / p.name for p in self.files)
        for source, destination in zip(self.files, paths):
            destination.write_bytes(source.read_bytes())
        snapshot = ds.capture_dashboard_sources(RuntimePaths(*paths), as_of=AS_OF)
        identities = [(p.stat().st_ino, p.stat().st_mtime_ns) for p in paths]
        retired = self.root / 'retired'
        active.rename(retired)
        active.mkdir()
        for path in paths:
            (retired / path.name).rename(path)
        self.assertEqual([(p.stat().st_ino, p.stat().st_mtime_ns) for p in paths], identities)
        before = self.fixture_inventory()
        self.assert_stale_capture(snapshot)
        self.assertEqual(self.fixture_inventory(), before)

    def test_every_store_unsafe_substitution_blocks_model(self):
        for path in self.files:
            for kind in ('symlink', 'hardlink', 'directory'):
                with self.subTest(store=path.name, kind=kind):
                    snapshot = self.capture()
                    target = self.root / 'retained-source'
                    path.rename(target)
                    try:
                        if kind == 'symlink':
                            path.symlink_to(target)
                        elif kind == 'hardlink':
                            os.link(target, path)
                        else:
                            path.mkdir()
                        before = self.fixture_inventory()
                        self.assert_stale_capture(snapshot)
                        self.assertEqual(self.fixture_inventory(), before)
                    finally:
                        if kind == 'directory':
                            path.rmdir()
                        else:
                            path.unlink()
                        target.rename(path)

    def test_runtime_paths_omitted_maintenance_has_no_side_effects(self):
        before = self.fixture_inventory()
        paths = RuntimePaths(*self.files[:3])
        with self.assertRaisesRegex(ValueError, 'explicitly'):
            ds.capture_dashboard_sources(paths, as_of=AS_OF)
        self.assertEqual(self.fixture_inventory(), before)

    def test_missing_parent_for_each_explicit_path_has_no_side_effects(self):
        keywords = ('checklist_path', 'history_path', 'profiles_path', 'maintenance_path')
        for index in range(4):
            with self.subTest(store=ds.KINDS[index]):
                paths = list(self.files)
                paths[index] = self.root / 'absent' / 'nested' / self.files[index].name
                before = self.fixture_inventory()
                with self.assertRaises(FileNotFoundError):
                    ds.capture_dashboard_sources(**dict(zip(keywords, paths)), as_of=AS_OF)
                self.assertEqual(self.fixture_inventory(), before)
                self.assertFalse((self.root / 'absent').exists())

    def test_invalid_and_boundary_evaluation_dates_preserve_sources(self):
        before = self.fixture_inventory()
        for value in (None, '2026-10-21', '2026-02-30', 0, True, [],
                      datetime(2026, 10, 21)):
            with self.subTest(invalid=value), self.assertRaisesRegex(ValueError, 'explicit date'):
                ds.capture_dashboard_sources(self.paths, as_of=value)
            self.assertEqual(self.fixture_inventory(), before)
        for value in (date.min, date(2024, 2, 29), AS_OF, date.max):
            with self.subTest(valid=value):
                snapshot = ds.capture_dashboard_sources(self.paths, as_of=value)
                model = snapshot.model
                self.assertEqual(model['insights']['availability'], 'Available')
                self.assertEqual(model['insights']['data']['as_of'], value.isoformat())
                self.assertEqual(model['presentation']['as_of'], value.isoformat())
                snapshot.revalidate()
                self.assertEqual(self.fixture_inventory(), before)
