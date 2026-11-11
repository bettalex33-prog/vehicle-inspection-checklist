"""Projection contracts exercised with real, in-memory domain models."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from checklist import create_default_data
from dashboard_model import build_dashboard_model, build_backup_panel
from fleet_dashboard import STATUSES
from test_fleet_dashboard import PID, OTHER, profile, task, service
from test_maintenance_insights import AS_OF, build, positive_task


def backup_report():
    """Owned synthetic engine facts, including private evidence to exclude."""
    names = ('checklist_data.json', 'inspection_history.json',
             'maintenance_schedule.json', 'vehicle_profiles.json')
    return dict(availability='Available', coverage='complete', coverage_reason='all_stores_match',
        unavailable_reason=None, healthy=True, archive=dict(path='/private/secret/archive.zip',
            presence='Present', zip_valid=True, members_safe=True, manifest_valid=True,
            compatible=True, checksum_valid=True, payload_valid=True, release='1.9.0',
            created_at='2026-10-22T12:34:56.123456789Z', validation_reason='/private/secret/error'),
        stores=[dict(name=name, path='/private/secret/' + name, archived=True,
            archive_checksum_valid=True, current_presence='Present', current_readability='Readable',
            current_reason=None, exact_byte_match=True, match_reason='exact_bytes_match') for name in names])


class TestBackupPanel(unittest.TestCase):
    def test_session_state_matrix_drops_old_health_and_private_evidence(self):
        for status in ('unavailable', 'no_selection', 'required', 'loading', 'stale', 'error'):
            panel = build_backup_panel(backup_report(), status=status)
            self.assertEqual(panel['classification'], status)
            self.assertFalse(panel['healthy'])
            self.assertEqual(panel['stores'], [])
            self.assertNotIn('/private', str(panel))

    def test_assessment_classifications_and_combined_partial_different(self):
        for expected in ('complete', 'missing', 'invalid', 'unsupported', 'partial', 'changed', 'unknown'):
            with self.subTest(expected=expected):
                report = backup_report()
                if expected != 'complete': report['healthy'] = False
                if expected == 'missing':
                    report.update(availability='Unavailable', coverage='unknown')
                    report['archive']['presence'] = 'Missing'
                elif expected in ('invalid', 'unsupported'):
                    report.update(availability='Invalid', coverage='invalid')
                    report['archive']['payload_valid'] = False
                    if expected == 'unsupported':
                        report['archive'].update(compatible=False,
                            compatibility_reason='unsupported_schema_application_or_release')
                elif expected == 'partial':
                    report['coverage'] = 'partial'
                    report['stores'][0].update(archived=False, exact_byte_match=None, match_reason='not_archived')
                elif expected == 'changed':
                    report['coverage'] = 'changed'
                    report['stores'][0].update(archived=False, exact_byte_match=None)
                    report['stores'][1].update(exact_byte_match=False, match_reason='different_bytes')
                elif expected == 'unknown':
                    report.update(availability='Unavailable', coverage='unknown')
                    report['stores'][0].update(current_readability='Unreadable', exact_byte_match=None,
                                              current_reason='os_error_13')
                panel = build_backup_panel(report, status='assessed')
                self.assertEqual(panel['classification'], expected)
                self.assertEqual(panel['healthy'], expected == 'complete')
                if expected == 'changed':
                    self.assertTrue(panel['partial']); self.assertTrue(panel['differs'])
                if expected == 'unknown': self.assertEqual(panel['stores'][0]['current_reason'], 'os_error_13')

    def test_projection_is_allowlisted_sorted_and_independently_owned(self):
        report = backup_report()
        report['stores'].reverse()
        report['archive']['read_reason'] = '/private/secret/diagnostic'
        report['archive']['created_at'] = '/private/secret/timestamp'
        report['archive']['release'] = '/private/secret/release'
        report['stores'][0]['match_reason'] = '/private/secret/match'
        before = deepcopy(report)
        first = build_backup_panel(report, status='assessed')
        self.assertNotIn('/private', str(first))
        self.assertEqual(report, before)
        self.assertEqual([r['name'] for r in first['stores']], sorted(r['name'] for r in report['stores']))
        second = build_backup_panel(report, status='assessed')
        self.assertEqual(first, second)
        first['stores'][0]['archived'] = False
        self.assertTrue(second['stores'][0]['archived'])
        self.assertEqual(report, before)

    def test_health_requires_every_validation_and_exact_current_store(self):
        for key in ('zip_valid', 'members_safe', 'manifest_valid', 'compatible', 'checksum_valid', 'payload_valid'):
            report = backup_report(); report['archive'][key] = False
            self.assertFalse(build_backup_panel(report, status='assessed')['healthy'])
        for field, value in (('archived', False), ('exact_byte_match', None),
                             ('current_presence', 'Missing'), ('current_readability', 'Unreadable')):
            report = backup_report(); report['stores'][0][field] = value
            self.assertFalse(build_backup_panel(report, status='assessed')['healthy'])


class TestDashboardModel(unittest.TestCase):
    def project(self, model=None, checklist=None, scope=None):
        return build_dashboard_model(build() if model is None else model, checklist, scope)

    def test_empty_data_and_backup_unavailable(self):
        result = self.project(build(profiles=[]), create_default_data())
        self.assertEqual(result['maintenance']['status_totals'], dict.fromkeys(STATUSES, 0))
        self.assertEqual(result['recent_services'], [])
        self.assertEqual(result['service_history'], [])
        self.assertIsNone(result['service_cost_summary']['total_cost_cents'])
        self.assertEqual(result['shared_inspection_progress']['completed'], 0)
        self.assertEqual(result['backup_health'], build_backup_panel())

    def test_missing_checklist_is_not_zero(self):
        progress = self.project()['shared_inspection_progress']
        self.assertEqual(progress['availability'], 'Unavailable')
        for field in ('completed', 'total', 'percentage'):
            self.assertIsNone(progress[field])

    def test_all_maintenance_statuses_preserved(self):
        tasks = [task(10+i, baseline_date=(AS_OF-timedelta(days=100-offset)).isoformat())
                 for i, offset in enumerate((-1, 0, 10, 11))]
        tasks += [task(14, interval_days=None, warning_days=None,
                       interval_miles=1000, warning_miles=100),
                  task(15, enabled=False), task(16, enabled=False, archived=True)]
        model = build(tasks)
        result = self.project(model, scope=PID)
        self.assertEqual(result['maintenance']['status_totals'], dict.fromkeys(STATUSES, 1))
        self.assertEqual(result['maintenance']['tasks'], model['tasks'])

    def test_invalid_mileage_and_unavailable_forecasts_retained(self):
        for mileage in (-1, 'bad', None):
            with self.subTest(mileage=mileage):
                model = build([task(interval_miles=1000, warning_miles=100)],
                              selected_profile_id=PID, explicit_current_mileage=mileage)
                result = self.project(model)
                self.assertEqual(result['evaluation']['input_error'], model['input_error'])
                self.assertEqual(result['mileage_forecasts']['tasks'], model['tasks'])
                axis = result['mileage_forecasts']['tasks'][0]['axes'][1]
                self.assertEqual(axis['availability'], 'Unavailable')
                self.assertIsNone(axis['estimated_due_date'])

    def test_duplicate_names_filter_by_id_without_reevaluation(self):
        model = build([task(), task(11, pid=OTHER)],
                      profiles=[profile(), profile(OTHER)])
        for pid in (PID, OTHER):
            result = self.project(model, scope=pid)
            self.assertEqual([r['profile_id'] for r in result['maintenance']['tasks']], [pid])
            self.assertEqual(result['service_cost_summary']['service_count'], 0)
            self.assertIsNone(result['evaluation']['selected_profile_id'])
            self.assertEqual(set(result['mileage_forecasts']['rates']), {pid})

    def test_orphaned_profile_keeps_history_and_quality(self):
        model = build([positive_task()], profiles=[])
        result = self.project(model, scope=PID)
        self.assertTrue(result['vehicles'][0]['profile_missing'])
        self.assertEqual(result['service_history'], model['service_history'])
        self.assertEqual(len(result['recent_services']), 2)
        self.assertIn('missing_live_profile',
                      [w['code'] for w in result['data_quality']['warnings']])

    def test_cross_profile_nonzero_costs_are_isolated_by_stable_id(self):
        model = build([
            task(10, services=[service(100, '2026-10-01', labor_cost='12.34')]),
            task(11, pid=OTHER, services=[
                service(101, '2026-10-02', labor_cost='20.00'),
                service(102, '2026-10-03', labor_cost='5.67')])],
            profiles=[profile(name='Same name'), profile(OTHER, name='Same name')])
        for scope, cents, count in ((PID, 1234, 1), (OTHER, 2567, 2), (None, 3801, 3)):
            with self.subTest(scope=scope):
                result = self.project(model, scope=scope)
                costs = result['service_cost_summary']
                self.assertEqual(costs['total_cost_cents'], cents)
                self.assertEqual(costs['service_count'], count)
                self.assertEqual(costs['priced_service_count'], count)
                self.assertEqual(costs['unpriced_service_count'], 0)
                self.assertEqual(costs['legacy_service_count'], 0)
                expected_ids = {PID, OTHER} if scope is None else {scope}
                for rows in (result['service_history'], result['recent_services']):
                    self.assertEqual({row['profile_id'] for row in rows}, expected_ids)
                    self.assertEqual(len(rows), count)

    def test_available_forecasts_preserved_across_display_scopes(self):
        model = build([
            positive_task(),
            positive_task(task_id=task(11)['task_id'], profile_id=OTHER,
                          profile_snapshot=task(11, pid=OTHER)['profile_snapshot'],
                          services=[service(102, '2026-10-01', 20000),
                                    service(103, '2026-10-11', 20100)])],
            profiles=[profile(mileage=90000), profile(OTHER, mileage=80000)],
            selected_profile_id=PID, explicit_current_mileage=10400)
        before = deepcopy(model)
        expected = {row['profile_id']: deepcopy(row) for row in model['tasks']}
        axis = expected[PID]['axes'][1]
        self.assertIsNone(model['input_error'])
        self.assertEqual(axis['availability'], 'Available')
        self.assertEqual(axis['reference_kind'], 'Explicit session mileage')
        self.assertEqual(axis['reference_mileage'], 10400)
        self.assertEqual(axis['reference_date'], AS_OF.isoformat())
        self.assertEqual(axis['estimated_due_date'], '2026-11-11')
        self.assertEqual(axis['estimated_days'], 21)
        self.assertNotEqual(model['rates'][PID], model['rates'][OTHER])
        # Projection must consume the captured facts even if reevaluation and
        # wall-clock access would now fail. The fixed evaluation date is retained.
        with patch('maintenance_insights.forecast_task', side_effect=AssertionError('reevaluated')), \
             patch('maintenance_insights.build_insights', side_effect=AssertionError('rebuilt')), \
             patch('dashboard_model.date', create=True) as clock:
            clock.today.side_effect = AssertionError('hidden clock')
            for scope in (PID, OTHER, None, PID):
                result = self.project(model, scope=scope)
                self.assertEqual(result['as_of'], AS_OF.isoformat())
                self.assertEqual(result['evaluation']['selected_profile_id'], PID)
                self.assertEqual(result['evaluation']['explicit_current_mileage'], 10400)
                rows = result['mileage_forecasts']['tasks']
                self.assertEqual({row['profile_id'] for row in rows},
                                 {PID, OTHER} if scope is None else {scope})
                for row in rows:
                    self.assertEqual(row, expected[row['profile_id']])
                self.assertEqual(result['mileage_forecasts']['rates'], {
                    pid: rate for pid, rate in model['rates'].items()
                    if scope is None or pid == scope})
            clock.today.assert_not_called()
        self.assertEqual(model, before)

    def test_zero_unpriced_and_legacy_costs_remain_distinct(self):
        zero = service(100, '2026-10-01', 10000, labor_cost='0')
        unpriced = service(101, '2026-10-02', 10100)
        legacy = {k: v for k, v in service(102, '2026-10-03', 10200).items()
                  if k in ('service_id', 'completed_date', 'mileage', 'notes')}
        model = build([task(services=[zero, unpriced, legacy])])
        for scope in (None, PID):
            result = self.project(model, scope=scope)
            costs = result['service_cost_summary']
            self.assertEqual(costs['total_cost_cents'], 0)
            self.assertEqual(costs['priced_service_count'], 1)
            self.assertEqual(costs['unpriced_service_count'], 2)
            self.assertEqual(costs['legacy_service_count'], 1)
            records = [r['service'] for r in result['service_history']]
            self.assertEqual(records[0]['total_cost_cents'], 0)
            self.assertIsNone(records[1]['total_cost_cents'])
            self.assertNotIn('total_cost_cents', records[2])

    def test_recent_order_is_newest_first_with_stable_ties(self):
        model = build([task(11, pid=OTHER, services=[service(102, '2026-10-11')]),
                       task(10, services=[service(101, '2026-10-10'),
                                          service(103, '2026-10-11')])],
                      profiles=[profile(), profile(OTHER)])
        expected = [103, 102, 101]
        first = self.project(model)['recent_services']
        model['recent_services'].reverse()
        second = self.project(model)['recent_services']
        self.assertEqual(first, second)
        self.assertEqual([int(r['service']['service_id'].replace('-', ''), 16)
                          for r in first], expected)

    def test_cost_bounds_do_not_filter_status_or_complete_history(self):
        tasks = [positive_task()]
        plain = self.project(build(tasks))
        bounded = self.project(build(tasks, cost_start_date='2026-10-12'))
        self.assertEqual(plain['maintenance'], bounded['maintenance'])
        self.assertEqual(plain['service_history'], bounded['service_history'])
        self.assertEqual(plain['recent_services'], bounded['recent_services'])
        self.assertEqual(bounded['service_cost_summary']['service_count'], 0)
        self.assertIsNone(bounded['service_cost_summary']['total_cost_cents'])

    def test_inputs_and_nested_outputs_are_independent(self):
        model, checklist = build([positive_task()]), create_default_data()
        before = deepcopy((model, checklist))
        result = self.project(model, checklist)
        self.assertEqual((model, checklist), before)
        result['maintenance']['tasks'][0]['task']['services'][0]['notes'] = 'changed'
        result['vehicles'][0]['profile']['vehicle_name'] = 'changed'
        result['data_quality']['assumptions'][0]['message'] = 'changed'
        self.assertEqual((model, checklist), before)
        fresh = self.project(model, checklist)
        model['service_history'][0]['service']['notes'] = 'later change'
        self.assertNotEqual(fresh['service_history'][0]['service']['notes'], 'later change')
        self.assertNotEqual(fresh['vehicles'][0]['profile']['vehicle_name'], 'changed')

    def test_scope_preserves_shared_progress_and_global_warnings(self):
        checklist = create_default_data()
        for item in list(checklist.values())[:5]:
            item['complete'] = True
        model = build([task(), task(11, pid=OTHER)], profiles=[profile(), profile(OTHER)])
        model['warnings'].append(dict(code='source_missing', source_kind='history'))
        results = [self.project(model, checklist, scope) for scope in (None, PID, OTHER)]
        for result in results:
            self.assertEqual(result['shared_inspection_progress'],
                             results[0]['shared_inspection_progress'])
            self.assertEqual(result['shared_inspection_progress']['completed'], 5)
            warnings = result['data_quality']['warnings']
            self.assertIn('source_missing', [w['code'] for w in warnings])
            if result['scope_profile_id']:
                self.assertTrue(all(w.get('profile_id') in (None, result['scope_profile_id'])
                                    for w in warnings))

    def test_unknown_display_scope_rejected(self):
        for scope in ('not-an-id', True, OTHER):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.project(scope=scope)
