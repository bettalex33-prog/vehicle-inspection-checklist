"""All source-capture filesystem tests use disposable stores."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import maintenance_insights_sources as sources
from test_maintenance_insights import AS_OF, positive_task, schedule, history, inspection, profile


def stores(root):
    paths = tuple(root / name for name in ('custom-maintenance.json', 'custom-profiles.json', 'custom-inspections.json'))
    for path, data in zip(paths, (schedule([positive_task()]), {'schema_version': 1, 'profiles': [profile()]}, history([inspection()]))):
        path.write_text(json.dumps(data), encoding='utf-8')
    return paths


class TestInsightsSources(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = stores(self.root)

    def capture(self, **kwargs):
        return sources.capture_insights(*self.paths, as_of=AS_OF, **kwargs)

    def test_missing_stores_are_empty_without_creation(self):
        for p in self.paths:
            p.unlink()
        model = self.capture().model
        self.assertEqual(model['costs']['service_count'], 0)
        self.assertEqual(sum(w['code'] == 'source_missing' for w in model['warnings']), 3)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_frozen_capture_and_independent_model_access(self):
        snapshot = self.capture()
        model = snapshot.model
        model['tasks'][0]['task']['title'] = 'changed'
        self.assertNotEqual(snapshot.model['tasks'][0]['task']['title'], 'changed')
        with self.assertRaises(AttributeError):
            snapshot.as_of = AS_OF

    def test_malformed_utf8_json_duplicate_keys_and_constants(self):
        for raw in (b'\xff', b'{', b'{"schema_version":1,"schema_version":1,"tasks":[]}',
                    b'{"schema_version":NaN,"tasks":[]}', b'{"schema_version":Infinity,"tasks":[]}'):
            self.paths[0].write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.capture()

    def test_unsupported_schemas_and_bad_snapshots_fail_complete_capture(self):
        for index, value in ((0, {'schema_version': 9, 'tasks': []}), (1, {'schema_version': 1, 'profiles': [{}]}),
                             (2, {'schema_version': 4, 'inspections': []})):
            original = self.paths[index].read_bytes()
            self.paths[index].write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                self.capture()
            self.paths[index].write_bytes(original)

    def test_bounded_reads_and_deep_json_rejected(self):
        with patch.object(sources, 'MAX_FILE', 10), self.assertRaises(ValueError):
            self.capture()
        self.paths[0].write_text('[' * 140 + '0' + ']' * 140)
        with self.assertRaisesRegex(ValueError, 'nesting'):
            self.capture()

    def test_symlink_hardlink_and_directory_sources_refused(self):
        path = self.paths[0]
        original = path.read_bytes()
        target = self.root / 'target'; target.write_bytes(original)
        for kind in ('symlink', 'hardlink', 'directory'):
            path.unlink()
            if kind == 'symlink': path.symlink_to(target)
            elif kind == 'hardlink': os.link(target, path)
            else: path.mkdir()
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.capture()
            if kind == 'directory': path.rmdir(); path.write_bytes(original)

    def test_source_paths_cannot_alias(self):
        for other in (self.paths[0], self.root / '.' / self.paths[0].name):
            with self.assertRaises(ValueError):
                sources.capture_insights(self.paths[0], other, self.paths[2], as_of=AS_OF)

    def test_changed_source_invalidates_model_and_revalidation(self):
        snapshot = self.capture()
        self.paths[2].write_bytes(self.paths[2].read_bytes() + b'\n')
        with self.assertRaises(ValueError): snapshot.revalidate()
        with self.assertRaises(ValueError): _ = snapshot.model

    def test_deleted_source_is_stale_not_empty(self):
        snapshot = self.capture(); self.paths[1].unlink()
        with self.assertRaises(ValueError): _ = snapshot.model

    def test_recreated_same_bytes_are_stale(self):
        snapshot = self.capture()
        path = self.paths[0]; original = path.read_bytes()
        replacement = self.root / 'replacement'; replacement.write_bytes(original)
        os.replace(replacement, path)
        with self.assertRaises(ValueError): snapshot.revalidate()

    def test_source_change_during_capture_refused(self):
        original = sources.read_source
        def read(path):
            token = original(path)
            if Path(path) == self.paths[2]:
                self.paths[0].write_bytes(self.paths[0].read_bytes() + b'\n')
            return token
        with patch.object(sources, 'read_source', side_effect=read), self.assertRaises(ValueError):
            self.capture()

    def test_missing_source_appearing_and_link_changes_are_stale(self):
        original = self.paths[2].read_bytes(); self.paths[2].unlink()
        snapshot = self.capture(); self.paths[2].write_bytes(original)
        with self.assertRaises(ValueError): snapshot.revalidate()
        snapshot = self.capture()
        os.link(self.paths[1], self.root / 'extra-link')
        with self.assertRaises(ValueError): snapshot.revalidate()
