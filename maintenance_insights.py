"""Deterministic, read-only maintenance facts and estimates; no implicit clock."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json

from fleet_dashboard import STATUSES, parse_date
from inspection_history import validate_history
from maintenance_schedule import (LEGACY_SERVICE_KEYS, SERVICE_KEYS, calculate_due,
                                  validate_schedule, validate_task, _work_order, _date, _uuid, _text)
from vehicle_profiles import validate_profiles

ASSUMPTIONS = (
    ('endpoint_method', 'Observed endpoint rate uses oldest and newest accepted dates; all points are checked.'),
    ('freshness_days', 'Latest observation age must be 90 days or less.'),
    ('inspection_dates', 'Inspection observations use the stored UTC calendar date.'),
    ('daily_conflicts', 'Different mileages on one calendar date prevent a trustworthy rate.'),
    ('references', 'Explicit mileage is session-only; otherwise estimates start at the last recorded observation.'),
    ('costs', 'Inclusive completion-date bounds affect costs and months only; unpriced services are not zero.'),
    ('empty_months', 'Months without matching recorded services are omitted.'),
    ('estimate', 'Estimates assume the observed endpoint rate continues; they are not guaranteed service dates.'),
    ('history', 'Complete maintenance service history ignores cost and approaching filters.'),
)


def _day(value):
    if type(value) is not date:
        raise ValueError('Evaluation date must be a date.')
    return value


def warning(code, message, *, severity='warning', **identity):
    return dict(code=code, severity=severity, message=message, **identity)


def _digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def collect_observations(schedule, history, *, as_of):
    """Return dated points plus exclusions, preserving all duplicate provenance."""
    _day(as_of)
    schedule, history = validate_schedule(schedule), validate_history(deepcopy(history))
    candidates, warnings = [], []
    for task in sorted(schedule['tasks'], key=lambda t: (t['profile_id'], t['task_id'])):
        for service in task['services']:
            candidates.append(dict(profile_id=task['profile_id'], observation_date=service['completed_date'],
                observation_mileage=service['mileage'], source_kind='service', task_id=task['task_id'],
                service_id=service['service_id'], source_record_ref=service['service_id'],
                source_digest=_digest(service)))
    for index, record in enumerate(history['inspections']):
        provenance = dict(source_kind='inspection', source_record_ref=str(index), source_digest=_digest(record))
        if 'profile_id' not in record:
            warnings.append(warning('unlinked_inspection', 'Inspection has no stable profile association; excluded from rates.', **provenance))
            if datetime.fromisoformat(record['timestamp']).date() > as_of:
                warnings.append(warning('future_observation', 'Future unlinked inspection is excluded from rates and observed trends.', **provenance))
            continue
        candidates.append(dict(profile_id=record['profile_id'],
            observation_date=datetime.fromisoformat(record['timestamp']).date().isoformat(),
            observation_mileage=record['mileage'], **provenance))
    groups = {}
    for candidate in candidates:
        key = (candidate['profile_id'], candidate['observation_date'], candidate['observation_mileage'])
        point = groups.setdefault(key, dict(profile_id=key[0], observation_date=key[1],
            observation_mileage=key[2], observation_accepted=key[1] <= as_of.isoformat(), provenance=[]))
        point['provenance'].append({k: v for k, v in candidate.items()
                                    if k not in ('profile_id', 'observation_date', 'observation_mileage')})
        if not point['observation_accepted']:
            warnings.append(warning('future_observation', 'Future observation is excluded from rates and observed trends.', **candidate))
    points = [groups[k] for k in sorted(groups)]
    for point in points:
        point['provenance'].sort(key=lambda p: (p['source_kind'], p['source_record_ref'], p['source_digest']))
    return dict(observations=points, warnings=warnings)


def derive_usage_rate(observations, *, as_of, max_age_days=90):
    """Exact observed endpoint rate. Conflicts never select a convenient subset."""
    _day(as_of)
    if type(max_age_days) is not int or max_age_days < 0:
        raise ValueError('Freshness limit must be a nonnegative integer.')
    points = deepcopy(observations)
    ids = set()
    for point in points:
        _uuid(point['profile_id']); ids.add(point['profile_id'])
        _date(point['observation_date'])
        if type(point['observation_mileage']) is not int or point['observation_mileage'] < 0:
            raise ValueError('Observation mileage must be a nonnegative integer.')
        if type(point.get('observation_accepted', True)) is not bool:
            raise ValueError('Observation acceptance must be a boolean.')
    if len(ids) > 1:
        raise ValueError('A rate requires one stable profile ID.')
    points = sorted({(p['observation_date'], p['observation_mileage']) for p in points
                     if p.get('observation_accepted', True) and p['observation_date'] <= as_of.isoformat()})
    result = dict(availability='Unavailable', unavailable_reason=None, rate_start_date=None,
        rate_start_mileage=None, rate_end_date=None, rate_end_mileage=None,
        rate_delta_miles=None, rate_delta_days=None, rate_miles_per_day=None)
    reason = None
    if len({p[0] for p in points}) != len(points):
        reason = 'conflicting_same_day_mileage'
    elif any(b[1] < a[1] for a, b in zip(points, points[1:])):
        reason = 'decreasing_mileage'
    elif len(points) < 2:
        reason = 'fewer_than_two_distinct_dates'
    elif (as_of - date.fromisoformat(points[-1][0])).days > max_age_days:
        reason = 'latest_observation_stale'
    if reason:
        result['unavailable_reason'] = reason
        return result
    first, last = points[0], points[-1]
    miles, days = last[1] - first[1], (date.fromisoformat(last[0]) - date.fromisoformat(first[0])).days
    hundredths = (2 * miles * 100 + days) // (2 * days)
    result.update(availability='Available', rate_start_date=first[0], rate_start_mileage=first[1],
        rate_end_date=last[0], rate_end_mileage=last[1], rate_delta_miles=miles, rate_delta_days=days,
        rate_miles_per_day=f'{hundredths // 100}.{hundredths % 100:02d}')
    return result


def forecast_task(task, *, as_of, explicit_mileage=None, usage_rate=None):
    """Keep recorded thresholds/status and independent date/mileage estimates."""
    _day(as_of)
    task = validate_task(task)
    anchor = task['services'][-1] if task['services'] else None
    anchor_date = anchor['completed_date'] if anchor else task['baseline_date']
    anchor_mileage = anchor['mileage'] if anchor else task['baseline_mileage']
    invalid = explicit_mileage is not None and (type(explicit_mileage) is not int or explicit_mileage < 0
              or (anchor_mileage is not None and explicit_mileage < anchor_mileage))
    excluded = task['archived'] or not task['enabled']
    due = calculate_due(task, today=as_of, current_mileage=None if invalid or excluded else explicit_mileage)
    axes = []
    for axis, interval in (('date', 'interval_days'), ('mileage', 'interval_miles')):
        row = dict(forecast_axis=axis, anchor_date=anchor_date, anchor_mileage=anchor_mileage,
                   availability='Available', unavailable_reason=None, reference_date=None,
                   reference_mileage=None, reference_kind=None, estimated_days=None, estimated_due_date=None)
        if task[interval] is None:
            row.update(availability='Not applicable', unavailable_reason='axis_not_configured')
        elif excluded:
            row.update(availability='Excluded', unavailable_reason='archived' if task['archived'] else 'disabled')
        elif anchor_date is not None and anchor_date > as_of.isoformat():
            row.update(availability='Unavailable', unavailable_reason='future_service_or_baseline_anchor')
        elif axis == 'mileage':
            reason = None
            rate = usage_rate or {}
            if invalid:
                reason = 'invalid_explicit_mileage'
            elif explicit_mileage is not None:
                row.update(reference_date=as_of.isoformat(), reference_mileage=explicit_mileage,
                           reference_kind='Explicit session mileage')
            elif rate.get('availability') == 'Available':
                row.update(reference_date=rate['rate_end_date'], reference_mileage=rate['rate_end_mileage'],
                           reference_kind='Estimate from last recorded observation')
            else:
                reason = rate.get('unavailable_reason', 'no_trustworthy_observed_endpoint_rate')
            if reason is None:
                remaining = due['next_due_mileage'] - row['reference_mileage']
                if remaining <= 0:
                    row['availability'] = 'Threshold already reached at reference'
                elif rate.get('availability') != 'Available':
                    reason = rate.get('unavailable_reason', 'no_trustworthy_observed_endpoint_rate')
                elif rate['rate_delta_miles'] == 0:
                    reason = 'observed_endpoint_rate_is_zero'
                else:
                    n, d = remaining * rate['rate_delta_days'], rate['rate_delta_miles']
                    days = (n + d - 1) // d
                    try:
                        estimated = date.fromisoformat(row['reference_date']) + timedelta(days=days)
                        row.update(estimated_days=days, estimated_due_date=estimated.isoformat())
                    except OverflowError:
                        reason = 'estimated_date_exceeds_supported_calendar'
            if reason:
                row.update(availability='Unavailable', unavailable_reason=reason)
        axes.append(row)
    return dict(**due, axes=axes, explicit_mileage_invalid=invalid)


def _validate_services(services):
    ids = set()
    for service in services:
        # Service wrappers carry task identity separately, never in saved records.
        record = service.get('service', service)
        if set(record) not in (LEGACY_SERVICE_KEYS, SERVICE_KEYS):
            raise ValueError('Invalid service fields.')
        _uuid(record['service_id']); _date(record['completed_date'])
        if record['service_id'] in ids:
            raise ValueError('Duplicate service ID.')
        ids.add(record['service_id'])
        if type(record['mileage']) is not int or record['mileage'] < 0:
            raise ValueError('Invalid service mileage.')
        _text(record['notes'], 'Service notes', 10000)
        if set(record) == SERVICE_KEYS:
            _work_order(record)
        yield record


def summarize_service_costs(services):
    records = list(_validate_services(services))
    priced = [s['total_cost_cents'] for s in records if s.get('total_cost_cents') is not None]
    total, count = sum(priced), len(priced)
    return dict(service_count=len(records), priced_service_count=count,
        unpriced_service_count=len(records) - count,
        legacy_service_count=sum(set(s) == LEGACY_SERVICE_KEYS for s in records),
        total_cost_cents=total if count else None,
        average_cost_cents=(2 * total + count) // (2 * count) if count else None,
        min_cost_cents=min(priced) if count else None, max_cost_cents=max(priced) if count else None)


def _bounds(start, end):
    start, end = parse_date(start), parse_date(end)
    if start and end and start > end:
        raise ValueError('Cost start date must not exceed end date.')
    return start.isoformat() if start else None, end.isoformat() if end else None


def _matching(services, start, end):
    return [s for s in services if (start is None or s.get('service', s)['completed_date'] >= start)
            and (end is None or s.get('service', s)['completed_date'] <= end)]


def build_monthly_costs(services, *, start_date=None, end_date=None):
    records = list(_validate_services(services))
    start, end = _bounds(start_date, end_date)
    months = {}
    for service in _matching(records, start, end):
        months.setdefault(service['completed_date'][:7], []).append(service)
    return [dict(month=month, **summarize_service_costs(months[month])) for month in sorted(months)]


def build_insights(schedule, profiles, history, *, as_of, selected_profile_id=None,
                   explicit_current_mileage=None, cost_start_date=None, cost_end_date=None):
    _day(as_of)
    schedule, profiles = validate_schedule(schedule), validate_profiles(profiles)
    history = validate_history(deepcopy(history))
    start, end = _bounds(cost_start_date, cost_end_date)
    live = {p['profile_id']: p for p in profiles['profiles']}
    if selected_profile_id is not None and selected_profile_id not in live:
        raise ValueError('Explicit mileage requires an existing selected profile ID.')
    collected = collect_observations(schedule, history, as_of=as_of)
    points, warnings = collected['observations'], collected['warnings']
    vehicles = {pid: dict(profile_id=pid, vehicle_name=p['vehicle_name'], profile_missing=False,
                         profile=deepcopy(p)) for pid, p in live.items()}
    for task in sorted(schedule['tasks'], key=lambda t: (t['profile_id'], t['task_id'])):
        vehicles.setdefault(task['profile_id'], dict(profile_id=task['profile_id'],
            vehicle_name=task['profile_snapshot']['vehicle_name'], profile_missing=True, profile=None))
    for point in points:
        vehicles.setdefault(point['profile_id'], dict(profile_id=point['profile_id'],
            vehicle_name='Missing profile', profile_missing=True, profile=None))
    rates = {}
    for pid in sorted(vehicles):
        if vehicles[pid]['profile_missing']:
            warnings.append(warning('missing_live_profile',
                'Historical stable profile ID has no live profile; history is retained without name-based reassociation.', profile_id=pid))
        observed = [p for p in points if p['profile_id'] == pid]
        rates[pid] = derive_usage_rate(observed, as_of=as_of)
        if rates[pid]['availability'] != 'Available':
            warnings.append(warning(rates[pid]['unavailable_reason'], 'Observed endpoint rate unavailable: ' +
                                    rates[pid]['unavailable_reason'].replace('_', ' ') + '.', profile_id=pid))
        accepted = [p for p in observed if p['observation_accepted']]
        if accepted and pid in live:
            latest = max(p['observation_date'] for p in accepted)
            if any(p['observation_mileage'] != live[pid]['mileage'] for p in accepted if p['observation_date'] == latest):
                warnings.append(warning('undated_profile_disagreement',
                    'Undated profile mileage differs from dated observations; it is not a rate input.',
                    severity='info', profile_id=pid))
    input_error = None
    if explicit_current_mileage is not None:
        if selected_profile_id is None or type(explicit_current_mileage) is not int or explicit_current_mileage < 0:
            input_error = 'Select a live profile and enter nonnegative integer mileage.'
        else:
            minimums = [p['observation_mileage'] for p in points if p['profile_id'] == selected_profile_id and p['observation_accepted']]
            for task in schedule['tasks']:
                if task['profile_id'] == selected_profile_id and task['enabled'] and not task['archived']:
                    anchor = task['services'][-1]['mileage'] if task['services'] else task['baseline_mileage']
                    if anchor is not None:
                        minimums.append(anchor)
            if minimums and explicit_current_mileage < max(minimums):
                input_error = 'Explicit mileage is below accepted history or an applicable task anchor.'
        if input_error:
            warnings.append(warning('invalid_explicit_mileage', input_error, severity='error', profile_id=selected_profile_id))
    totals = dict.fromkeys(STATUSES, 0)
    tasks, services, approaching, already_due = [], [], [], []
    for task in sorted(schedule['tasks'], key=lambda t: (t['profile_id'], t['task_id'])):
        pid = task['profile_id']
        mileage = explicit_current_mileage if pid == selected_profile_id and not input_error else None
        forecast = forecast_task(task, as_of=as_of, explicit_mileage=mileage, usage_rate=rates[pid])
        if input_error and pid == selected_profile_id:
            for axis in forecast['axes']:
                if axis['forecast_axis'] == 'mileage' and axis['availability'] != 'Not applicable' and task['enabled'] and not task['archived']:
                    axis.update(availability='Unavailable', unavailable_reason='invalid_explicit_mileage',
                                estimated_due_date=None, estimated_days=None,
                                reference_date=None, reference_mileage=None, reference_kind=None)
        row = dict(task=task, profile_id=pid, vehicle_name=vehicles[pid]['vehicle_name'],
                   profile_missing=vehicles[pid]['profile_missing'], **forecast)
        totals[row['status']] += 1
        tasks.append(row)
        if any(a['unavailable_reason'] == 'future_service_or_baseline_anchor' for a in forecast['axes']):
            warnings.append(warning('future_task_anchor',
                'Future service/baseline anchor makes operational forecasts unavailable.', profile_id=pid, task_id=task['task_id']))
        if task['enabled'] and not task['archived']:
            if row['status'] in ('Due', 'Overdue'):
                already_due.append(dict(profile_id=pid, task_id=task['task_id'], status=row['status']))
            for axis in forecast['axes']:
                target = forecast['next_due_date'] if axis['forecast_axis'] == 'date' else axis['estimated_due_date']
                if axis['availability'] == 'Available' and target and 0 <= (date.fromisoformat(target) - as_of).days <= 30:
                    approaching.append(dict(profile_id=pid, task_id=task['task_id'], task_title=task['title'],
                                            status=row['status'], **axis, next_due_date=forecast['next_due_date']))
        for service in task['services']:
            wrapper = dict(profile_id=pid, task_id=task['task_id'], task_title=task['title'], service=service)
            services.append(wrapper)
            if service['completed_date'] > as_of.isoformat():
                warnings.append(warning('future_service_cost',
                    'Future completion date is excluded from observed trends; literal cost queries may include it.',
                    profile_id=pid, task_id=task['task_id'], service_id=service['service_id']))
    services.sort(key=lambda s: (s['service']['completed_date'], s['profile_id'], s['task_id'], s['service']['service_id']))
    matching = _matching(services, start, end)
    for pid, vehicle in vehicles.items():
        vehicle['costs'] = summarize_service_costs([s for s in matching if s['profile_id'] == pid])
        vehicle['task_count'] = sum(t['profile_id'] == pid for t in tasks)
    recent = [s for s in services if 0 <= (as_of - date.fromisoformat(s['service']['completed_date'])).days <= 30]
    approaching.sort(key=lambda r: (r['next_due_date'] if r['forecast_axis'] == 'date' else r['estimated_due_date'],
                                    r['profile_id'], r['task_id'], r['forecast_axis']))
    warnings.sort(key=lambda w: tuple(str(w.get(k, '')) for k in ('code', 'profile_id', 'task_id', 'service_id', 'source_kind', 'source_record_ref')))
    return deepcopy(dict(report_version=1, as_of=as_of.isoformat(), selected_profile_id=selected_profile_id,
        explicit_current_mileage=explicit_current_mileage, cost_start_date=start, cost_end_date=end,
        assumptions=[dict(code=k, message=v) for k, v in ASSUMPTIONS], warnings=warnings,
        observations=points, rates=rates, vehicles=[vehicles[k] for k in sorted(vehicles)], tasks=tasks,
        status_totals=totals, costs=summarize_service_costs(matching),
        months=build_monthly_costs([s['service'] for s in services], start_date=start, end_date=end),
        recent_services=recent, approaching=approaching, already_due=already_due, service_history=services,
        input_error=input_error, export_available=input_error is None, sources=[]))
