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
import re


_BACKUP_REASONS = frozenset('''all_stores_match archive_missing archive_not_validated
archive_unreadable checksum_mismatch current_bytes_differ current_store_evidence_missing
current_store_missing_or_unreadable different_bytes duplicate_or_excess_members
exact_bytes_match inconsistent_or_overlapping_members invalid_archived_data
invalid_manifest_json invalid_manifest_membership_or_entry invalid_recorded_timestamp
invalid_zip_or_crc malformed_manifest malformed_manifest_entry
manifest_membership_or_total_size member_or_total_size_limit missing_manifest
no_archive_supplied nonregular_symlink_or_hardlink not_archived not_supplied
path_missing release_payload_incompatible size_limit stores_not_archived
unsafe_compression_metadata unsafe_local_header unsafe_member unsafe_or_oversized_archive
unsupported_schema_application_or_release'''.split())
_STORE_NAMES = ('checklist_data.json', 'inspection_history.json',
                'maintenance_schedule.json', 'vehicle_profiles.json')
_BACKUP_LABELS = dict(unavailable='Backup inspection unavailable in this session',
    no_selection='No archive selected', required='Assessment required',
    loading='Assessing selected archive', stale='Stale — reassessment required',
    error='Backup assessment error', missing='Selected archive missing',
    invalid='Invalid archive', unsupported='Unsupported archive', partial='Partial backup',
    changed='Differs from current data', unknown='Unreadable / unknown comparison',
    complete='Complete exact-byte match')


def build_backup_panel(report=None, *, status='no_selection'):
    """Project supplied facts only; never retain paths or arbitrary diagnostics.

    Session status overrides assessment facts. Only an assessed, fully validated
    report may assert health. Unknown reason strings are deliberately discarded.
    """
    def reason(value):
        return value if type(value) is str and (value in _BACKUP_REASONS or
            re.fullmatch(r'os_error_[0-9]{1,5}', value)) else None

    def choice(value, values):
        return value if type(value) is str and value in values else None

    def boolean(value):
        return value if type(value) is bool else None

    result = dict(status=status, classification=status, message=_BACKUP_LABELS.get(status,
        _BACKUP_LABELS['error']), availability='Unavailable', coverage='unknown',
        healthy=False, partial=False, differs=False, recorded_created_at=None,
        release=None, reasons={}, stores=[],
        semantics='Last assessed evidence; no continuous monitoring. Exact bytes do not establish backup age or restore safety.')
    if status != 'assessed':
        if status not in _BACKUP_LABELS:
            raise ValueError('Unknown backup session status.')
        return result
    if not isinstance(report, dict):
        raise ValueError('Assessment report is required.')
    archive = report['archive']
    result['availability'] = choice(report.get('availability'), ('Available', 'Unavailable', 'Invalid'))
    result['coverage'] = choice(report.get('coverage'), ('unknown', 'invalid', 'partial', 'changed', 'complete'))
    result['reasons'] = {key: reason(report.get(key)) for key in ('coverage_reason', 'unavailable_reason')}
    result['reasons'].update({key: reason(archive.get(key)) for key in
        ('read_reason', 'zip_reason', 'manifest_reason', 'compatibility_reason')})
    stamp = archive.get('created_at')
    if type(stamp) is str and re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)', stamp):
        result['recorded_created_at'] = stamp
    result['release'] = choice(archive.get('release'), ('1.4.0', '1.5.0', '1.6.0', '1.7.0', '1.8.0', '1.9.0'))
    for row in sorted(report['stores'], key=lambda row: _STORE_NAMES.index(row['name'])):
        result['stores'].append(dict(name=choice(row['name'], _STORE_NAMES),
            archived=boolean(row.get('archived')),
            archive_checksum_valid=boolean(row.get('archive_checksum_valid')),
            current_presence=choice(row.get('current_presence'), ('Present', 'Missing', 'Unknown')),
            current_readability=choice(row.get('current_readability'), ('Readable', 'Unreadable', 'Unknown', 'Unsafe', 'Oversized')),
            current_reason=reason(row.get('current_reason')),
            exact_byte_match=boolean(row.get('exact_byte_match')),
            match_reason=reason(row.get('match_reason'))))
    rows = result['stores']
    valid = all(archive.get(key) is True for key in ('zip_valid', 'members_safe',
        'manifest_valid', 'compatible', 'checksum_valid', 'payload_valid'))
    result['partial'] = valid and any(row['archived'] is False for row in rows)
    result['differs'] = valid and any(row['exact_byte_match'] is False for row in rows)
    result['healthy'] = (valid and report.get('healthy') is True and result['coverage'] == 'complete'
        and result['availability'] == 'Available' and len(rows) == 4
        and {row['name'] for row in rows} == set(_STORE_NAMES)
        and all(row['archived'] is True and row['exact_byte_match'] is True
                and row['current_presence'] == 'Present' and row['current_readability'] == 'Readable'
                for row in rows))
    classification = ('missing' if archive.get('presence') == 'Missing' else
        'unsupported' if archive.get('compatible') is False else
        'invalid' if result['availability'] == 'Invalid' else
        'complete' if result['healthy'] else
        'changed' if result['differs'] else 'partial' if result['partial'] else 'unknown')
    result.update(classification=classification, message=_BACKUP_LABELS[classification])
    return result


def build_dashboard_model(insights, checklist, scope_profile_id=None):
    """Return presentation data; None checklist is an explicit unavailable state.

    Costs are copied from the precomputed fleet or vehicle summary. Recent and
    complete service history ignore cost bounds. Warnings without a profile ID
    remain visible in every scope. Backup assessment is published independently.
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
        backup_health=build_backup_panel()))
