import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import app_entry as entry
from application_paths import packaged_paths
from data_backup import create_backup
from checklist import create_default_data
import json

class TestAppEntry(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "Home 空 白"
        self.paths = packaged_paths(self.home)
        self.paths.checklist.parent.mkdir(parents=True)
        self.dialogs, self.picker = MagicMock(), MagicMock()

    def prepare(self):
        return entry.prepare_data(None, self.paths, self.dialogs, self.picker)

    def test_initial_cancel_creates_no_stores(self):
        self.dialogs.askyesnocancel.return_value = None
        self.assertFalse(self.prepare())
        self.assertFalse(list(self.paths.checklist.parent.iterdir()))

    def test_new_checklist_choice_defers_creation(self):
        self.dialogs.askyesnocancel.return_value = False
        self.assertTrue(self.prepare())
        self.assertFalse(self.paths.checklist.exists())

    def test_picker_cancel(self):
        self.dialogs.askyesnocancel.return_value = True
        self.picker.askopenfilename.return_value = ""
        self.assertFalse(self.prepare())
        self.assertFalse(self.paths.checklist.exists())

    def backup(self):
        from application_paths import runtime_paths
        source = self.home / "fixture"; source.mkdir()
        paths = runtime_paths(source)
        paths.checklist.write_text(json.dumps(create_default_data()))
        return create_backup(paths, source)

    def test_restore_decline_leaves_destination_empty(self):
        self.picker.askopenfilename.return_value = str(self.backup())
        self.dialogs.askyesno.return_value = False
        self.assertFalse(self.prepare())
        self.assertFalse(self.paths.checklist.exists())

    def test_explicit_restore_preserves_exact_bytes(self):
        archive = self.backup()
        self.picker.askopenfilename.return_value = str(archive)
        self.dialogs.askyesno.return_value = True
        self.assertTrue(self.prepare())
        self.assertEqual(self.paths.checklist.read_bytes(), (archive.parent / 'checklist_data.json').read_bytes())
        self.assertFalse(self.paths.history.exists())

    def test_invalid_archive_no_default_creation(self):
        archive = self.home / 'bad.zip'; archive.write_bytes(b'bad')
        self.picker.askopenfilename.return_value = str(archive)
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.paths.checklist.exists())
        self.dialogs.askyesno.assert_not_called()

    def test_existing_destination_not_automatically_restored(self):
        self.paths.checklist.write_bytes(b'existing')
        self.assertTrue(self.prepare())
        self.dialogs.askyesnocancel.assert_not_called()
        self.assertEqual(self.paths.checklist.read_bytes(), b'existing')

    def test_restore_failure_propagates_without_default(self):
        self.picker.askopenfilename.return_value = str(self.backup())
        with patch.object(entry, 'restore_backup', side_effect=OSError('fixture')):
            with self.assertRaises(OSError): self.prepare()
        self.assertFalse(self.paths.checklist.exists())

    def test_duplicate_lock_and_release(self):
        directory = self.paths.checklist.parent
        with entry.instance_guard(directory):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                with entry.instance_guard(directory): pass
        with entry.instance_guard(directory): pass
        self.assertEqual([p.name for p in directory.iterdir()], ['.instance.lock'])

    def test_symlink_lock_rejected(self):
        target = self.home / 'protected'; target.write_bytes(b'original')
        (self.paths.checklist.parent / '.instance.lock').symlink_to(target)
        with self.assertRaises(OSError):
            with entry.instance_guard(self.paths.checklist.parent): pass
        self.assertEqual(target.read_bytes(), b'original')

    def test_startup_failure_native_alert_no_record_details(self):
        with patch.object(entry, 'run', side_effect=RuntimeError('private record')), patch.object(entry, 'native_error') as alert:
            self.assertEqual(entry.main(), 1)
        self.assertNotIn('private record', alert.call_args.args[0])

    def test_native_error_passes_message_as_argument(self):
        with patch.object(entry.subprocess, 'run') as run:
            entry.native_error('quote " and 空')
        self.assertEqual(run.call_args.args[0][-1], 'quote " and 空')

    def test_wrong_runtime_rejected_before_storage(self):
        with patch.object(entry.sys, 'platform', 'darwin'), patch.object(entry.platform, 'machine', return_value='arm64'), patch.object(entry.sys, 'executable', '/wrong/python'):
            with self.assertRaisesRegex(RuntimeError, 'Required runtime'): entry.run(home=self.home)

    def test_non_arm_platform_rejected(self):
        with patch.object(entry.platform, 'machine', return_value='x86_64'):
            with self.assertRaises(RuntimeError): entry.run(home=self.home)

    def test_run_injects_all_paths_without_real_gui(self):
        import gui
        import tkinter
        root = MagicMock()
        with patch.object(entry.sys, 'platform', 'darwin'), \
             patch.object(entry.platform, 'machine', return_value='arm64'), \
             patch.object(entry.sys, 'executable', entry.RUNTIME), \
             patch.object(tkinter, 'Tk', return_value=root), \
             patch.object(entry, 'prepare_data', return_value=True), \
             patch.object(gui, 'ChecklistApp') as app:
            entry.run(home=self.home)
        app.assert_called_once_with(root, self.paths.checklist, self.paths.history,
                                    self.paths.profiles, self.paths.maintenance)
        root.mainloop.assert_called_once()
        root.destroy.assert_called_once()

    def test_run_cancel_never_constructs_checklist(self):
        import gui
        import tkinter
        root = MagicMock()
        with patch.object(entry.sys, 'platform', 'darwin'), \
             patch.object(entry.platform, 'machine', return_value='arm64'), \
             patch.object(entry.sys, 'executable', entry.RUNTIME), \
             patch.object(tkinter, 'Tk', return_value=root), \
             patch.object(entry, 'prepare_data', return_value=False), \
             patch.object(gui, 'ChecklistApp') as app:
            entry.run(home=self.home)
        app.assert_not_called()
        root.mainloop.assert_not_called()
        root.destroy.assert_called_once()

    def test_known_startup_alerts_are_specific_and_safe(self):
        for code in ('architecture', 'runtime', 'duplicate'):
            with self.subTest(code=code), patch.object(entry, 'run', side_effect=entry.StartupFailure(code)), patch.object(entry, 'native_error') as alert:
                self.assertEqual(entry.main(), 1)
                alert.assert_called_once_with(entry.STARTUP_MESSAGES[code])

    def test_prelock_architecture_failure_creates_no_data_directory(self):
        home = self.home / 'absent architecture home'
        with patch.object(entry.sys, 'platform', 'darwin'), patch.object(entry.platform, 'machine', return_value='x86_64'):
            with self.assertRaises(entry.StartupFailure): entry.run(home=home)
        self.assertFalse(home.exists())

    def test_prelock_runtime_failure_creates_no_data_directory(self):
        home = self.home / 'absent runtime home'
        with patch.object(entry.sys, 'platform', 'darwin'), patch.object(entry.platform, 'machine', return_value='arm64'), patch.object(entry.sys, 'executable', '/wrong runtime'):
            with self.assertRaises(entry.StartupFailure): entry.run(home=home)
        self.assertFalse(home.exists())

    def test_concurrent_process_rejected_and_lock_released(self):
        import subprocess
        import sys
        directory = self.paths.checklist.parent
        code = ("import sys;sys.path.insert(0,sys.argv[1]);from app_entry import instance_guard,StartupFailure; "
                "guard=instance_guard(sys.argv[2]);guard.__enter__();guard.__exit__(None,None,None)")
        command = [sys.executable, '-I', '-B', '-c', code, str(Path(entry.__file__).resolve().parent), str(directory)]
        with entry.instance_guard(directory):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('already running', result.stderr)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
