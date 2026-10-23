"""Exercise the unchanged CLI using a temporary script copy and data stores."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class TestCLIRegression(unittest.TestCase):
    def test_commands_progress_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'checklist.py', root / 'checklist.py')
            stores = ('inspection_history.json', 'vehicle_profiles.json', 'maintenance_schedule.json')
            for name in stores:
                (root / name).write_bytes(b'unchanged')
            def run(*args):
                return subprocess.run([sys.executable, str(root / 'checklist.py'), *args],
                                      capture_output=True, text=True, cwd=root)
            self.assertEqual(run('progress').stdout, 'Progress: [░░░░░░░] 0/7 (0%)\n')
            self.assertIn('Oil & Fluid Levels', run('list').stdout)
            self.assertEqual(run('complete', 'oil').stdout, 'oil marked complete. Progress: [█░░░░░░]\n')
            self.assertEqual(run('progress').stdout, 'Progress: [█░░░░░░] 1/7 (14%)\n')
            before = (root / 'checklist_data.json').read_bytes()
            self.assertEqual(run('complete', 'oil').returncode, 0)
            error = run('complete', 'unknown')
            self.assertEqual(error.returncode, 2)
            self.assertIn("Unknown item 'unknown'", error.stderr)
            self.assertEqual((root / 'checklist_data.json').read_bytes(), before)
            self.assertEqual(run('reset').stdout, 'All items cleared. Progress reset to 0/7 (0%)\n')
            self.assertEqual(run().returncode, 1)
            for name in stores:
                self.assertEqual((root / name).read_bytes(), b'unchanged')
                (root / name).unlink()
            self.assertEqual(run('progress').returncode, 0)
            for name in stores:
                self.assertFalse((root / name).exists())
