import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from textwrap import dedent, indent
import unittest
from build_macos_app import build_app, PRODUCTION_FILES
from application_paths import RUNTIME

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_MODULES = {
    'dashboard_app', 'dashboard_gui', 'dashboard_controller',
    'dashboard_model', 'dashboard_sources', 'backup_health',
}

class TestMacOSPackaging(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = build_app(self.root / "Application 空 白.app")
        self.code = self.bundle / 'Contents/Resources/app'

    def test_exact_bundle_content_and_permissions(self):
        files = {str(p.relative_to(self.bundle)) for p in self.bundle.rglob('*') if p.is_file()}
        expected = {'Contents/Info.plist', 'Contents/MacOS/VehicleInspectionChecklist'}
        expected.update('Contents/Resources/app/' + n for n in PRODUCTION_FILES)
        self.assertEqual(files, expected)
        self.assertEqual(len(PRODUCTION_FILES), 24)
        self.assertEqual(len(PRODUCTION_FILES), len(set(PRODUCTION_FILES)))
        self.assertLessEqual({name + '.py' for name in DASHBOARD_MODULES},
                             {path.name for path in self.code.iterdir()})
        executable = self.bundle / 'Contents/MacOS/VehicleInspectionChecklist'
        self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
        for name in PRODUCTION_FILES:
            self.assertEqual((self.bundle / 'Contents/Resources/app' / name).read_bytes(), (ROOT / name).read_bytes())

    def run_bundled_python(self, script):
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-c', dedent(script), str(self.code)],
            cwd=self.root, env=dict(os.environ, HOME=str(self.root)),
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dashboard_import_closure_resolves_from_bundle(self):
        closure = DASHBOARD_MODULES | {
            'checklist', 'data_backup', 'fleet_dashboard', 'inspection_history',
            'maintenance_insights', 'maintenance_insights_sources',
            'maintenance_schedule', 'vehicle_profiles',
        }
        self.run_bundled_python(f"""
            import sys
            from pathlib import Path

            code = Path(sys.argv[1])
            sys.path.insert(0, str(code))
            import dashboard_app

            for name in {sorted(closure)!r}:
                assert Path(sys.modules[name].__file__) == code / (name + '.py'), name
        """)

    def run_fake_tk_startup(self, on_mainloop):
        setup = dedent("""
            import sys
            from pathlib import Path
            import tkinter as tk
            from tkinter import filedialog, messagebox, simpledialog, ttk
            from unittest.mock import Mock, patch

            code = Path(sys.argv[1])
            sys.path.insert(0, str(code))
            import app_entry

            root = Mock()
            tk.Tk = Mock(return_value=root)
            tk.Toplevel = Mock()
            for name in ('Frame', 'Label', 'Entry', 'Text', 'Checkbutton',
                         'StringVar', 'BooleanVar'):
                setattr(tk, name, Mock())
            for name in ('Frame', 'Label', 'Entry', 'Combobox', 'Button',
                         'Notebook', 'Scrollbar'):
                setattr(ttk, name, Mock())
            tk.Button = Mock()
            messagebox.askyesnocancel = Mock(return_value=False)
            messagebox.showerror = Mock()
            simpledialog.askstring = Mock(return_value='2026-11-11')
            filedialog.askopenfilename = Mock()

            def at_mainloop():
                commands = [call.kwargs['command'] for call in tk.Button.call_args_list
                            if call.kwargs.get('text') == 'Open Dashboard']
                assert len(commands) == 1
                open_dashboard, = commands
        """)
        run = dedent("""
            root.mainloop.side_effect = at_mainloop
            # Exercise the entry point under either test interpreter.
            with (
                patch.object(app_entry.sys, 'platform', 'darwin'),
                patch.object(app_entry.platform, 'machine', return_value='arm64'),
                patch.object(app_entry, 'RUNTIME', sys.executable),
            ):
                app_entry.run(home=Path.home())
            tk.Tk.assert_called_once_with()
            root.mainloop.assert_called_once_with()
            root.destroy.assert_called_once_with()
            filedialog.askopenfilename.assert_not_called()
        """)
        self.run_bundled_python(setup + indent(dedent(on_mainloop), '    ') + run)

    def test_packaged_startup_leaves_dashboard_passive(self):
        self.run_fake_tk_startup(f"""
            assert not set({sorted(DASHBOARD_MODULES)!r}).intersection(sys.modules)
            assert open_dashboard.__self__.optional_dashboard is None
            simpledialog.askstring.assert_not_called()
            tk.Toplevel.assert_not_called()
            messagebox.showerror.assert_not_called()
        """)

    def test_open_dashboard_uses_bundled_modules_with_fake_tk(self):
        self.run_fake_tk_startup(f"""
            assert 'dashboard_app' not in sys.modules
            window = open_dashboard()
            assert window is not None
            assert window.controller.state['status'] == 'empty'
            simpledialog.askstring.assert_called_once()
            tk.Toplevel.assert_called_once_with(root)
            messagebox.showerror.assert_not_called()
            for name in {sorted(DASHBOARD_MODULES)!r}:
                assert Path(sys.modules[name].__file__) == code / (name + '.py'), name
        """)

    def test_missing_optional_module_shows_sanitized_error(self):
        (self.code / 'backup_health.py').unlink()
        self.run_fake_tk_startup("""
            assert open_dashboard() is None
            assert open_dashboard.__self__.optional_dashboard is None
            messagebox.showerror.assert_called_once_with(
                'Open Dashboard',
                'Dashboard is unavailable. Check that the optional dashboard modules are installed.',
                parent=root,
            )
            simpledialog.askstring.assert_not_called()
            tk.Toplevel.assert_not_called()
        """)

    def test_nonproduction_checkout_files_are_excluded(self):
        source = self.root / 'source'
        source.mkdir()
        for path in self.code.iterdir():
            (source / path.name).write_bytes(path.read_bytes())
        for name in ('Info.plist', 'VehicleInspectionChecklist'):
            path = source / 'packaging/macos' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / 'packaging/macos' / name).read_bytes())
        for name in (
            'checklist_data.json', 'inspection_history.json', 'vehicle_profiles.json',
            'maintenance_schedule.json', 'backups/backup.zip', 'tests/test_fixture.py',
            '__pycache__/dashboard_app.cpython-310.pyc', 'logs/startup.log',
            'exports/history.csv', '.git/HEAD', 'tmp/dashboard_fixture.json',
            'temporary_fixture.py',
        ):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic fixture')

        bundle = build_app(self.root / 'Synthetic.app', source=source)
        self.assertEqual({p.relative_to(bundle) for p in bundle.rglob('*')},
                         {p.relative_to(self.bundle) for p in self.bundle.rglob('*')})

    def test_plist_local_release(self):
        info = plistlib.loads((self.bundle / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleShortVersionString'], '1.9.0')
        self.assertEqual(info['CFBundleExecutable'], 'VehicleInspectionChecklist')
        self.assertEqual(info['LSMinimumSystemVersion'], '15.5')
        self.assertNotIn('LSUIElement', info)

    def test_existing_output_never_replaced(self):
        with self.assertRaises(FileExistsError): build_app(self.bundle)

    def test_launcher_syntax_and_isolation(self):
        launcher = self.bundle / 'Contents/MacOS/VehicleInspectionChecklist'
        subprocess.run(['bash', '-n', str(launcher)], check=True)
        text = launcher.read_text()
        self.assertIn(RUNTIME, text)
        self.assertIn('/usr/bin/arch -arm64 "$runtime" -I -B "$code_dir/app_entry.py"', text)
        self.assertNotIn('/usr/bin/python', text)
        self.assertNotIn('read -r', text)

    def test_missing_runtime_visible_failure_without_gui(self):
        # Modify only the disposable fixture; use a stub for the native dialog.
        launcher = self.bundle / 'Contents/MacOS/VehicleInspectionChecklist'
        stub = self.root / 'dialog'; stub.write_text('#!/bin/sh\nprintf "%s\n" "$@"\n'); stub.chmod(0o755)
        launcher.write_text(launcher.read_text().replace(RUNTIME, str(self.root / 'missing python')).replace('/usr/bin/osascript', '"' + str(stub) + '"'))
        result = subprocess.run(['bash', str(launcher)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('runtime is unavailable', result.stdout)

    def test_runtime_failure_visible_without_gui(self):
        launcher = self.bundle / 'Contents/MacOS/VehicleInspectionChecklist'
        stub = self.root / 'dialog'; stub.write_text('#!/bin/sh\nprintf "%s\n" "$@"\n'); stub.chmod(0o755)
        runtime = self.root / 'failed runtime'; runtime.write_text('#!/bin/sh\nexit 7\n'); runtime.chmod(0o755)
        launcher.write_text(launcher.read_text().replace(RUNTIME, str(runtime)).replace('/usr/bin/osascript', '"' + str(stub) + '"'))
        result = subprocess.run(['bash', str(launcher)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('startup failed', result.stdout)

    def test_isolated_bootstrap_arbitrary_cwd_no_bundle_writes(self):
        code = self.bundle / 'Contents/Resources/app'
        # Fixture entry replacement exercises isolated import bootstrap without Tk or live data.
        entry = code / 'app_entry.py'
        original = entry.read_text()
        entry.write_text(original.split('if __name__ == "__main__":\n    raise SystemExit(main())')[0] + '\nprint(packaged_paths("/fixture home 空").checklist)\n')
        before = {str(p): hashlib.sha256(p.read_bytes()).digest() for p in self.bundle.rglob('*') if p.is_file()}
        env = dict(os.environ, PYTHONPATH='/invalid', PYTHONHOME='/invalid')
        result = subprocess.run([sys.executable, '-I', '-B', str(entry)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/fixture home 空/Library/Application Support/Vehicle Inspection Checklist/checklist_data.json', result.stdout)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).digest() for p in self.bundle.rglob('*') if p.is_file()})
        self.assertFalse(list(self.bundle.rglob('__pycache__')))

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS architecture regression')
    def test_translated_parent_starts_arm64_bootstrap_without_gui(self):
        code = self.bundle / 'Contents/Resources/app'
        entry = code / 'app_entry.py'
        # Instrument only the disposable entry; SystemExit at Tk prevents any GUI.
        prelude = ("import tkinter, platform, sys, os\n"
                   "def blocked_tk(*args, **kwargs):\n"
                   "    print('TK_BOUNDARY', platform.machine(), sys.flags.isolated, sys.dont_write_bytecode, os.environ['HOME'], flush=True)\n"
                   "    raise SystemExit(0)\n"
                   "tkinter.Tk = blocked_tk\n")
        entry.write_text(prelude + entry.read_text())
        before = {str(p): hashlib.sha256(p.read_bytes()).digest() for p in self.bundle.rglob('*') if p.is_file()}
        home = self.root / 'Temporary HOME 空 白'; home.mkdir()
        parent = ("import subprocess,platform,sys; print('PARENT',platform.machine(),flush=True); "
                  "raise SystemExit(subprocess.run(['/bin/bash',sys.argv[1]]).returncode)")
        env = dict(os.environ, HOME=str(home), PYTHONPATH='/invalid', PYTHONHOME='/invalid')
        result = subprocess.run(['/usr/bin/arch', '-x86_64', RUNTIME, '-I', '-B', '-c', parent,
                                 str(self.bundle / 'Contents/MacOS/VehicleInspectionChecklist')],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PARENT x86_64', result.stdout)
        self.assertIn('TK_BOUNDARY arm64 1 True ' + str(home), result.stdout)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).digest() for p in self.bundle.rglob('*') if p.is_file()})
        self.assertFalse(list(self.bundle.rglob('__pycache__')))
        self.assertEqual([p.name for p in home.rglob('*') if p.is_file()], ['.instance.lock'])
