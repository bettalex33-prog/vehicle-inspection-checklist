import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from build_macos_app import build_app, PRODUCTION_FILES
from application_paths import RUNTIME

ROOT = Path(__file__).resolve().parents[1]

class TestMacOSPackaging(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = build_app(self.root / "Application 空 白.app")

    def test_exact_bundle_content_and_permissions(self):
        files = {str(p.relative_to(self.bundle)) for p in self.bundle.rglob('*') if p.is_file()}
        expected = {'Contents/Info.plist', 'Contents/MacOS/VehicleInspectionChecklist'}
        expected.update('Contents/Resources/app/' + n for n in PRODUCTION_FILES)
        self.assertEqual(files, expected)
        self.assertEqual(len(PRODUCTION_FILES), len(set(PRODUCTION_FILES)))
        executable = self.bundle / 'Contents/MacOS/VehicleInspectionChecklist'
        self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
        for name in PRODUCTION_FILES:
            self.assertEqual((self.bundle / 'Contents/Resources/app' / name).read_bytes(), (ROOT / name).read_bytes())

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
