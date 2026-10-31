"""Pure presentation projection of an already-built maintenance insights model.

``build_dashboard_model(insights, checklist, scope_profile_id=None)`` accepts
the output of maintenance_insights.build_insights and a validated checklist
snapshot. None explicitly means the checklist is unavailable. No stores are read
or initialized. The caller owns validation and source freshness.

Display scope never reevaluates mileage, forecasts, costs, or dates. In particular,
it is independent of the insights model's selected_profile_id (evaluation scope).
Unknown display IDs raise ValueError rather than showing misleading empty totals.
All returned containers are independent copies, including between calls.
"""
from copy import deepcopy


def build_dashboard_model(insights, checklist, scope_profile_id=None):
    """Return presentation data; None checklist is an explicit unavailable state.

    Costs are copied from the precomputed fleet or vehicle summary. Recent and
    complete service history ignore cost bounds. Warnings without a profile ID
    remain visible in every scope. Backup health is intentionally unimplemented.
    """
    vehicles = insights['vehicles']
    if scope_profile_id is not None and (
            not isinstance(scope_profile_id, str) or
            not any(v['profile_id'] == scope_profile_id for v in vehicles)):
        raise ValueError('Display scope must identify a vehicle in the insights model.')

    def scoped(rows):
        return [row for row in rows
                if scope_profile_id is None or row['profile_id'] == scope_profile_id]

    tasks = scoped(insights['tasks'])
    if scope_profile_id is None:
        totals = insights['status_totals']
        costs = insights['costs']
    else:
        # Count existing classifications; never invoke due evaluation again.
        totals = dict.fromkeys(insights['status_totals'], 0)
        for row in tasks:
            totals[row['status']] += 1
        costs = next(v['costs'] for v in vehicles if v['profile_id'] == scope_profile_id)

    if checklist is None:
        progress = dict(availability='Unavailable', unavailable_reason='checklist_unavailable',
                        scope='shared', completed=None, total=None, percentage=None)
    else:
        total = len(checklist)
        completed = sum(item['complete'] for item in checklist.values())
        progress = dict(availability='Available', unavailable_reason=None,
                        scope='shared', completed=completed, total=total,
                        percentage=completed / total * 100)

    # ISO calendar dates sort chronologically. Stable IDs break same-day ties;
    # the second stable sort changes only date direction.
    recent = sorted(scoped(insights['recent_services']), key=lambda row: (
        row['profile_id'], row['task_id'], row['service']['service_id']))
    recent.sort(key=lambda row: row['service']['completed_date'], reverse=True)
    warnings = [row for row in insights['warnings'] if scope_profile_id is None
                or row.get('profile_id') is None or row['profile_id'] == scope_profile_id]

    return deepcopy(dict(
        as_of=insights['as_of'], scope_profile_id=scope_profile_id,
        evaluation=dict(selected_profile_id=insights['selected_profile_id'],
                        explicit_current_mileage=insights['explicit_current_mileage'],
                        input_error=insights['input_error'],
                        export_available=insights['export_available']),
        vehicles=vehicles, shared_inspection_progress=progress,
        maintenance=dict(status_totals=totals, tasks=tasks,
                         already_due=scoped(insights['already_due']),
                         approaching=scoped(insights['approaching'])),
        mileage_forecasts=dict(tasks=tasks, rates={
            pid: rate for pid, rate in insights['rates'].items()
            if scope_profile_id is None or pid == scope_profile_id}),
        service_cost_summary=dict(availability='Available',
                                  cost_start_date=insights['cost_start_date'],
                                  cost_end_date=insights['cost_end_date'], **costs),
        recent_services=recent, service_history=scoped(insights['service_history']),
        data_quality=dict(warnings=warnings, sources=insights['sources'],
                          assumptions=insights['assumptions']),
        backup_health=dict(availability='Unavailable',
                           unavailable_reason='backup_inspection_not_implemented')))
