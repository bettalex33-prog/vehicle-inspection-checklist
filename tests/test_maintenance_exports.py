"""Maintenance exports use disposable stores; no windows or browsers."""
from copy import deepcopy
import csv
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import maintenance_exports as ex
import maintenance_schedule as ms
import test_maintenance_schedule as fixtures
from test_maintenance_schedule import TODAY


class TestMaintenanceExports(unittest.TestCase):
    setUp = fixtures.TestMaintenance.setUp
    create = fixtures.TestMaintenance.create

    def schedule(self):
        task = self.create(title='<script>task</script>', notes='=SUM(1)')
        task['services'] = [{'service_id': '00000000-0000-4000-8000-000000000010',
                             'completed_date': '2026-10-21', 'mileage': 46000, 'notes': 'Legacy'}]
        self.path.write_text(json.dumps({'schema_version': 1, 'tasks': [task]}))
        values = ms.validate_service_details('2026-10-22', '48001', ' \t@formula', today=TODAY,
                  provider='<img src=x onerror="bad">', invoice_reference='+invoice', labor_cost='1.01', tax='0')
        task = ms.record_service(self.path, task['task_id'], values, profiles_path=self.profiles, expected_task=task, today=TODAY)
        ms.archive_task(self.path, task['task_id'], expected_task=task)
        self.create(title='Empty task')
        return ms.load_schedule(self.path)

    def test_csv_content_formulas_money_legacy_and_determinism(self):
        schedule = self.schedule(); before = deepcopy(schedule)
        rendered = ex.render_maintenance_csv(schedule, today=TODAY)
        rows = list(csv.DictReader(io.StringIO(rendered.lstrip('\ufeff'))))
        self.assertEqual(len(rows), 3)
        legacy = next(row for row in rows if row['service_notes'] == 'Legacy')
        new = next(row for row in rows if row['service_invoice_reference'] == "'+invoice")
        self.assertEqual(legacy['service_total_cost_cents'], 'Not recorded')
        self.assertEqual(legacy['service_provider'], 'Not recorded')
        self.assertEqual(new['service_total_cost_cents'], '$1.01')
        self.assertEqual(new['service_tax_cents'], '$0.00')
        self.assertEqual(new['service_parts_cost_cents'], 'Not recorded')
        self.assertEqual(new['service_notes'], "' \t@formula")
        self.assertEqual(new['notes'], "'=SUM(1)")
        self.assertEqual(new['due_status'], 'Archived')
        self.assertEqual(new['snapshot_vehicle_name'], 'Unit')
        self.assertEqual(new['due_next_due_mileage'], '53001')
        reversed_schedule = deepcopy(schedule); reversed_schedule['tasks'].reverse()
        self.assertEqual(rendered, ex.render_maintenance_csv(reversed_schedule, today=TODAY))
        self.assertEqual(schedule, before)
        for value in ('=1', '+1', '-1', '@1', '\x00=1', '\ufeff+1', '\r-1'):
            self.assertTrue(ex._safe_cell(value).startswith("'"))

    def test_html_escaping_full_report_and_consistent_totals(self):
        schedule = self.schedule()
        report = ex.render_maintenance_html(schedule, today=TODAY)
        for value in ('&lt;script&gt;', '&lt;img', '&quot;', 'Not recorded', '$1.01', '$0.00',
                      'Archived', '53001', 'unpriced_service_count', 'invoice_reference', 'snapshot_vehicle_name'):
            self.assertIn(value, report)
        self.assertNotIn('<script>', report)
        self.assertNotIn('<img', report)
        reversed_schedule = deepcopy(schedule); reversed_schedule['tasks'].reverse()
        self.assertEqual(report, ex.render_maintenance_html(reversed_schedule, today=TODAY))

    def test_exports_read_legacy_without_upgrade(self):
        task = self.create()
        self.path.write_text(json.dumps({'schema_version': 1, 'tasks': [task]}, indent=4))
        before = self.path.read_bytes()
        for export, extension in ((ex.export_maintenance_csv, 'csv'), (ex.export_maintenance_html, 'html')):
            output = self.root / ('report.' + extension)
            export(self.path, output, today=TODAY)
            self.assertEqual(self.path.read_bytes(), before)
            with self.assertRaises(FileExistsError):
                export(self.path, output, today=TODAY)
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_all_runtime_source_custom_and_link_aliases_preserved(self):
        self.schedule()
        for name in ('checklist_data.json', 'inspection_history.json', 'vehicle_profiles.json', 'maintenance_schedule.json'):
            path = self.root / name
            if not path.exists():
                path.write_bytes(b'protected')
            before = path.read_bytes()
            for exporter in (ex.export_maintenance_csv, ex.export_maintenance_html):
                with self.assertRaises(ValueError):
                    exporter(self.path, path, today=TODAY, overwrite=True)
            self.assertEqual(path.read_bytes(), before)
        custom_source = self.root / 'custom-source.json'
        custom_source.write_bytes(self.path.read_bytes())
        custom_runtime = self.root / 'custom-runtime.json'; custom_runtime.write_bytes(b'protected')
        symlink = self.root / 'symlink.html'; symlink.symlink_to(custom_source)
        hardlink = self.root / 'hardlink.csv'; os.link(custom_source, hardlink)
        for path in (custom_source, symlink, hardlink, custom_runtime):
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                ex.export_maintenance_csv(custom_source, path, today=TODAY, overwrite=True, runtime_paths=(custom_runtime,))
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_source_mileage_and_write_failures_preserve_destination(self):
        self.schedule()
        output = self.root / 'report.html'; output.write_bytes(b'old report')
        for target in ('os.fsync', 'os.replace'):
            with patch('maintenance_exports.' + target, side_effect=OSError('injected')), self.assertRaises(OSError):
                ex.export_maintenance_html(self.path, output, today=TODAY, overwrite=True)
            self.assertEqual(output.read_bytes(), b'old report')
            self.assertFalse(list(self.root.glob('.*.tmp')))
        for operation in ('write', 'flush'):
            actual = ex.tempfile.NamedTemporaryFile
            def temporary(*args, **kwargs):
                stream = actual(*args, **kwargs)
                wrapper = MagicMock(wraps=stream)
                wrapper.name = stream.name
                wrapper.__enter__.return_value = wrapper
                wrapper.__exit__.side_effect = stream.__exit__
                getattr(wrapper, operation).side_effect = OSError('injected')
                return wrapper
            with patch.object(ex.tempfile, 'NamedTemporaryFile', side_effect=temporary), self.assertRaises(OSError):
                ex.export_maintenance_html(self.path, output, today=TODAY, overwrite=True)
            self.assertEqual(output.read_bytes(), b'old report')
            self.assertFalse(list(self.root.glob('.*.tmp')))
        for raw in (b'broken', b'{"schema_version":2,"tasks":[],"tasks":[]}', b'{"schema_version":9,"tasks":[]}'):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                ex.export_maintenance_html(self.path, output, today=TODAY, overwrite=True)
            self.assertEqual(output.read_bytes(), b'old report')
            self.assertEqual(self.path.read_bytes(), raw)

    def test_atomic_same_directory_and_explicit_mileage(self):
        schedule = self.schedule()
        profile_id = schedule['tasks'][0]['profile_id']
        with self.assertRaises(ValueError):
            ex.render_maintenance_csv(schedule, today=TODAY, current_mileages={profile_id: True})
        with self.assertRaises(ValueError):
            ex.render_maintenance_csv(schedule, today=TODAY, current_mileages={profile_id: 1})
        output = self.root / 'report.csv'; output.write_text('old')
        actual = ex.os.replace
        def replace(source, destination):
            self.assertEqual(source.parent, output.parent)
            return actual(source, destination)
        with patch.object(ex.os, 'replace', side_effect=replace):
            ex.export_maintenance_csv(self.path, output, today=TODAY, overwrite=True,
                                      current_mileages={profile_id: 48001})
        self.assertIn('48001', output.read_text())
