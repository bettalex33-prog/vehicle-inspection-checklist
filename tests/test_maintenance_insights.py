"""Pure Insights boundary and preservation tests: no filesystem or Tk."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import unittest

import maintenance_insights as mi
from checklist import create_default_data
from inspection_history import create_inspection_record, create_default_findings
from test_fleet_dashboard import PID, OTHER, profile, task, service

AS_OF = date(2026, 10, 21)


def inspection(day='2026-10-11', mileage=10300, pid=PID):
    return create_inspection_record('Unit', str(mileage), '', create_default_data(),
        findings=create_default_findings(), assessment='Ready for service',
        timestamp=datetime.fromisoformat(day + 'T23:59:00+00:00'),
        profile=profile(pid) if pid else None)


def schedule(tasks=()):
    return {'schema_version': 2, 'tasks': list(tasks)}


def history(records=()):
    return {'schema_version': 3, 'inspections': list(records)}


def positive_task(**changes):
    values = dict(baseline_date='2026-10-01', baseline_mileage=10000, interval_days=30,
        warning_days=10, interval_miles=701, warning_miles=100,
        services=[service(100, '2026-10-01', 10000), service(101, '2026-10-11', 10300)])
    values.update(changes)
    return task(**values)


def build(tasks=(), records=(), profiles=None, **kwargs):
    return mi.build_insights(schedule(tasks), {'schema_version': 1, 'profiles': [profile()] if profiles is None else profiles},
        history(records), as_of=kwargs.pop('as_of', AS_OF), **kwargs)


def points(*pairs, pid=PID):
    return [dict(profile_id=pid, observation_date=d, observation_mileage=m, observation_accepted=True) for d, m in pairs]


def rate(*pairs, as_of=AS_OF):
    return mi.derive_usage_rate(points(*pairs), as_of=as_of)


class TestMaintenanceInsights(unittest.TestCase):
    def test_final_service_anchors_both_axes(self):
        row = build([positive_task()])['tasks'][0]
        self.assertEqual((row['next_due_date'], row['next_due_mileage']), ('2026-11-10', 11001))
        self.assertEqual(row['remaining_days'], 20)
        self.assertIsNone(row['remaining_miles'])

    def test_baseline_anchors_without_services(self):
        row = build([positive_task(services=[])])['tasks'][0]
        self.assertEqual((row['next_due_date'], row['next_due_mileage']), ('2026-10-31', 10701))

    def test_date_axis_independent_of_missing_rate(self):
        row = build([positive_task(services=[])])['tasks'][0]
        self.assertEqual(row['axes'][0]['availability'], 'Available')
        self.assertEqual(row['axes'][1]['availability'], 'Unavailable')
        self.assertEqual(row['status'], 'Due soon')

    def test_unconfigured_axes_are_not_applicable(self):
        a = build([task()])['tasks'][0]
        b = build([task(interval_days=None, warning_days=None, interval_miles=1000, warning_miles=100)])['tasks'][0]
        self.assertEqual(a['axes'][1]['availability'], 'Not applicable')
        self.assertEqual(b['axes'][0]['availability'], 'Not applicable')

    def test_existing_date_status_boundaries(self):
        for remaining, expected in ((-1, 'Overdue'), (0, 'Due'), (10, 'Due soon'), (11, 'Upcoming')):
            with self.subTest(remaining=remaining):
                t = task(baseline_date=(AS_OF - timedelta(days=100 - remaining)).isoformat())
                self.assertEqual(build([t])['tasks'][0]['status'], expected)

    def test_existing_mileage_status_boundaries(self):
        for mileage, expected in ((11002, 'Overdue'), (11001, 'Due'), (10901, 'Due soon'), (10900, 'Upcoming')):
            with self.subTest(mileage=mileage):
                t = positive_task(interval_days=None, warning_days=None)
                row = build([t], selected_profile_id=PID, explicit_current_mileage=mileage)['tasks'][0]
                self.assertEqual(row['status'], expected)

    def test_disabled_and_archived_retain_thresholds(self):
        for archived, expected in ((False, 'Disabled'), (True, 'Archived')):
            row = build([positive_task(enabled=False, archived=archived)])['tasks'][0]
            self.assertEqual(row['status'], expected)
            self.assertEqual(row['next_due_mileage'], 11001)
            self.assertTrue(all(a['availability'] == 'Excluded' for a in row['axes']))

    def test_ceiling_estimate_from_last_observation(self):
        axis = build([positive_task()])['tasks'][0]['axes'][1]
        self.assertEqual((axis['estimated_days'], axis['estimated_due_date']), (24, '2026-11-04'))
        self.assertEqual(axis['reference_kind'], 'Estimate from last recorded observation')

    def test_explicit_reference_does_not_change_rate(self):
        a = build([positive_task()])
        b = build([positive_task()], selected_profile_id=PID, explicit_current_mileage=10600)
        self.assertEqual(a['rates'], b['rates'])
        self.assertEqual(b['tasks'][0]['axes'][1]['estimated_days'], 14)
        self.assertEqual(b['tasks'][0]['axes'][1]['estimated_due_date'], '2026-11-04')

    def test_threshold_reached_has_no_invented_crossing_date(self):
        axis = build([positive_task()], selected_profile_id=PID, explicit_current_mileage=11001)['tasks'][0]['axes'][1]
        self.assertEqual(axis['availability'], 'Threshold already reached at reference')
        self.assertIsNone(axis['estimated_due_date'])

    def test_zero_rate_is_observed_but_cannot_estimate(self):
        t = positive_task(services=[service(100, '2026-10-01', 10300), service(101, '2026-10-11', 10300)])
        model = build([t])
        self.assertEqual(model['rates'][PID]['rate_delta_miles'], 0)
        self.assertEqual(model['tasks'][0]['axes'][1]['unavailable_reason'], 'observed_endpoint_rate_is_zero')

    def test_calendar_overflow_is_unavailable(self):
        t = positive_task(interval_miles=10**20)
        self.assertEqual(build([t])['tasks'][0]['axes'][1]['unavailable_reason'], 'estimated_date_exceeds_supported_calendar')

    def test_rate_rounding_half_up_without_float(self):
        r = rate(('2026-10-01', 0), ('2026-10-09', 1))
        self.assertEqual((r['rate_delta_miles'], r['rate_delta_days'], r['rate_miles_per_day']), (1, 8, '0.13'))

    def test_freshness_day_90_and_91(self):
        pairs = (('2026-01-01', 1), ('2026-01-02', 2))
        self.assertEqual(rate(*pairs, as_of=date(2026, 4, 2))['availability'], 'Available')
        self.assertEqual(rate(*pairs, as_of=date(2026, 4, 3))['unavailable_reason'], 'latest_observation_stale')

    def test_services_and_linked_inspections_are_eligible(self):
        model = build([positive_task(services=[service(100, '2026-10-01', 10000)])], [inspection()])
        self.assertEqual(model['rates'][PID]['rate_delta_miles'], 300)
        self.assertEqual({p['provenance'][0]['source_kind'] for p in model['observations']}, {'inspection', 'service'})

    def test_unlinked_inspection_never_joins_by_name(self):
        model = build([], [inspection(pid=None)])
        self.assertEqual(model['observations'], [])
        self.assertIn('unlinked_inspection', [w['code'] for w in model['warnings']])

    def test_undated_values_baselines_appointments_not_observations(self):
        model = build([positive_task(services=[], scheduled_date='2026-10-21')])
        self.assertEqual(model['observations'], [])
        self.assertEqual(model['rates'][PID]['unavailable_reason'], 'fewer_than_two_distinct_dates')

    def test_future_observations_excluded_with_warning(self):
        model = build([], [inspection('2026-10-22')])
        self.assertFalse(model['observations'][0]['observation_accepted'])
        self.assertIn('future_observation', [w['code'] for w in model['warnings']])

    def test_same_day_duplicates_preserve_provenance(self):
        model = build([positive_task()], [inspection()])
        point = next(p for p in model['observations'] if p['observation_date'] == '2026-10-11')
        self.assertEqual(len(point['provenance']), 2)
        self.assertEqual(model['rates'][PID]['availability'], 'Available')

    def test_same_day_conflict_is_profile_local(self):
        model = build([positive_task()], [inspection(mileage=10301), inspection('2026-10-01', 1, OTHER), inspection('2026-10-11', 2, OTHER)])
        self.assertEqual(model['rates'][PID]['unavailable_reason'], 'conflicting_same_day_mileage')
        self.assertEqual(model['rates'][OTHER]['availability'], 'Available')
        self.assertEqual(model['costs']['service_count'], 2)

    def test_cross_task_mileage_decrease_rejected_for_rate(self):
        t = task(number=11, services=[service(102, '2026-10-12', 100)])
        self.assertEqual(build([positive_task(), t])['rates'][PID]['unavailable_reason'], 'decreasing_mileage')

    def test_one_date_zero_span_is_unavailable(self):
        self.assertEqual(rate(('2026-10-11', 1), ('2026-10-11', 1))['unavailable_reason'], 'fewer_than_two_distinct_dates')

    def test_stable_ids_separate_renamed_duplicate_names(self):
        model = build([positive_task()], [inspection('2026-10-01', 10, OTHER), inspection('2026-10-11', 20, OTHER)],
                      profiles=[profile(PID, 'Same'), profile(OTHER, 'Same')])
        self.assertEqual(model['rates'][PID]['rate_delta_miles'], 300)
        self.assertEqual(model['rates'][OTHER]['rate_delta_miles'], 10)

    def test_missing_profiles_and_archived_services_keep_history(self):
        model = build([positive_task(enabled=False, archived=True)], profiles=[])
        self.assertTrue(model['vehicles'][0]['profile_missing'])
        self.assertEqual(model['rates'][PID]['availability'], 'Available')
        self.assertEqual(len(model['service_history']), 2)
        self.assertIn('missing_live_profile', [w['code'] for w in model['warnings']])

    def test_undated_profile_disagreement_is_informational(self):
        model = build([positive_task()])
        notice = next(w for w in model['warnings'] if w['code'] == 'undated_profile_disagreement')
        self.assertEqual(notice['severity'], 'info')
        self.assertEqual(model['rates'][PID]['availability'], 'Available')

    def test_invalid_explicit_mileage_preserves_date_and_costs(self):
        for mileage in (True, -1, 'abc', 10000):
            model = build([positive_task()], selected_profile_id=PID, explicit_current_mileage=mileage)
            self.assertFalse(model['export_available'])
            self.assertEqual(model['tasks'][0]['axes'][0]['availability'], 'Available')
            self.assertEqual(model['tasks'][0]['axes'][1]['unavailable_reason'], 'invalid_explicit_mileage')
            self.assertIsNone(model['tasks'][0]['axes'][1]['reference_mileage'])
            self.assertEqual(model['costs']['service_count'], 2)

    def test_explicit_mileage_scoped_to_selected_id(self):
        t = positive_task(); other = deepcopy(t)
        other.update(task_id=task(number=11)['task_id'], profile_id=OTHER, services=[])
        model = build([t, other], profiles=[profile(), profile(OTHER)], selected_profile_id=PID, explicit_current_mileage=10600)
        row = next(r for r in model['tasks'] if r['profile_id'] == OTHER)
        self.assertIsNone(row['remaining_miles'])

    def test_zero_cost_is_priced_and_average_excludes_unpriced(self):
        legacy = {k: v for k, v in service(103).items() if k in mi.LEGACY_SERVICE_KEYS}
        costs = mi.summarize_service_costs([service(101, labor_cost='0.00'), service(102, labor_cost='10.01'), legacy])
        self.assertEqual(costs, dict(service_count=3, priced_service_count=2, unpriced_service_count=1,
            legacy_service_count=1, total_cost_cents=1001, average_cost_cents=501, min_cost_cents=0, max_cost_cents=1001))

    def test_no_priced_matches_is_not_recorded(self):
        for services in ([], [service()]):
            costs = mi.summarize_service_costs(services)
            for key in ('total_cost_cents', 'average_cost_cents', 'min_cost_cents', 'max_cost_cents'):
                self.assertIsNone(costs[key])

    def test_cost_bounds_are_inclusive_and_cost_only(self):
        a = build([positive_task()])
        b = build([positive_task()], cost_start_date='2026-10-11', cost_end_date='2026-10-11')
        self.assertEqual(b['costs']['service_count'], 1)
        for key in ('status_totals', 'rates', 'recent_services', 'approaching', 'service_history'):
            self.assertEqual(a[key], b[key])

    def test_months_sorted_and_unpriced_month_preserved(self):
        records = [service(101, '2026-10-11', labor_cost='0'), service(102, '2026-09-01')]
        months = mi.build_monthly_costs(records)
        self.assertEqual([m['month'] for m in months], ['2026-09', '2026-10'])
        self.assertIsNone(months[0]['total_cost_cents'])
        self.assertEqual(months[1]['total_cost_cents'], 0)

    def test_recent_service_inclusive_30_day_boundary(self):
        tasks = [task(number=10+i, services=[service(100+i, (AS_OF-timedelta(days=age)).isoformat(), 1)])
                 for i, age in enumerate((0, 30, 31))]
        self.assertEqual(len(build(tasks)['recent_services']), 2)

    def test_approaching_date_inclusive_boundaries(self):
        tasks = [task(number=10+i, baseline_date=(AS_OF-timedelta(days=100-offset)).isoformat())
                 for i, offset in enumerate((-1, 0, 30, 31))]
        model = build(tasks)
        self.assertEqual(len(model['approaching']), 2)
        self.assertEqual(len(model['already_due']), 2)

    def test_approaching_mileage_estimate_and_cost_independence(self):
        a = build([positive_task()])
        b = build([positive_task()], cost_start_date='2026-01-01', cost_end_date='2026-01-01')
        self.assertEqual({r['forecast_axis'] for r in a['approaching']}, {'date', 'mileage'})
        self.assertEqual(a['approaching'], b['approaching'])

    def test_future_services_disclosed_in_literal_costs_not_recent(self):
        model = build([task(services=[service(100, '2026-10-22', 1, labor_cost='1')])])
        self.assertEqual(model['costs']['total_cost_cents'], 100)
        self.assertEqual(model['recent_services'], [])
        self.assertIn('future_service_cost', [w['code'] for w in model['warnings']])

    def test_invalid_schema_cost_snapshot_and_association_fail(self):
        for key, value in (('profile_id', 'bad'), ('profile_snapshot', {}), ('services', [dict(service(), total_cost_cents=1)])):
            t = positive_task(); t[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build([t])

    def test_duplicate_service_ids_and_reversed_task_dates_fail(self):
        for records in ([service(100, '2026-10-01', 10000)] * 2,
                        [service(100, '2026-10-11', 10300), service(101, '2026-10-01', 10400)]):
            with self.assertRaises(ValueError):
                build([positive_task(services=records)])

    def test_inputs_and_returned_snapshots_are_independent(self):
        t, r = positive_task(), inspection()
        before = deepcopy((t, r))
        model = build([t], [r])
        model['tasks'][0]['task']['profile_snapshot']['vehicle_name'] = 'Changed'
        model['observations'][0]['provenance'].clear()
        self.assertEqual((t, r), before)

    def test_dates_are_explicit_and_cost_range_is_strict(self):
        for options in (dict(as_of=datetime(2026, 10, 21, tzinfo=timezone.utc)), dict(cost_start_date='2026-1-01'),
                        dict(cost_start_date='2026-10-22', cost_end_date='2026-10-21')):
            with self.assertRaises(ValueError):
                build(**options)

    def test_leap_day_and_year_boundaries(self):
        for baseline, interval, expected in (('2024-02-28', 1, '2024-02-29'), ('2026-12-31', 1, '2027-01-01')):
            row = build([task(baseline_date=baseline, interval_days=interval, warning_days=0)], as_of=date.fromisoformat(baseline))['tasks'][0]
            self.assertEqual(row['next_due_date'], expected)
