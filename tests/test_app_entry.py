from contextlib import ExitStack, contextmanager
import os
import plistlib
import stat
import sys
import tempfile
import tkinter
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import app_entry as entry
from application_paths import packaged_paths
from data_backup import create_backup
from checklist import create_default_data
import json


ALREADY_DESTROYED = 'cannot invoke "destroy" command: application has been destroyed'


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

    @contextmanager
    def packaged_main(self, root):
        import gui

        run = entry.run
        with patch.object(entry.sys, 'platform', 'darwin'), \
             patch.object(entry.platform, 'machine', return_value='arm64'), \
             patch.object(entry.sys, 'executable', entry.RUNTIME), \
             patch.object(tkinter, 'Tk', return_value=root), \
             patch.object(entry, 'prepare_data', return_value=True), \
             patch.object(gui, 'ChecklistApp') as app, \
             patch.object(entry, 'run', side_effect=lambda: run(home=self.home)), \
             patch.object(entry, 'native_error') as alert:
            yield app, alert

    def test_close_destroys_root_and_final_cleanup_is_successful(self):
        root = MagicMock()
        root.destroy.side_effect = [None, tkinter.TclError(ALREADY_DESTROYED)]
        root.mainloop.side_effect = root.destroy

        with self.packaged_main(root) as (_, alert):
            self.assertEqual(entry.main(), 0)
            alert.assert_not_called()
        self.assertEqual(root.destroy.call_count, 2)

    def test_repeated_cleanup_after_close_is_safe(self):
        root = MagicMock()
        root.destroy.side_effect = [None, tkinter.TclError(ALREADY_DESTROYED),
                                    tkinter.TclError(ALREADY_DESTROYED)]
        root.destroy()

        entry.destroy_root(root)
        entry.destroy_root(root)

        self.assertEqual(root.destroy.call_count, 3)

    def test_main_close_with_dashboard_child_destroys_child_once(self):
        Tk = tkinter.Tk
        root, dashboard = MagicMock(), MagicMock()
        root.children = {'dashboard': dashboard}
        dashboard.destroy.side_effect = root.children.clear
        root.tk.call.side_effect = [None, tkinter.TclError(ALREADY_DESTROYED)]
        root.destroy.side_effect = lambda: Tk.destroy(root)
        root.mainloop.side_effect = root.destroy

        with self.packaged_main(root) as (_, alert):
            self.assertEqual(entry.main(), 0)
            alert.assert_not_called()
        dashboard.destroy.assert_called_once_with()
        self.assertEqual(root.children, {})

    def test_unrelated_destroy_error_still_fails(self):
        for error in (tkinter.TclError('bad window path name "."'),
                      RuntimeError(ALREADY_DESTROYED)):
            with self.subTest(error=error):
                root = MagicMock()
                root.destroy.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    entry.destroy_root(root)
                self.assertIs(raised.exception, error)
                with self.packaged_main(root) as (_, alert):
                    self.assertEqual(entry.main(), 1)
                    alert.assert_called_once()

    def test_mainloop_failure_still_fails_after_already_destroyed_cleanup(self):
        root = MagicMock()
        root.mainloop.side_effect = RuntimeError('loop failed')
        root.destroy.side_effect = tkinter.TclError(ALREADY_DESTROYED)

        with self.packaged_main(root) as (_, alert):
            self.assertEqual(entry.main(), 1)
            self.assertIn('RuntimeError', alert.call_args.args[0])
        root.destroy.assert_called_once_with()

    def test_identical_cleanup_error_from_mainloop_still_fails(self):
        root = MagicMock()
        root.mainloop.side_effect = tkinter.TclError(ALREADY_DESTROYED)
        root.destroy.side_effect = tkinter.TclError(ALREADY_DESTROYED)

        with self.packaged_main(root) as (_, alert):
            self.assertEqual(entry.main(), 1)
            self.assertIn('TclError', alert.call_args.args[0])

    def test_startup_failure_still_fails_and_cleans_up_root(self):
        root = MagicMock()

        with self.packaged_main(root) as (app, alert):
            app.side_effect = ValueError('construction failed')
            self.assertEqual(entry.main(), 1)
            self.assertIn('ValueError', alert.call_args.args[0])
        root.mainloop.assert_not_called()
        root.destroy.assert_called_once_with()

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


class TestFrozenRuntime(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.contents = self.directory / 'Checklist with spaces.app/Contents'
        self.frameworks = self.contents / 'Frameworks'
        self.resources = self.contents / 'Resources'
        self.executable = self.contents / 'MacOS/VehicleInspectionChecklist'
        self.executable.parent.mkdir(parents=True)
        self.executable.touch()
        self.executable.chmod(0o755)
        (self.contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': self.executable.name,
            'CFBundlePackageType': 'APPL',
            'CFBundleIdentifier': 'local.kinu.vehicle-inspection-checklist',
        }))
        self.extension = self.contents.parent / entry.TK_EXTENSION
        for path in (self.resources / 'base_library.zip', self.resources / '_tcl_data/init.tcl',
                     self.resources / '_tk_data/tk.tcl', self.extension,
                     *(self.contents.parent / name for name in entry.NATIVE_FRAMEWORKS)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        for name, target in entry.STOCK_ALIASES.items():
            (self.frameworks / name).symlink_to(target)
        self.environment = {'TCL_LIBRARY': str(self.frameworks / '_tcl_data'),
                            'TK_LIBRARY': str(self.frameworks / '_tk_data')}
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.markers = {'frozen': True, '_MEIPASS': str(self.frameworks),
                        'executable': str(self.executable), 'platform': 'darwin', 'version_info': (3, 15, 0)}
        for name, value in self.markers.items():
            stack.enter_context(patch.object(sys, name, value, create=True))
        stack.enter_context(patch.dict(os.environ, self.environment))
        stack.enter_context(patch.object(entry.platform, 'machine', return_value='arm64'))
        stack.enter_context(patch.object(tkinter, 'TclVersion', 9.0))
        stack.enter_context(patch.object(tkinter, 'TkVersion', 9.0))
        stack.enter_context(patch.object(sys.modules['_tkinter'], '__file__', str(self.extension)))
        self.check_mapped_images = entry._frozen_images
        self.images = stack.enter_context(patch.object(entry, '_frozen_images'))
        self.tk = stack.enter_context(patch.object(tkinter, 'Tk'))

    def assert_runtime_failure(self):
        home = self.directory / 'untouched-home'
        with patch.object(entry, 'prepare_data') as prepare:
            with self.assertRaises(entry.StartupFailure) as caught:
                entry.run(home=home)
        self.assertEqual(str(caught.exception), entry.STARTUP_MESSAGES['runtime'])
        self.assertFalse(home.exists())
        prepare.assert_not_called()
        self.tk.assert_not_called()

    def test_valid_frozen_layout_before_root_creation(self):
        self.assertTrue(entry.validate_runtime())
        self.images.assert_called_once()
        self.assertEqual(self.images.call_args.args[0], self.contents.parent)
        self.assertEqual(os.environ['TCL_LIBRARY'], str(self.resources / '_tcl_data'))
        self.assertEqual(os.environ['TK_LIBRARY'], str(self.resources / '_tk_data'))
        self.tk.assert_not_called()

    def test_missing_frozen_marker(self):
        del sys.frozen
        try:
            self.assert_runtime_failure()
        finally:
            sys.frozen = True

    def test_missing_meipass_marker(self):
        del sys._MEIPASS
        try:
            self.assert_runtime_failure()
        finally:
            sys._MEIPASS = str(self.frameworks)

    def test_forged_marker_values(self):
        for marker in (False, 1, 'true'):
            with self.subTest(marker=marker), patch.object(sys, 'frozen', marker):
                self.assert_runtime_failure()

    def test_executable_outside_bundle(self):
        external = self.directory / 'python'
        external.touch()
        external.chmod(0o755)
        with patch.object(sys, 'executable', str(external)):
            self.assert_runtime_failure()

    def test_meipass_outside_bundle_or_in_different_bundle(self):
        other = self.directory / 'Other.app/Contents/Frameworks'
        other.mkdir(parents=True)
        for path in (self.directory, other):
            with self.subTest(path=path), patch.object(sys, '_MEIPASS', str(path)):
                self.assert_runtime_failure()

    def test_rejects_resource_symlink_escape(self):
        script = self.resources / '_tk_data/tk.tcl'
        external = self.directory / 'external.tcl'
        external.touch()
        script.unlink()
        script.symlink_to(external)
        self.assert_runtime_failure()

    def test_rejects_missing_resources_and_inconsistent_plist(self):
        resource = self.resources / '_tcl_data/init.tcl'
        resource.unlink()
        self.assert_runtime_failure()
        resource.touch()
        (self.contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'Other'}))
        self.assert_runtime_failure()

    def test_rejects_wrong_platform_architecture_and_python(self):
        for name, value in (('platform', 'linux'), ('version_info', (3, 14, 0))):
            with self.subTest(name=name), patch.object(sys, name, value):
                self.assert_runtime_failure()
        with patch.object(entry.platform, 'machine', return_value='x86_64'):
            self.assert_runtime_failure()

    def test_rejects_wrong_tcl_or_tk_version(self):
        for name in ('TclVersion', 'TkVersion'):
            with self.subTest(name=name), patch.object(tkinter, name, 8.6):
                self.assert_runtime_failure()

    def test_missing_tkinter_and_native_probe_errors_are_sanitized(self):
        with patch.dict(sys.modules, {'tkinter': None}):
            self.assert_runtime_failure()
        self.images.side_effect = OSError('/private/native runtime detail')
        with patch.object(entry, 'native_error') as alert:
            self.assertEqual(entry.main(), 1)
        alert.assert_called_once_with(entry.STARTUP_MESSAGES['runtime'])
        self.tk.assert_not_called()

    def test_native_check_rejects_foreign_image_before_root(self):
        self.images.side_effect = self.check_mapped_images
        dyld = MagicMock()
        dyld._dyld_image_count.return_value = 1
        dyld._dyld_get_image_name.return_value = os.fsencode(self.directory / 'foreign-image')
        with patch('ctypes.CDLL', return_value=dyld):
            self.assert_runtime_failure()

    def test_native_check_rejects_non_aqua_tk_before_root(self):
        self.images.side_effect = self.check_mapped_images
        native = (*entry.NATIVE_FRAMEWORKS, entry.TK_EXTENSION)
        dyld = MagicMock()
        dyld._dyld_image_count.return_value = len(native)
        dyld._dyld_get_image_name.side_effect = [os.fsencode(self.contents.parent / name) for name in native]
        with patch('ctypes.CDLL', side_effect=[dyld, object()]):
            self.assert_runtime_failure()

    def test_rejects_real_writable_data_volume_image_alias(self):
        external = self.directory / 'Writable.dylib'
        external.touch()
        alias = Path('/System/Volumes/Data') / external.relative_to('/')
        if not alias.exists():
            self.skipTest('macOS Data volume alias unavailable')
        self.assertTrue(alias.samefile(external))
        self.assertFalse(entry.is_system_image(alias))
        self.images.side_effect = self.check_mapped_images
        dyld = MagicMock()
        dyld._dyld_image_count.return_value = 1
        dyld._dyld_get_image_name.return_value = os.fsencode(alias)
        with patch('ctypes.CDLL', return_value=dyld):
            self.assert_runtime_failure()

    def test_accepts_only_the_sealed_entry_to_the_observed_os_cryptex(self):
        image = Path('/System/Library/Frameworks/WebKit.framework/Versions/A/WebKit')
        target = entry.OS_CRYPTEX_LIBRARY / image.relative_to('/System/Library')
        if image.resolve() != target:
            self.skipTest('the observed macOS WebKit Cryptex alias is unavailable')
        self.assertTrue(entry.is_system_image(image))
        self.assertFalse(entry.is_system_image(target))

    def test_only_exact_stock_alias_text_is_accepted(self):
        link = self.frameworks / '_tcl_data'
        for target in ('../Resources/../Resources/_tcl_data', '../Resources/./_tcl_data',
                       str(self.resources / '_tcl_data'), '../../outside',
                       '../Resources/_tk_data'):
            with self.subTest(target=target):
                link.unlink()
                link.symlink_to(target)
                self.assert_runtime_failure()

    def test_rejects_chained_alias_and_fourth_native_symlink(self):
        target = self.resources / '_tk_data'
        moved = self.resources / 'moved'
        target.rename(moved)
        target.symlink_to(moved.name)
        self.assert_runtime_failure()
        target.unlink()
        moved.rename(target)
        tk = self.contents.parent / entry.NATIVE_FRAMEWORKS[1]
        tk.rename(tk.with_name('other'))
        tk.symlink_to('other')
        self.assert_runtime_failure()

    def test_rejects_external_hardlinks_for_each_regular_resource(self):
        external = self.directory / 'external'
        external.touch()
        for path in (self.resources / 'base_library.zip', self.resources / '_tcl_data/init.tcl',
                     self.resources / '_tk_data/tk.tcl', self.contents / 'Info.plist',
                     *(self.contents.parent / name for name in entry.NATIVE_FRAMEWORKS)):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.unlink()
                path.hardlink_to(external)
                self.assert_runtime_failure()
                path.unlink()
                path.write_bytes(original)

    def test_rejects_symlinked_contents_frameworks_and_resources(self):
        for parent in (self.contents, self.frameworks, self.resources):
            with self.subTest(parent=parent.name):
                moved = parent.with_name('old-' + parent.name)
                parent.rename(moved)
                parent.symlink_to(moved.name)
                self.assert_runtime_failure()
                parent.unlink()
                moved.rename(parent)

    def test_revalidates_replaced_parent_alias_and_target(self):
        for path in (self.contents, self.frameworks, self.resources,
                     self.frameworks / '_tk_data', self.resources / 'base_library.zip'):
            with self.subTest(path=path.name):
                moved = path.with_name('old-' + path.name)
                def replace(*_):
                    path.rename(moved)
                    if moved.is_symlink():
                        path.symlink_to(os.readlink(moved))
                    elif moved.is_dir():
                        path.symlink_to(moved.name)
                    else:
                        path.touch()
                self.images.side_effect = replace
                self.assert_runtime_failure()
                path.unlink()
                moved.rename(path)

    def test_plist_rejects_fifo_socket_directory_and_device(self):
        path = self.contents / 'Info.plist'
        path.unlink()
        os.mkfifo(path)
        self.assert_runtime_failure()
        path.unlink()
        path.mkdir()
        self.assert_runtime_failure()
        path.rmdir()
        path.touch()
        real_stat = os.stat
        for kind in (stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            def special(name, *args, **kwargs):
                info = real_stat(name, *args, **kwargs)
                if name == 'Info.plist':
                    return os.stat_result((kind, *tuple(info)[1:]))
                return info
            with self.subTest(kind=kind), patch.object(entry.os, 'stat', side_effect=special):
                self.assert_runtime_failure()

    def test_plist_rejects_symlink_malformed_and_oversized_files(self):
        path = self.contents / 'Info.plist'
        path.unlink()
        external = self.directory / 'external.plist'
        external.touch()
        path.symlink_to(external)
        self.assert_runtime_failure()
        path.unlink()
        for data in (b'bad plist', b' ' * (entry.MAX_PLIST_BYTES + 1)):
            path.write_bytes(data)
            self.assert_runtime_failure()

    def test_plist_rejects_short_reads_growth_truncation_and_replacement(self):
        path = self.contents / 'Info.plist'
        original = path.read_bytes()
        read = os.read
        for operation in ('short', 'grow', 'truncate', 'replace'):
            with self.subTest(operation=operation):
                def change(fd, size):
                    if operation == 'short':
                        return b''
                    if operation == 'grow':
                        with path.open('ab') as stream:
                            stream.write(b'x')
                    elif operation == 'truncate':
                        path.write_bytes(b'')
                    else:
                        path.rename(path.with_suffix('.old'))
                        path.write_bytes(original)
                    return read(fd, size)
                with patch.object(entry.os, 'read', side_effect=change):
                    self.assert_runtime_failure()
                path.write_bytes(original)

    def test_plist_descriptors_close_on_failure_and_cancellation(self):
        original_open = os.open
        for error in (OSError('synthetic'), KeyboardInterrupt()):
            opened = []
            def track(*args, **kwargs):
                descriptor = original_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor
            with (self.subTest(error=type(error)), patch.object(entry.os, 'open', side_effect=track),
                  patch.object(entry.os, 'read', side_effect=error)):
                expected = entry.StartupFailure if isinstance(error, Exception) else KeyboardInterrupt
                with self.assertRaises(expected):
                    entry.validate_runtime()
            self.assertTrue(opened)
            for descriptor in opened:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_rejects_non_aqua_before_application_data_is_loaded(self):
        self.tk.return_value.tk.call.return_value = 'x11'
        with patch.object(entry, 'prepare_data') as prepare:
            with self.assertRaisesRegex(entry.StartupFailure, 'Required runtime'):
                entry.run(home=self.directory / 'isolated')
        prepare.assert_not_called()
        self.tk.return_value.destroy.assert_called_once()

    def test_dyld_rejects_fourth_alias_even_when_its_target_is_valid(self):
        self.images.side_effect = self.check_mapped_images
        required = [self.contents.parent / name
                    for name in (*entry.NATIVE_FRAMEWORKS, entry.TK_EXTENSION)]
        alias = self.frameworks / 'UnexpectedTkAlias'
        alias.symlink_to(required[1].name)
        alias_parent = self.directory / 'UnexpectedParent'
        alias_parent.symlink_to(self.directory, target_is_directory=True)
        tk = required[1]
        for path in (alias, alias_parent / tk.relative_to(self.directory)):
            with self.subTest(path=path):
                required[1] = path
                dyld = MagicMock()
                dyld._dyld_image_count.return_value = len(required)
                dyld._dyld_get_image_name.side_effect = list(map(os.fsencode, required))
                with patch('ctypes.CDLL', side_effect=[dyld, MagicMock()]):
                    self.assert_runtime_failure()

    def test_exact_stock_native_aliases_are_usable_but_reject_retargeting(self):
        python = self.frameworks / 'Python'
        python.symlink_to('Python.framework/Versions/3.15/Python')
        extension_directory = self.frameworks / 'python3.15'
        extension_directory.symlink_to('python3__dot__15')
        self.images.side_effect = self.check_mapped_images
        paths = [self.frameworks / 'Tcl', self.frameworks / 'Tk', python,
                 extension_directory / 'lib-dynload' / self.extension.name]
        dyld = MagicMock()
        dyld._dyld_image_count.return_value = len(paths)
        dyld._dyld_get_image_name.side_effect = list(map(os.fsencode, paths))
        with patch('ctypes.CDLL', side_effect=[dyld, MagicMock()]):
            self.assertTrue(entry.validate_runtime())
        python.unlink()
        python.symlink_to('./Python.framework/Versions/3.15/Python')
        self.assert_runtime_failure()

    def test_native_inventory_rejects_a_hardlink_even_if_it_is_not_mapped(self):
        (self.frameworks / 'extra.dylib').hardlink_to(self.frameworks / 'Tk')
        self.assert_runtime_failure()

    def test_descriptor_closes_if_interrupted_after_registration(self):
        seen = []
        def trace(frame, event, value):
            if frame.f_code is entry.owned_fd.__code__ and event == 'return':
                seen.append(value)
                raise KeyboardInterrupt
            return trace
        previous = sys.gettrace()
        sys.settrace(trace)
        try:
            with self.assertRaises(KeyboardInterrupt), ExitStack() as stack:
                entry.owned_fd(stack, self.directory, os.O_RDONLY | os.O_DIRECTORY)
        finally:
            sys.settrace(previous)
        self.assertEqual(len(seen), 1)
        with self.assertRaises(OSError):
            os.fstat(seen[0])

    def test_frozen_launch_reuses_single_root_and_existing_data_entry(self):
        import gui

        self.tk.return_value.tk.call.return_value = 'aqua'
        with patch.object(entry, 'prepare_data', return_value=True), patch.object(gui, 'ChecklistApp') as app:
            entry.run(home=self.directory / 'isolated')
        self.tk.assert_called_once_with()
        app.assert_called_once()
        self.tk.return_value.mainloop.assert_called_once_with()
        self.tk.return_value.destroy.assert_called_once_with()
