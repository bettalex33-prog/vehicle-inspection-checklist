"""Dashboard publication uses only disposable TemporaryDirectory stores."""
from copy import deepcopy
import csv
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import fleet_dashboard_exports as ex
from test_fleet_dashboard import TODAY, PID, profile, task, service, build


class TestFleetDashboardExports(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.schedule = self.root / 'custom-maintenance.json'
        self.profiles = self.root / 'custom-profiles.json'
        self.schedule.write_text(json.dumps({'schema_version': 2, 'tasks': [task(
            title='=SUM(1)', notes='<script>"bad"</script>', services=[
                {k: v for k, v in service(100, completed_date='2026-10-21').items()
                 if k in ('service_id', 'completed_date', 'mileage', 'notes')},
                service(101, labor_cost='45.50', parts_cost='12.25', tax='4.62', provider='<img src=x>')])]}))
        self.profiles.write_text(json.dumps({'schema_version': 1, 'profiles': [profile()]}))
        self.output = self.root / 'dashboard.html'

    def capture(self, **options):
        return ex.capture_dashboard(self.schedule, self.profiles, today=TODAY, **options)

    def test_fixed_csv_schema_types_formulas_crlf_and_determinism(self):
        model = self.capture().model
        before = deepcopy(model)
        text = ex.render_dashboard_csv(model)
        self.assertTrue(text.startswith('\ufeff'))
        self.assertIn('\r\n', text)
        rows = list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))))
        self.assertEqual(tuple(rows[0]), ex.COLUMNS)
        self.assertEqual({r['row_type'] for r in rows},
                         {'status_total', 'vehicle_summary', 'task', 'cost_overall', 'cost_vehicle', 'cost_task'})
        cost = next(r for r in rows if r['row_type'] == 'cost_overall')
        self.assertEqual(cost['total_cost_cents'], '6237')
        self.assertEqual(cost['total_cost'], '$62.37')
        self.assertEqual(cost['legacy_service_count'], '1')
        t = next(r for r in rows if r['row_type'] == 'task')
        self.assertEqual(t['task_title'], "'=SUM(1)")
        self.assertEqual(t['profile_missing'], 'false')
        self.assertEqual(model, before)
        self.assertEqual(text, ex.render_dashboard_csv(self.capture().model))
        for prefix in ('', ' \t', '\ufeff', '\x00', '\u2003\x00'):
            for formula in ('=1', '+1', '-1', '@1'):
                self.assertTrue(ex._safe_cell(prefix + formula).startswith("'"))

    def test_html_full_history_snapshot_escaping_and_cost_filter(self):
        snap = self.capture(cost_start_date='2026-10-23')
        report = ex.render_dashboard_html(snap.model)
        for text in ('&lt;script&gt;', '&quot;', '&lt;img', 'Captured profile snapshot',
                     'Complete service history (unfiltered)', '2026-10-21', '$62.37', '43000',
                     'Not recorded', 'cost_vehicle', 'cost_task', 'task-' + task()['task_id']):
            self.assertIn(text, report)
        self.assertNotIn('<script>', report)
        self.assertNotIn('<img', report)
        self.assertEqual(snap.model['costs']['service_count'], 1)
        self.assertEqual(len(snap.model['tasks'][0]['task']['services']), 2)

    def test_empty_missing_sources_no_creation_and_stable_header(self):
        self.schedule.unlink(); self.profiles.unlink()
        snap = self.capture()
        ex.export_dashboard_csv(snap, self.root / 'empty.csv')
        self.assertFalse(self.schedule.exists()); self.assertFalse(self.profiles.exists())
        rows = list(csv.DictReader(io.StringIO(ex.render_dashboard_csv(snap.model).lstrip('\ufeff'))))
        self.assertEqual(tuple(rows[0]), ex.COLUMNS)
        self.assertEqual(rows[-1]['total_cost_cents'], '')
        self.assertEqual(rows[-1]['total_cost'], 'Not recorded')

    def test_legacy_read_never_upgrades_and_snapshot_is_independent(self):
        legacy = task(services=[{k: v for k, v in service().items()
                                if k in ('service_id', 'completed_date', 'mileage', 'notes')}])
        self.schedule.write_text(json.dumps({'schema_version': 1, 'tasks': [legacy]}, indent=4))
        before = self.schedule.read_bytes()
        snap = self.capture()
        model = snap.model; model['tasks'].clear()
        self.assertEqual(len(snap.model['tasks']), 1)
        ex.export_dashboard_html(snap, self.output)
        self.assertEqual(self.schedule.read_bytes(), before)

    def test_duplicate_keys_nonfinite_and_corruption_in_both_sources(self):
        for path in (self.schedule, self.profiles):
            original = path.read_bytes()
            for raw in (b'broken', b'\xff', b'{"schema_version":1,"schema_version":1}',
                        b'{"x":NaN}', b'{"schema_version":9,"tasks":[]}',
                        b'{"schema_version":1,"profiles":[{"profile_id":"x","profile_id":"y"}]}'):
                path.write_bytes(raw)
                with self.assertRaises((ValueError, UnicodeError)):
                    self.capture()
                self.assertEqual(path.read_bytes(), raw)
                self.assertFalse(self.output.exists())
            path.write_bytes(original)

    def test_all_runtime_custom_resolved_symlink_and_hardlink_aliases(self):
        snapshot = self.capture()
        custom = self.root / 'custom-checklist.json'; custom.write_bytes(b'protected')
        paths = [self.schedule, self.profiles, custom]
        for name in ex.NAMES:
            p = self.root / name; p.write_bytes(b'protected'); paths.append(p)
        for path in paths:
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                ex.export_dashboard_html(snapshot, path, overwrite=True, runtime_paths=(custom,))
            self.assertEqual(path.read_bytes(), before)
        folder = self.root / 'alias'; folder.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            ex.export_dashboard_csv(snapshot, folder / self.schedule.name, overwrite=True)
        link = self.root / 'symlink'; link.symlink_to(custom)
        hard = self.root / 'hardlink'; os.link(custom, hard)
        for path in (link, hard):
            with self.assertRaises(ValueError):
                ex.export_dashboard_csv(snapshot, path, overwrite=True, runtime_paths=(custom,))
        # Even an unrelated hard-linked output may not be replaced.
        with self.assertRaises(ValueError):
            ex.export_dashboard_csv(snapshot, hard, overwrite=True)

    def test_sources_reject_links_special_files_oversize_and_path_collision(self):
        for path in (self.schedule, self.profiles):
            original = path.read_bytes(); path.unlink()
            path.symlink_to(self.root / 'missing')
            with self.assertRaises(ValueError):
                self.capture()
            path.unlink(); path.mkdir()
            with self.assertRaises(ValueError):
                self.capture()
            path.rmdir(); path.write_bytes(original)
            link = self.root / 'hard'; os.link(path, link)
            with self.assertRaises(ValueError):
                self.capture()
            link.unlink()
        with self.assertRaises(ValueError):
            ex.capture_dashboard(self.schedule, self.schedule, today=TODAY)
        with patch.object(ex, 'MAX_FILE', 1), self.assertRaises(ValueError):
            self.capture()

    def test_no_overwrite_without_flag_and_atomic_same_directory(self):
        snap = self.capture()
        self.output.write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            ex.export_dashboard_html(snap, self.output)
        self.assertEqual(self.output.read_bytes(), b'old')
        actual = ex.os.replace
        def publish(source, target):
            self.assertEqual(source.parent, self.output.parent)
            self.assertIn(b'<!doctype html>', source.read_bytes())
            return actual(source, target)
        with patch.object(ex.os, 'replace', side_effect=publish):
            ex.export_dashboard_html(snap, self.output, overwrite=True)
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_stale_sources_before_and_immediately_before_publication(self):
        for path in (self.schedule, self.profiles):
            original = path.read_bytes()
            snap = self.capture(); self.output.write_bytes(b'old')
            path.write_bytes(original + b'\n')
            with self.assertRaises(ValueError):
                ex.export_dashboard_html(snap, self.output, overwrite=True)
            self.assertEqual(self.output.read_bytes(), b'old')
            path.write_bytes(original)
            snap = self.capture()
            actual = ex.os.fsync
            def changed(fd):
                actual(fd); path.write_bytes(original + b'\n')
            with patch.object(ex.os, 'fsync', side_effect=changed), self.assertRaises(ValueError):
                ex.export_dashboard_html(snap, self.output, overwrite=True)
            self.assertEqual(self.output.read_bytes(), b'old')
            self.assertFalse(list(self.root.glob('.*.tmp')))
            path.write_bytes(original)

    def test_newer_output_never_overwritten(self):
        snap = self.capture(); self.output.write_bytes(b'old')
        actual = ex.os.fsync
        def changed(fd):
            actual(fd); self.output.write_bytes(b'newer report')
        with patch.object(ex.os, 'fsync', side_effect=changed), self.assertRaises(ValueError):
            ex.export_dashboard_html(snap, self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b'newer report')
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_confirmation_fingerprint_rejects_changed_output(self):
        snap = self.capture(); self.output.write_bytes(b'confirmed report')
        confirmed = ex.output_fingerprint(self.output)
        self.output.write_bytes(b'newer report')
        with self.assertRaises(ValueError):
            ex.export_dashboard_html(snap, self.output, overwrite=True, expected_output=confirmed)
        self.assertEqual(self.output.read_bytes(), b'newer report')
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_unchanged_sources_and_output_when_rendering_fails(self):
        snap = self.capture(); self.output.write_bytes(b'old')
        original = (self.schedule.read_bytes(), self.profiles.read_bytes())
        with patch.object(ex, 'render_dashboard_html', side_effect=ValueError('invalid report')), self.assertRaises(ValueError):
            ex.export_dashboard_html(snap, self.output, overwrite=True)
        self.assertEqual((self.schedule.read_bytes(), self.profiles.read_bytes()), original)
        self.assertEqual(self.output.read_bytes(), b'old')
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_write_flush_fsync_and_publication_failure_cleanup(self):
        snap = self.capture(); self.output.write_bytes(b'old')
        for target in ('os.fsync', 'os.replace', 'os.link'):
            with patch('fleet_dashboard_exports.' + target, side_effect=OSError('injected')), self.assertRaises(OSError):
                ex.export_dashboard_html(snap, self.output, overwrite=target != 'os.link')
            self.assertEqual(self.output.read_bytes(), b'old')
            self.assertFalse(list(self.root.glob('.*.tmp')))
        actual = ex.tempfile.NamedTemporaryFile
        for operation in ('write', 'flush'):
            def failing(*args, **kwargs):
                stream = actual(*args, **kwargs)
                wrapper = MagicMock(wraps=stream); wrapper.name = stream.name
                wrapper.__enter__.return_value = wrapper
                wrapper.__exit__.side_effect = stream.__exit__
                getattr(wrapper, operation).side_effect = OSError('injected')
                return wrapper
            with patch.object(ex.tempfile, 'NamedTemporaryFile', side_effect=failing), self.assertRaises(OSError):
                ex.export_dashboard_html(snap, self.output, overwrite=True)
            self.assertEqual(self.output.read_bytes(), b'old')
            self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_model_validation_cannot_be_bypassed_by_bad_capture_arguments(self):
        with self.assertRaises(ValueError):
            replace(self.capture(), current_mileage=True, selected_profile_id=PID).model
        with self.assertRaises(ValueError):
            ex.export_dashboard_csv({}, self.output)
        with self.assertRaises(ValueError):
            ex.export_dashboard_csv(self.capture(), self.output, overwrite=1)

    def test_appointment_rows_and_csv_quoting(self):
        model = build([task(title='Line, one\nLine "two"', scheduled_date=TODAY.isoformat())])
        rows = list(csv.DictReader(io.StringIO(ex.render_dashboard_csv(model).lstrip('\ufeff'))))
        appointment = next(r for r in rows if r['row_type'] == 'appointment')
        self.assertEqual(appointment['task_title'], 'Line, one\nLine "two"')
        self.assertEqual(appointment['appointment_bucket'], '0–7 days')
