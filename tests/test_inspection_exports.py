"""Exports use disposable files and never open browsers or windows."""
import csv
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from checklist import create_default_data
from inspection_history import create_inspection_record, create_default_findings
from inspection_exports import (render_inspection_html, render_history_csv,
                                export_inspection_html, export_history_csv)


def record():
    return create_inspection_record('Actual <unit> " &', '123', '=formula\nUnicode é',
                                   create_default_data(), findings=create_default_findings(),
                                   assessment='Ready for service')


class TestExports(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.record = record()

    def test_html_all_fields_escaping_snapshot_and_independence(self):
        r = self.record
        r['profile_id'] = '00000000-0000-4000-8000-000000000001'
        r['profile_snapshot'] = dict(vehicle_name='Old <name>', year=None, make='" &', model="'model'", mileage=2)
        r['items']['oil']['name'] = '<script>alert(1)</script>'
        r['findings']['oil']['comment'] = '<img src=x onerror="bad">\nsecond'
        before = deepcopy(r)
        result = render_inspection_html(r, schema_version=3)
        for text in ('&lt;script&gt;', '&lt;img', '&quot;', '&#x27;', '&amp;', '123', 'Old &lt;name&gt;', '0/7 (0%)', 'Ready for service', '=formula', 'Not specified'):
            self.assertIn(text, result)
        self.assertNotIn('<script>', result)
        self.assertNotIn('<img', result)
        self.assertNotIn('http', result)
        self.assertEqual(r, before)
        self.assertEqual(result.count('<tr>'), 8)

    def test_legacy_and_mixed_history(self):
        legacy = deepcopy(self.record)
        legacy.pop('findings'); legacy.pop('assessment')
        for schema in (1, 2, 3):
            output = render_inspection_html(legacy, schema_version=schema)
            self.assertIn('Not recorded (legacy inspection)', output)
            self.assertIn('Profile association/snapshot not recorded', output)
        history = {'schema_version': 3, 'inspections': [legacy, self.record, self.record]}
        before = deepcopy(history)
        rows = list(csv.DictReader(io.StringIO(render_history_csv(history).lstrip('\ufeff'))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['findings_recorded'], 'false')
        self.assertEqual(rows[0]['oil_comment'], 'Not recorded (legacy inspection)')
        self.assertEqual(rows[1]['history_record_number'], '2')
        self.assertEqual(rows[1]['profile_recorded'], 'false')
        self.assertEqual(history, before)

    def test_csv_quotes_multiline_and_formula_protection(self):
        for value in ('=SUM(1)', '+1', '-2', '@x', ' \t=1', '\r+2', '\x00=1', '\x1b@x', '\ufeff+2'):
            self.record['notes'] = value
            result = render_history_csv({'schema_version': 2, 'inspections': [self.record]})
            rows = list(csv.DictReader(io.StringIO(result.lstrip('\ufeff'))))
            self.assertEqual(rows[0]['notes'], "'" + value.replace("\x00", "\\u0000"))
        self.record['notes'] = 'é,"quoted"\nline'
        rows = list(csv.DictReader(io.StringIO(render_history_csv({'schema_version': 2, 'inspections': [self.record]}).lstrip('\ufeff'))))
        self.assertEqual(rows[0]['notes'], self.record['notes'])
        self.assertEqual(rows[0]['oil_complete'], 'false')
        self.assertEqual(rows[0]['percentage'], '0.0')

    def test_invalid_inputs_rejected_without_output(self):
        self.record['progress']['completed'] = 1
        with self.assertRaises(ValueError):
            export_inspection_html(self.record, self.directory / 'report.html', schema_version=2)
        with self.assertRaises(ValueError):
            render_history_csv({'schema_version': 2, 'inspections': [self.record]})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_output_atomic_overwrite_and_failure(self):
        path = self.directory / 'report.html'
        export_inspection_html(self.record, path, schema_version=2)
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            export_inspection_html(self.record, path, schema_version=2)
        self.record['notes'] = 'changed'
        with patch('data_backup.os.replace', side_effect=OSError('failure')):
            with self.assertRaises(OSError):
                export_inspection_html(self.record, path, schema_version=2, overwrite=True)
        self.assertEqual(path.read_bytes(), original)
        export_inspection_html(self.record, path, schema_version=2, overwrite=True)
        self.assertIn('changed', path.read_text())
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_missing_history_header_only_and_runtime_alias(self):
        history = self.directory / 'inspection_history.json'
        output = self.directory / 'history.csv'
        export_history_csv(history, output)
        self.assertFalse(history.exists())
        self.assertEqual(len(list(csv.reader(io.StringIO(output.read_text(encoding='utf-8-sig'))))), 1)
        with self.assertRaises(ValueError):
            export_history_csv(history, history)
        with self.assertRaises(ValueError):
            export_inspection_html(self.record, self.directory / 'vehicle_profiles.json', schema_version=2)
