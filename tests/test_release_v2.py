"""Check release metadata and documentation without running application launchers."""

from pathlib import Path
import plistlib
import unittest

from application_paths import RUNTIME


ROOT = Path(__file__).resolve().parents[1]


class TestReleaseV2(unittest.TestCase):
    def test_product_version_surfaces_agree(self):
        plist = plistlib.loads((ROOT / 'packaging/macos/Info.plist').read_bytes())
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        version = plist['CFBundleShortVersionString']
        self.assertEqual(version, '2.0.0')
        self.assertEqual(plist['CFBundleVersion'], version)
        self.assertIn(f'## v{version} optional Dashboard\n', readme)
        self.assertIn(f'## v{version} — November 21, 2026\n', changelog)

    def test_packaged_runtime_documentation_matches_launcher(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        launcher = (ROOT / 'packaging/macos/VehicleInspectionChecklist').read_text(
            encoding='utf-8')
        local_package = readme.split('### Local macOS package\n', 1)[1].split('\n## ', 1)[0]
        self.assertIn(f'`{RUNTIME}`', local_package)
        self.assertIn(f'runtime="{RUNTIME}"', launcher.splitlines())
        self.assertIn('ARM64 Python 3.15 with Tcl/Tk 9 Aqua', local_package)
        self.assertIn('/usr/bin/arch -arm64 "$runtime"', launcher)
        self.assertIn('Python 3.10', local_package)

    def test_dashboard_and_historical_compatibility_documentation(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        dashboard = readme.split('## v2.0.0 optional Dashboard\n', 1)[1].split('\n## ', 1)[0]
        for label in ('Open Dashboard', 'Refresh', 'Vehicle scope', 'Choose archive…',
                      'Assess selected archive', 'Clear selection'):
            with self.subTest(label=label):
                self.assertIn(f'**{label}**', dashboard)
        for statement in ('passive and read-only', 'do not display backup paths',
                          'No restore action exists in the dashboard.',
                          'version 1 and the `1.9.0` compatibility label'):
            with self.subTest(statement=statement):
                self.assertIn(statement, ' '.join(dashboard.split()))
        self.assertIn('\n## v1.9.0 local macOS application\n', readme)
        self.assertIn('New backups use release **1.7.0**, manifest version 1', readme)
        self.assertIn('not accept 1.9.0-labelled backups', readme)
