"""Pure fleet evaluation. Mileage is an explicit, transient input, never inferred."""
from copy import deepcopy
from datetime import date
from uuid import UUID

from maintenance_schedule import calculate_due, validate_schedule, LEGACY_SERVICE_KEYS
from vehicle_profiles import validate_profiles

STATUSES = ('Overdue', 'Due', 'Due soon', 'Upcoming', 'Mileage needed', 'Disabled', 'Archived')
BUCKETS = ('Passed', '0–7 days', '8–30 days', '31–90 days')


def parse_date(value):
    """Optional canonical ISO date input for cost filters."""
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('Date must use YYYY-MM-DD.')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Date must use YYYY-MM-DD.')
    return parsed


def _costs(services):
    priced = [s['total_cost_cents'] for s in services if s.get('total_cost_cents') is not None]
    return {'service_count': len(services), 'priced_service_count': len(priced),
            'unpriced_service_count': len(services) - len(priced),
            'legacy_service_count': sum(set(s) == LEGACY_SERVICE_KEYS for s in services),
            'total_cost_cents': sum(priced) if priced else None}


def build_dashboard(schedule, profiles, *, today, selected_profile_id=None,
                    current_mileage=None, cost_start_date=None, cost_end_date=None):
    """Return independent fleet data; only the selected live profile gets mileage.

    Status and appointment totals cover the whole fleet. Cost bounds apply only
    to cost summaries; full task history and due evaluation are never filtered.
    """
    schedule = validate_schedule(schedule)
    profiles = validate_profiles(profiles)
    if type(today) is not date:
        raise ValueError('Evaluation date must be a date.')
    start, end = parse_date(cost_start_date), parse_date(cost_end_date)
    if start is not None and end is not None and start > end:
        raise ValueError('Cost start date must not exceed end date.')
    live = {p['profile_id']: p for p in profiles['profiles']}
    if selected_profile_id is not None:
        if (not isinstance(selected_profile_id, str)
                or str(UUID(selected_profile_id)) != selected_profile_id
                or selected_profile_id not in live):
            raise ValueError('Select an existing vehicle profile for mileage evaluation.')
    if current_mileage is not None:
        if selected_profile_id is None or type(current_mileage) is not int or current_mileage < 0:
            raise ValueError('Current mileage requires a selected profile and a nonnegative integer.')
    totals = dict.fromkeys(STATUSES, 0)
    appointments = {key: [] for key in BUCKETS}
    vehicles = {}
    rows, all_services = [], []
    # Retain vehicles without tasks, and group missing vehicles by stable ID.
    for pid, profile in live.items():
        vehicles[pid] = {'profile_id': pid, 'vehicle_name': profile['vehicle_name'],
                         'profile_missing': False, 'task_count': 0,
                         'status_totals': dict.fromkeys(STATUSES, 0), 'snapshots': [], 'services': []}
    for task in sorted(schedule['tasks'], key=lambda t: (t['profile_id'], t['task_id'])):
        pid = task['profile_id']
        if pid not in vehicles:
            vehicles[pid] = {'profile_id': pid, 'vehicle_name': task['profile_snapshot']['vehicle_name'],
                             'profile_missing': True, 'task_count': 0,
                             'status_totals': dict.fromkeys(STATUSES, 0), 'snapshots': [], 'services': []}
        vehicle = vehicles[pid]
        mileage = (current_mileage if pid == selected_profile_id and task['enabled']
                   and not task['archived'] else None)
        due = calculate_due(task, today=today, current_mileage=mileage)
        totals[due['status']] += 1
        vehicle['status_totals'][due['status']] += 1
        vehicle['task_count'] += 1
        if task['profile_snapshot'] not in vehicle['snapshots']:
            vehicle['snapshots'].append(deepcopy(task['profile_snapshot']))
        matching = [s for s in task['services']
                    if (start is None or s['completed_date'] >= start.isoformat())
                    and (end is None or s['completed_date'] <= end.isoformat())]
        vehicle['services'].extend(matching)
        all_services.extend(matching)
        row = {'task': task, 'profile_id': pid, 'vehicle_name': vehicle['vehicle_name'],
               'profile_missing': vehicle['profile_missing'], 'current_mileage': mileage,
               **due, 'costs': _costs(matching), 'appointment_bucket': None}
        if task['enabled'] and not task['archived'] and task['scheduled_date'] is not None:
            days = (date.fromisoformat(task['scheduled_date']) - today).days
            bucket = (BUCKETS[0] if days < 0 else BUCKETS[1] if days <= 7 else
                      BUCKETS[2] if days <= 30 else BUCKETS[3] if days <= 90 else None)
            row['appointment_bucket'] = bucket
            if bucket is not None:
                appointments[bucket].append(row)
        rows.append(row)
    for vehicle in vehicles.values():
        vehicle['costs'] = _costs(vehicle.pop('services'))
    for bucket in appointments.values():
        bucket.sort(key=lambda r: (r['task']['scheduled_date'], r['profile_id'], r['task']['task_id']))
    return deepcopy({'evaluated_on': today.isoformat(), 'scope_profile_id': selected_profile_id,
                     'explicit_current_mileage': current_mileage,
                     'cost_start_date': start.isoformat() if start else None,
                     'cost_end_date': end.isoformat() if end else None,
                     'status_totals': totals, 'vehicles': [vehicles[k] for k in sorted(vehicles)],
                     'tasks': rows, 'appointments': appointments, 'costs': _costs(all_services)})
