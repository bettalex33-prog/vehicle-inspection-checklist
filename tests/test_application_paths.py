import unittest
import tempfile
from pathlib import Path
from application_paths import packaged_paths, source_paths
from data_backup import NAMES

class TestApplicationPaths(unittest.TestCase):
    def test_packaged_spaces_unicode_no_creation(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "Home 空 白"
            paths = packaged_paths(home)
            self.assertEqual(tuple(paths.mapping()), NAMES)
            self.assertEqual(paths.checklist.parent, home / "Library/Application Support/Vehicle Inspection Checklist")
            self.assertFalse(home.exists())

    def test_source_mode_is_code_relative(self):
        with tempfile.TemporaryDirectory() as d:
            code = Path(d) / "source 空" / "gui.py"
            self.assertEqual(source_paths(code).checklist, code.resolve().parent / "checklist_data.json")
