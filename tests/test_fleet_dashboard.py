"""Pure dashboard tests: no filesystem access or Tk dependencies."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import unittest
from uuid import UUID

from fleet_dashboard import build_dashboard, STATUSES, BUCKETS
from maintenance_schedule import validate_service_details

TODAY = date(2026, 10, 23)
PID = str(UUID(int=1))
OTHER = str(UUID(int=2))


def profile(pid=PID, name='Unit', mileage=43000):
    return {'profile_id': pid, 'vehicle_name': name, 'year': None, 'make': '', 'model': '', 'mileage': mileage}


def task(number=10, pid=PID, **changes):
    captured = profile(pid)
    captured.pop('profile_id')
    result = dict(task_id=str(UUID(int=number)), profile_id=pid, profile_snapshot=captured,
                  title='Rotation', notes='', enabled=True, archived=False, services=[],
                  interval_days=100, warning_days=10, interval_miles=None, warning_miles=None,
                  baseline_date='2026-01-01', baseline_mileage=0, scheduled_date=None)
    result.update(changes)
    return result


def service(number=100, completed_date='2026-10-23', mileage=48001, **changes):
    return {'service_id': str(UUID(int=number)), **validate_service_details(
        completed_date, str(mileage), 'service', today=TODAY, **changes)}


def build(tasks=(), profiles=None, **options):
    return build_dashboard({'schema_version': 2, 'tasks': list(tasks)},
        {'schema_version': 1, 'profiles': [profile()] if profiles is None else profiles}, today=TODAY, **options)


class TestFleetDashboard(unittest.TestCase):
    def test_seven_statuses_exclusive_and_vehicle_counts(self):
        tasks = [task(10 + i, baseline_date=(TODAY - timedelta(days=100 - offset)).isoformat())
                 for i, offset in enumerate((-1, 0, 10, 11))]
        tasks += [task(14, interval_days=None, warning_days=None, interval_miles=1000, warning_miles=100),
                  task(15, enabled=False), task(16, enabled=False, archived=True)]
        model = build(tasks, profiles=[profile(), profile(OTHER)])
        self.assertEqual(model['status_totals'], dict.fromkeys(STATUSES, 1))
        self.assertEqual(sum(model['status_totals'].values()), len(tasks))
        self.assertEqual(model['vehicles'][0]['status_totals'], model['status_totals'])
        self.assertEqual(model['vehicles'][1]['task_count'], 0)
        self.assertIsNone(model['vehicles'][1]['costs']['total_cost_cents'])

    def test_mixed_interval_precedence_and_mileage_unknown(self):
        for days, status in ((101, 'Overdue'), (100, 'Due'), (90, 'Due soon'), (89, 'Mileage needed')):
            t = task(interval_miles=1000, warning_miles=100,
                     baseline_date=(TODAY - timedelta(days=days)).isoformat())
            row = build([t])['tasks'][0]
            self.assertEqual(row['status'], status)
            self.assertTrue(row['mileage_unknown'])
        t = task(interval_miles=1000, warning_miles=100, baseline_date=TODAY.isoformat())
        for mileage, status in ((1001, 'Overdue'), (1000, 'Due'), (900, 'Due soon'), (899, 'Upcoming')):
            self.assertEqual(build([t], selected_profile_id=PID, current_mileage=mileage)['tasks'][0]['status'], status)

    def test_explicit_mileage_only_selected_live_profile_and_no_mutation(self):
        tasks = [task(interval_miles=1000, warning_miles=100),
                 task(11, pid=OTHER, interval_miles=1000, warning_miles=100)]
        before = deepcopy(tasks)
        model = build(tasks, profiles=[profile(), profile(OTHER)], selected_profile_id=PID, current_mileage=1000)
        self.assertEqual([r['current_mileage'] for r in model['tasks']], [1000, None])
        self.assertEqual(tasks, before)
        model['tasks'][0]['task']['profile_snapshot']['mileage'] = 0
        self.assertEqual(tasks, before)
        self.assertTrue(all(r['current_mileage'] is None for r in build(tasks)['tasks']))
        self.assertEqual(profile()['mileage'], 43000)

    def test_mileage_validation_and_disabled_archived_not_evaluated(self):
        t = task(baseline_mileage=48001, interval_miles=1000, warning_miles=100)
        for value in (True, -1, 1.5, '48001', 48000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build([t], selected_profile_id=PID, current_mileage=value)
        for pid in (OTHER, 'bad-id', True):
            with self.assertRaises(ValueError):
                build([t], selected_profile_id=pid)
        with self.assertRaises(ValueError):
            build([t], current_mileage=48001)
        for archived in (True, False):
            t.update(enabled=False, archived=archived)
            row = build([t], selected_profile_id=PID, current_mileage=0)['tasks'][0]
            self.assertIsNone(row['current_mileage'])
            self.assertEqual(row['status'], 'Archived' if archived else 'Disabled')

    def test_appointment_boundaries_exclusions_and_independent_due_status(self):
        offsets = (-1, 0, 7, 8, 30, 31, 90, 91)
        tasks = [task(10 + i, scheduled_date=(TODAY + timedelta(days=n)).isoformat()) for i, n in enumerate(offsets)]
        tasks += [task(30, enabled=False, scheduled_date=TODAY.isoformat()),
                  task(31, enabled=False, archived=True, scheduled_date=TODAY.isoformat())]
        model = build(tasks)
        self.assertEqual([len(model['appointments'][b]) for b in BUCKETS], [1, 2, 2, 2])
        self.assertEqual(len({r['task']['task_id'] for group in model['appointments'].values() for r in group}), 7)
        self.assertTrue(all(r['status'] == 'Overdue' for r in model['tasks'][:8]))
        self.assertEqual(model['tasks'][7]['appointment_bucket'], None)
        for today in (date(2028, 2, 28), date(2026, 12, 31)):
            t = task(scheduled_date=(today + timedelta(days=7)).isoformat())
            m = build_dashboard({'schema_version': 1, 'tasks': [t]}, {'schema_version': 1, 'profiles': []}, today=today)
            self.assertEqual(len(m['appointments'][BUCKETS[1]]), 1)

    def test_cost_ranges_exact_money_legacy_zero_and_no_priced_matches(self):
        legacy = {k: v for k, v in service(100, completed_date='2026-10-21').items()
                  if k in ('service_id', 'completed_date', 'mileage', 'notes')}
        unpriced = service(101, completed_date='2026-10-22')
        priced = service(102, labor_cost='45.50', parts_cost='12.25', tax='4.62')
        t = task(services=[legacy, unpriced, priced], enabled=False, archived=True)
        full = build([t], profiles=[])
        self.assertEqual(full['costs'], dict(service_count=3, priced_service_count=1,
            unpriced_service_count=2, legacy_service_count=1, total_cost_cents=6237))
        filtered = build([t], cost_start_date='2026-10-23', cost_end_date='2026-10-23')
        self.assertEqual(filtered['costs']['total_cost_cents'], 6237)
        self.assertEqual(filtered['costs']['service_count'], 1)
        self.assertEqual(len(filtered['tasks'][0]['task']['services']), 3)
        self.assertEqual(filtered['status_totals'], full['status_totals'])
        self.assertIsNone(build([t], cost_end_date='2026-10-22')['costs']['total_cost_cents'])
        self.assertIsNone(build([t], cost_start_date='2026-10-24')['costs']['total_cost_cents'])
        zero = task(11, services=[service(103, labor_cost='0')], enabled=False)
        self.assertEqual(build([zero])['costs']['total_cost_cents'], 0)
        self.assertEqual(build([zero])['costs']['priced_service_count'], 1)

    def test_identity_snapshot_variants_missing_profiles_and_sorting(self):
        a = task(services=[service()])
        b = task(11, title='Other task'); b['profile_snapshot']['vehicle_name'] = 'Old name'
        c = task(12, pid=OTHER)
        model = build([c, b, a], profiles=[profile(), profile(OTHER)])
        self.assertEqual(len(model['vehicles']), 2)
        self.assertEqual(len(model['vehicles'][0]['snapshots']), 2)
        self.assertEqual(model, build([a, b, c], profiles=[profile(OTHER), profile()]))
        missing = build([a, b], profiles=[])
        self.assertTrue(missing['vehicles'][0]['profile_missing'])
        self.assertEqual(missing['vehicles'][0]['task_count'], 2)

    def test_v160_work_order_baseline_next_mileage_and_historical_totals(self):
        legacy = {k: v for k, v in service(100, completed_date='2026-10-22', mileage=47000).items()
                  if k in ('service_id', 'completed_date', 'mileage', 'notes')}
        priced = service(101, provider='KINU Motor Pool', invoice_reference='WO-1600',
                         labor_cost='45.50', parts_cost='12.25', tax='4.62')
        t = task(interval_days=None, warning_days=None, interval_miles=1000, warning_miles=100,
                 services=[legacy, priced])
        m = build([t], selected_profile_id=PID, current_mileage=48001)
        self.assertEqual(m['tasks'][0]['next_due_mileage'], 49001)
        self.assertEqual(m['costs'], dict(service_count=2, priced_service_count=1,
            unpriced_service_count=1, legacy_service_count=1, total_cost_cents=6237))
        self.assertEqual(m['tasks'][0]['task']['profile_snapshot']['mileage'], 43000)

    def test_invalid_ranges_types_and_corrupt_structures(self):
        for options in ({'cost_start_date': '2026-10-24', 'cost_end_date': '2026-10-23'},
                        {'cost_start_date': '20261023'}, {'cost_end_date': True}):
            with self.assertRaises(ValueError):
                build(**options)
        for today in ('2026-10-23', datetime(2026, 10, 23), None):
            with self.assertRaises(ValueError):
                build_dashboard({'schema_version': 2, 'tasks': []}, {'schema_version': 1, 'profiles': []}, today=today)
        bad = task(services=[service(labor_cost='1')]); bad['services'][0]['total_cost_cents'] = 0
        with self.assertRaises(ValueError):
            build([bad])
        with self.assertRaises(ValueError):
            build([task(), task()])
