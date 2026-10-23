"""Disposable-file maintenance validation, due thresholds, and failure tests."""
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import maintenance_schedule as ms
from vehicle_profiles import create_profile, update_profile, delete_profile

TODAY = date(2026, 10, 22)


def details(**changes):
    result = dict(title='Oil', notes='', enabled=True, interval_days=180, interval_miles=5000,
                  warning_days=30, warning_miles=500, baseline_date='2026-04-25',
                  baseline_mileage=42000, scheduled_date=None)
    return {**result, **changes}


class TestMaintenance(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'maintenance_schedule.json'
        self.profiles = self.root / 'vehicle_profiles.json'
        self.profile = create_profile(self.profiles, 'Unit', '', '', '', '42000')

    def create(self, **changes):
        return ms.create_task(self.path, self.profile['profile_id'], details(**changes),
                              profiles_path=self.profiles, today=TODAY)

    def service(self, task, **changes):
        values = dict(completed_date='2026-10-22', mileage=46500, notes='done')
        return ms.record_service(self.path, task['task_id'], {**values, **changes},
            profiles_path=self.profiles, expected_task=task, today=TODAY)

    def test_missing_read_does_not_create_and_copies_are_independent(self):
        self.assertEqual(ms.load_schedule(self.path), {'schema_version': 1, 'tasks': []})
        self.assertFalse(self.path.exists())
        task = self.create()
        task['profile_snapshot']['vehicle_name'] = 'Changed'
        task['services'].append({})
        loaded = ms.load_schedule(self.path)
        self.assertEqual(loaded['tasks'][0]['profile_snapshot']['vehicle_name'], 'Unit')
        self.assertEqual(loaded['tasks'][0]['services'], [])

    def test_snapshot_survives_profile_edit_deletion_and_orphan_service_rejected(self):
        task = self.create()
        update_profile(self.profiles, self.profile['profile_id'], 'Renamed', '', '', '', '49000')
        self.assertEqual(ms.load_schedule(self.path)['tasks'][0], task)
        delete_profile(self.profiles, self.profile['profile_id'])
        self.assertEqual(ms.load_schedule(self.path)['tasks'][0], task)
        with self.assertRaises(ValueError):
            self.service(task)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(ms.load_schedule(self.path)['tasks'][0], task)

    def test_service_resets_both_thresholds_clears_appointment_and_cannot_be_deleted(self):
        task = self.create(scheduled_date='2026-10-23')
        updated = self.service(task)
        self.assertIsNone(updated['scheduled_date'])
        due = ms.calculate_due(updated, today=TODAY, current_mileage=46500)
        self.assertEqual(due['remaining_days'], 180)
        self.assertEqual(due['next_due_mileage'], 51500)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            ms.delete_task(self.path, task['task_id'], expected_task=updated)
        self.assertEqual(self.path.read_bytes(), before)
        archived = ms.archive_task(self.path, task['task_id'], expected_task=updated)
        self.assertTrue(archived['archived']); self.assertFalse(archived['enabled'])
        self.assertEqual(archived['services'], updated['services'])
        self.assertEqual(archived['profile_snapshot'], task['profile_snapshot'])
        self.assertEqual(ms.calculate_due(archived, today=TODAY, current_mileage=None)['status'], 'Archived')
        with self.assertRaises(ValueError):
            self.service(archived)
        with self.assertRaises(ValueError):
            ms.update_task(self.path, task['task_id'], details(), expected_task=archived, today=TODAY)

    def test_delete_empty_task_preserves_other_tasks(self):
        first = self.create(); second = self.create(title='Brakes')
        ms.delete_task(self.path, first['task_id'], expected_task=first)
        self.assertEqual(ms.load_schedule(self.path)['tasks'], [second])

    def test_stale_task_refused_but_unrelated_updates_retained(self):
        first = self.create(); second = self.create(title='Brakes')
        updated = ms.update_task(self.path, first['task_id'], details(title='New'), expected_task=first, today=TODAY)
        with self.assertRaises(ValueError):
            ms.delete_task(self.path, first['task_id'], expected_task=first)
        ms.delete_task(self.path, second['task_id'], expected_task=second)
        self.assertEqual(ms.load_schedule(self.path)['tasks'], [updated])

    def test_service_order_baseline_and_disabled_validation(self):
        task = self.create()
        for change in ({'completed_date': '2026-04-24'}, {'mileage': 41999}, {'completed_date': '2026-10-23'}):
            with self.assertRaises(ValueError):
                self.service(task, **change)
        task = self.service(task)
        with self.assertRaises(ValueError):
            ms.update_task(self.path, task['task_id'], details(baseline_mileage=0), expected_task=task, today=TODAY)
        # Equal service date and mileage are allowed.
        task = self.service(task)
        self.assertEqual(len(task['services']), 2)
        task = ms.update_task(self.path, task['task_id'], details(enabled=False), expected_task=task, today=TODAY)
        with self.assertRaises(ValueError):
            self.service(task)

    def test_due_boundaries_and_unknown_mileage(self):
        task = self.create()
        for mileage, expected in ((47001, 'Overdue'), (47000, 'Due'), (46500, 'Due'), (46499, 'Due')):
            # Baseline + 180 days equals TODAY; date wins over mileage warning.
            self.assertEqual(ms.calculate_due(task, today=TODAY, current_mileage=mileage)['status'], expected)
        self.assertEqual(ms.calculate_due(task, today=TODAY, current_mileage=None)['status'], 'Due')
        self.assertTrue(ms.calculate_due(task, today=TODAY, current_mileage=None)['mileage_unknown'])
        with self.assertRaises(ValueError):
            ms.calculate_due(task, today=TODAY, current_mileage=41999)
        for days_before, expected in ((31, 'Upcoming'), (30, 'Due soon'), (0, 'Due'), (-1, 'Overdue')):
            from datetime import timedelta
            due = ms.calculate_due(task, today=TODAY - timedelta(days=days_before), current_mileage=42000)
            self.assertEqual(due['status'], expected)

    def test_single_axis_null_baselines_and_calendar(self):
        mileage = self.create(interval_days=None, warning_days=None, baseline_date=None)
        self.assertEqual(ms.calculate_due(mileage, today=TODAY, current_mileage=None)['status'], 'Mileage needed')
        for current, status in ((42000, 'Upcoming'), (46500, 'Due soon'), (47000, 'Due'), (47001, 'Overdue')):
            self.assertEqual(ms.calculate_due(mileage, today=TODAY, current_mileage=current)['status'], status)
        task = self.create(interval_miles=None, warning_miles=None, baseline_mileage=None,
                           baseline_date='2024-02-28', interval_days=1, warning_days=0)
        self.assertEqual(ms.calculate_due(task, today=TODAY, current_mileage=None)['next_due_date'], '2024-02-29')
        with self.assertRaises(ValueError):
            self.create(baseline_date=None)
        with self.assertRaises(ValueError):
            self.create(baseline_mileage=None)

    def test_strict_task_and_envelope_validation(self):
        task = self.create()
        invalid = [('enabled', 1), ('archived', 0), ('interval_days', True), ('interval_days', 0),
            ('warning_miles', -1), ('warning_days', 181), ('baseline_date', '2026-2-01'),
            ('baseline_date', '2026-02-30'), ('baseline_mileage', True), ('title', ' '),
            ('title', 'x' * 201), ('notes', '\x00'), ('notes', 'x' * 10001),
            ('scheduled_date', 'bad'), ('task_id', 'bad'), ('profile_snapshot', {}),
            ('interval_days', 10**20), ('services', {}), ('unexpected', 1)]
        for key, value in invalid:
            with self.subTest(key=key, value=str(value)[:30]), self.assertRaises(ValueError):
                ms.validate_task({**task, key: value})
        for data in ({'schema_version': True, 'tasks': []}, {'schema_version': 2, 'tasks': []},
                     {'schema_version': 1, 'tasks': [task, task]}, {'schema_version': 1, 'tasks': [], 'extra': 1}):
            with self.assertRaises(ValueError):
                ms.validate_schedule(data)
        task = self.service(task)
        duplicate = deepcopy(task); duplicate['task_id'] = '00000000-0000-4000-8000-000000000001'
        with self.assertRaises(ValueError):
            ms.validate_schedule({'schema_version': 1, 'tasks': [task, duplicate]})

    def test_corruption_never_overwritten(self):
        for raw in (b'broken', b'\xff', b'{"schema_version":1,"schema_version":1,"tasks":[]}',
                    b'{"schema_version":1,"tasks":[],"x":NaN}'):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.create()
            self.assertEqual(self.path.read_bytes(), raw)

    def test_appointments_and_form_validation(self):
        with self.assertRaises(ValueError):
            self.create(scheduled_date='2026-10-21')
        task = self.create(scheduled_date='2026-10-22')
        self.assertTrue(ms.calculate_due(task, today=date(2026, 10, 23), current_mileage=None)['appointment_passed'])
        # Existing appointments may remain in the past on editing.
        ms.update_task(self.path, task['task_id'], details(scheduled_date='2026-10-22'), expected_task=task, today=date(2026, 10, 23))
        form = {k: str(v) if type(v) is int else v for k, v in details().items()}
        self.assertEqual(ms.validate_task_details(today=TODAY, **form), details())
        for value in ('-1', '1.2', '١', True):
            with self.assertRaises(ValueError):
                ms.validate_task_details(today=TODAY, **{**form, 'interval_days': value})
        self.assertEqual(ms.validate_service_details('2026-10-22', ' 42 ', 'é', today=TODAY)['mileage'], 42)
        with self.assertRaises(ValueError):
            ms.validate_service_details('2026-10-23', '42', '', today=TODAY)

    def test_atomic_failures_and_same_directory_cleanup(self):
        task = self.create(); before = self.path.read_bytes()
        for target in ('os.fsync', 'os.replace', 'json.dumps'):
            with patch('maintenance_schedule.' + target, side_effect=OSError('injected')), self.assertRaises(OSError):
                ms.update_task(self.path, task['task_id'], details(title='Changed'), expected_task=task, today=TODAY)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(list(self.root.glob('.*.tmp')))
        actual = ms.os.replace
        def replace(source, destination):
            self.assertEqual(Path(source).parent, self.path.parent)
            return actual(source, destination)
        with patch.object(ms.os, 'replace', side_effect=replace):
            ms.update_task(self.path, task['task_id'], details(title='é'), expected_task=task, today=TODAY)

    def test_staging_write_flush_failure_and_first_write_failure(self):
        for operation in ('write', 'flush'):
            actual = ms.tempfile.NamedTemporaryFile
            def temporary(*args, **kwargs):
                from unittest.mock import MagicMock
                stream = actual(*args, **kwargs)
                wrapper = MagicMock(wraps=stream)
                wrapper.__enter__.return_value = wrapper
                wrapper.__exit__.side_effect = stream.__exit__
                getattr(wrapper, operation).side_effect = OSError('injected')
                wrapper.name = stream.name
                return wrapper
            with patch.object(ms.tempfile, 'NamedTemporaryFile', side_effect=temporary), self.assertRaises(OSError):
                self.create()
            self.assertFalse(self.path.exists())
            self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_intervening_changes_and_aliases(self):
        task = self.create(); original = self.path.read_bytes()
        actual = ms.os.fsync
        def changed(descriptor):
            actual(descriptor)
            self.path.write_bytes(b'concurrent')
        with patch.object(ms.os, 'fsync', side_effect=changed), self.assertRaises(ValueError):
            ms.delete_task(self.path, task['task_id'], expected_task=task)
        self.assertEqual(self.path.read_bytes(), b'concurrent')
        self.assertFalse(list(self.root.glob('.*.tmp')))
        self.path.unlink(); self.path.symlink_to(self.profiles)
        with self.assertRaises(ValueError):
            ms.load_schedule(self.path)
        self.path.unlink(); os.link(self.profiles, self.path)
        with self.assertRaises(ValueError):
            ms.load_schedule(self.path)
        self.path.unlink()
        with self.assertRaises(ValueError):
            ms._write_schedule_atomic(self.profiles, {'schema_version': 1, 'tasks': []}, expected_bytes=self.profiles.read_bytes())
