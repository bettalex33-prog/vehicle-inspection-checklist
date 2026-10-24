"""Deterministic reports and adversarial publication tests in temporary stores."""
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import maintenance_insights_exports as ex
from maintenance_insights_sources import capture_insights
from test_maintenance_insights import AS_OF, PID, build, positive_task, service
from test_maintenance_insights_sources import stores


class TestInsightsExports(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = stores(self.root)
        self.output = self.root / 'report.csv'

    def capture(self, **kwargs):
        return capture_insights(*self.paths, as_of=AS_OF, **kwargs)

    def export(self, snapshot=None, **kwargs):
        return ex.export_insights_csv(snapshot or self.capture(), self.output, **kwargs)

    def test_fixed_schema_and_empty_header(self):
        expected = 'row_type,report_version,as_of,selected_profile_id,explicit_current_mileage,cost_start_date,cost_end_date,profile_id,vehicle_name,profile_missing,task_id,task_title,service_id,source_kind,source_record_ref,source_digest,code,severity,message,field_name,field_value,snapshot_vehicle_name,snapshot_year,snapshot_make,snapshot_model,snapshot_mileage,enabled,archived,status,date_status,mileage_status,count,observation_date,observation_mileage,observation_accepted,rate_start_date,rate_start_mileage,rate_end_date,rate_end_mileage,rate_delta_miles,rate_delta_days,rate_miles_per_day,forecast_axis,availability,unavailable_reason,anchor_date,anchor_mileage,next_due_date,next_due_mileage,remaining_days,remaining_miles,reference_date,reference_mileage,reference_kind,estimated_days,estimated_due_date,month,service_count,priced_service_count,unpriced_service_count,legacy_service_count,total_cost_cents,average_cost_cents,min_cost_cents,max_cost_cents,completed_date,service_mileage,provider,invoice_reference,labor_cost_cents,parts_cost_cents,tax_cents,service_total_cost_cents,service_legacy,notes'
        self.assertEqual(','.join(ex.COLUMNS), expected)
        self.assertEqual(ex.render_insights_csv(build()).split('\r\n')[0], '\ufeff' + expected)

    def test_row_type_order_and_report_version(self):
        rows = ex.insights_rows(self.capture().model)
        indices = [ex.ROW_TYPES.index(r['row_type']) for r in rows]
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(all(r['report_version'] == 1 for r in rows))

    def test_bom_crlf_quoting_and_unicode(self):
        t = positive_task(title='é, "rotation"\nnext')
        report = ex.render_insights_csv(build([t]))
        self.assertTrue(report.encode().startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(io.StringIO(report.lstrip('\ufeff'), newline='')))
        self.assertEqual(next(r['task_title'] for r in rows if r['row_type'] == 'task_forecast'), t['title'])
        self.assertTrue(report.endswith('\r\n'))

    def test_formula_protection_after_hidden_prefixes(self):
        for prefix in ('', ' ', '\t', '\r', '\x01', '\x7f', '\ufeff', '\u2003'):
            for operator in '=+-@':
                self.assertTrue(ex.safe_cell(prefix + operator + 'cmd').startswith("'"))
        self.assertEqual(ex.safe_cell('a\x00b'), 'a\\u0000b')

    def test_numeric_zero_negative_and_blank_remain_numeric(self):
        self.assertEqual(ex.safe_cell(-1), '-1')
        self.assertEqual(ex.safe_cell(0), '0')
        self.assertEqual(ex.safe_cell(None), '')
        self.assertEqual(ex.safe_cell(True), 'true')

    def test_html_escapes_every_dynamic_text(self):
        report = ex.render_insights_html(build([positive_task(title='<script>"&')]))
        self.assertNotIn('<script>', report)
        self.assertIn('&lt;script&gt;&quot;&amp;', report)
        self.assertNotIn('src=', report)

    def test_html_currency_not_recorded_and_zero(self):
        t = positive_task(services=[service(100, '2026-10-01', 10000, labor_cost='0.00')])
        report = ex.render_insights_html(build([t]))
        self.assertIn('$0.00', report)
        self.assertIn('Not recorded', report)

    def test_csv_html_equivalent_rows_and_history(self):
        model = self.capture().model
        csv_rows = list(csv.DictReader(io.StringIO(ex.render_insights_csv(model).lstrip('\ufeff'))))
        html = ex.render_insights_html(model)
        for row in csv_rows:
            self.assertIn('<h2>' + row['row_type'] + '</h2>', html)
        self.assertEqual(sum(r['row_type']=='service_history' for r in csv_rows), 2)

    def test_complete_history_ignores_cost_filters(self):
        model = self.capture(cost_start_date='2026-01-01', cost_end_date='2026-01-01').model
        rows = ex.insights_rows(model)
        self.assertEqual(model['costs']['service_count'], 0)
        self.assertEqual(sum(r['row_type']=='service_history' for r in rows), 2)
        self.assertEqual(len({r['service_id'] for r in rows if r['row_type']=='service_history'}), 2)

    def test_snapshots_observation_provenance_and_assumptions(self):
        rows = ex.insights_rows(self.capture().model)
        self.assertEqual(next(r for r in rows if r['row_type']=='profile_snapshot')['snapshot_mileage'], 43000)
        observations = [r for r in rows if r['row_type']=='observation']
        self.assertTrue(all(len(r['source_digest']) == 64 for r in observations))
        self.assertIn('inspection', {r['source_kind'] for r in observations})
        self.assertTrue(any(r['row_type']=='assumption' and r['code']=='freshness_days' for r in rows))

    def test_deterministic_bytes_and_no_absolute_paths(self):
        snapshot = self.capture()
        for renderer in (ex.render_insights_csv, ex.render_insights_html):
            a = renderer(snapshot.model); b = renderer(snapshot.model)
            self.assertEqual(a, b)
            self.assertNotIn(str(self.root), a)

    def test_source_bytes_unchanged_on_successful_csv_and_html(self):
        before = [p.read_bytes() for p in self.paths]
        self.export()
        ex.export_insights_html(self.capture(), self.root / 'report.html')
        self.assertEqual(before, [p.read_bytes() for p in self.paths])

    def test_existing_output_requires_confirmed_fingerprint(self):
        self.output.write_bytes(b'old')
        for kwargs in ({}, {'overwrite': True}):
            with self.assertRaises(ValueError): self.export(**kwargs)
        fingerprint = ex.output_fingerprint(self.output)
        self.export(overwrite=True, expected_output=fingerprint)
        self.assertTrue(self.output.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_changed_confirmation_preserves_newer_destination(self):
        self.output.write_bytes(b'old'); previous = ex.output_fingerprint(self.output)
        self.output.write_bytes(b'new')
        with self.assertRaises(ValueError): self.export(overwrite=True, expected_output=previous)
        self.assertEqual(self.output.read_bytes(), b'new')

    def test_runtime_and_custom_source_aliases_rejected(self):
        for path in (*self.paths, *[self.root / n for n in ('checklist_data.json', 'inspection_history.json', 'vehicle_profiles.json', 'maintenance_schedule.json')]):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                ex.export_insights_csv(self.capture(), path)
        custom = self.root / 'custom-checklist'; custom.write_bytes(b'protected')
        with self.assertRaises(ValueError): ex.export_insights_csv(self.capture(), custom, runtime_paths=(custom,))
        self.assertEqual(custom.read_bytes(), b'protected')

    def test_resolved_parent_alias_of_source_rejected(self):
        alias = self.root / 'parent-alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): ex.export_insights_csv(self.capture(), alias / self.paths[0].name)

    def test_symlink_hardlink_and_special_destinations_rejected(self):
        original = self.root / 'original'; original.write_bytes(b'old')
        for kind in ('symlink', 'hardlink', 'directory', 'fifo'):
            if kind=='symlink': self.output.symlink_to(original)
            elif kind=='hardlink': os.link(original, self.output)
            elif kind=='directory': self.output.mkdir()
            else: os.mkfifo(self.output)
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.export()
            if kind=='directory': self.output.rmdir()
            else: self.output.unlink()

    def test_stale_sources_before_export_refused(self):
        snapshot = self.capture(); self.paths[0].write_bytes(self.paths[0].read_bytes()+b'\n')
        with self.assertRaises(ValueError): self.export(snapshot)
        self.assertFalse(self.output.exists())

    def test_source_change_immediately_before_publication_cleans_up(self):
        snapshot = self.capture(); original = ex.os.fsync
        def change(fd):
            original(fd); self.paths[1].write_bytes(self.paths[1].read_bytes()+b'\n')
        with patch.object(ex.os, 'fsync', side_effect=change), self.assertRaises(ValueError): self.export(snapshot)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_atomic_write_flush_and_fsync_failures_preserve_output(self):
        self.output.write_bytes(b'old')
        for operation in ('write', 'flush', 'fsync'):
            with self.subTest(operation=operation):
                fingerprint = ex.output_fingerprint(self.output)
                if operation=='fsync':
                    manager = patch.object(ex.os, 'fsync', side_effect=OSError('blocked'))
                else:
                    actual = ex.tempfile.NamedTemporaryFile
                    def wrap(*args, **kwargs):
                        stream = actual(*args, **kwargs)
                        setattr(stream, operation, lambda *a, **k: (_ for _ in ()).throw(OSError('blocked')))
                        return stream
                    manager = patch.object(ex.tempfile, 'NamedTemporaryFile', side_effect=wrap)
                with manager, self.assertRaises(OSError): self.export(overwrite=True, expected_output=fingerprint)
                self.assertEqual(self.output.read_bytes(), b'old')
                self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_link_replace_and_render_failure_cleanup(self):
        for operation in ('link', 'replace', 'render_insights_csv'):
            with self.subTest(operation=operation):
                overwrite = operation=='replace'
                if overwrite: self.output.write_bytes(b'old')
                fingerprint = ex.output_fingerprint(self.output)
                target = ex if operation.startswith('render') else ex.os
                with patch.object(target, operation, side_effect=OSError('blocked')), self.assertRaises(OSError):
                    self.export(overwrite=overwrite, expected_output=fingerprint)
                self.assertFalse(list(self.root.glob('.*.tmp')))
                if overwrite:
                    self.assertEqual(self.output.read_bytes(), b'old'); self.output.unlink()
                else: self.assertFalse(self.output.exists())

    def test_parent_change_destination_race_and_invalid_mileage_refused(self):
        original = ex.parent_identity
        calls = [0]
        def parent(path):
            result = original(path); calls[0] += 1
            return result if calls[0]==1 else (result[0], result[1], result[2]+1)
        with patch.object(ex, 'parent_identity', side_effect=parent), self.assertRaises(ValueError): self.export()
        self.assertFalse(list(self.root.glob('.*.tmp')))
        def race(fd): self.output.write_bytes(b'new writer')
        with patch.object(ex.os, 'fsync', side_effect=race), self.assertRaises(ValueError): self.export()
        self.assertEqual(self.output.read_bytes(), b'new writer')
        self.output.unlink()
        with self.assertRaises(ValueError): self.export(self.capture(selected_profile_id=PID, explicit_current_mileage=1))
        self.assertFalse(self.output.exists())
