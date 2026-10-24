"""Fixed-schema Insights reports and source-revalidated atomic publication."""
import csv
import hashlib
import html
import io
import os
from pathlib import Path
import stat
import tempfile

from data_backup import NAMES, reject_output_alias
from fleet_dashboard import STATUSES
from maintenance_schedule import LEGACY_SERVICE_KEYS, format_money
from maintenance_insights_sources import InsightsSnapshot, parent_identity

COLUMNS = tuple('row_type,report_version,as_of,selected_profile_id,explicit_current_mileage,cost_start_date,cost_end_date,profile_id,vehicle_name,profile_missing,task_id,task_title,service_id,source_kind,source_record_ref,source_digest,code,severity,message,field_name,field_value,snapshot_vehicle_name,snapshot_year,snapshot_make,snapshot_model,snapshot_mileage,enabled,archived,status,date_status,mileage_status,count,observation_date,observation_mileage,observation_accepted,rate_start_date,rate_start_mileage,rate_end_date,rate_end_mileage,rate_delta_miles,rate_delta_days,rate_miles_per_day,forecast_axis,availability,unavailable_reason,anchor_date,anchor_mileage,next_due_date,next_due_mileage,remaining_days,remaining_miles,reference_date,reference_mileage,reference_kind,estimated_days,estimated_due_date,month,service_count,priced_service_count,unpriced_service_count,legacy_service_count,total_cost_cents,average_cost_cents,min_cost_cents,max_cost_cents,completed_date,service_mileage,provider,invoice_reference,labor_cost_cents,parts_cost_cents,tax_cents,service_total_cost_cents,service_legacy,notes'.split(','))
ROW_TYPES = ('report_context', 'assumption', 'source', 'warning', 'status_total', 'vehicle_summary',
             'profile_snapshot', 'observation', 'usage_rate', 'task_forecast', 'cost_overall',
             'cost_vehicle', 'cost_month', 'recent_service', 'approaching_service', 'service_history')
_UNSET = object()


def insights_rows(model):
    if not model['export_available']:
        raise ValueError(model['input_error'])
    context = {k: model[k] for k in ('report_version', 'as_of', 'selected_profile_id',
               'explicit_current_mileage', 'cost_start_date', 'cost_end_date')}
    rows = []
    def add(kind, **values):
        rows.append(dict(context, row_type=kind, **values))
    add('report_context', message='Observed facts, recorded thresholds and derived estimates are separate.')
    for assumption in model['assumptions']:
        add('assumption', **assumption)
    for source in model['sources']:
        add('source', **source)
    for notice in model['warnings']:
        add('warning', **{k: v for k, v in notice.items() if k in COLUMNS})
    for status in STATUSES:
        add('status_total', status=status, count=model['status_totals'][status])
    for vehicle in model['vehicles']:
        add('vehicle_summary', **{k: vehicle[k] for k in ('profile_id', 'vehicle_name', 'profile_missing')},
            count=vehicle['task_count'])
    for row in model['tasks']:
        task = row['task']
        add('profile_snapshot', profile_id=row['profile_id'], task_id=task['task_id'],
            **{'snapshot_' + k: v for k, v in task['profile_snapshot'].items()})
    for point in model['observations']:
        for provenance in point['provenance']:
            add('observation', **{k: v for k, v in point.items() if k != 'provenance'}, **provenance)
    for pid, rate in sorted(model['rates'].items()):
        add('usage_rate', profile_id=pid, message='Observed endpoint rate', **rate)
    for row in model['tasks']:
        task = row['task']
        base = {k: row[k] for k in ('profile_id', 'vehicle_name', 'profile_missing', 'status',
                'date_status', 'mileage_status', 'next_due_date', 'next_due_mileage', 'remaining_days', 'remaining_miles')}
        for axis in row['axes']:
            add('task_forecast', **base, task_id=task['task_id'], task_title=task['title'],
                enabled=task['enabled'], archived=task['archived'], notes=task['notes'], **axis)
    add('cost_overall', **model['costs'])
    for vehicle in model['vehicles']:
        add('cost_vehicle', profile_id=vehicle['profile_id'], vehicle_name=vehicle['vehicle_name'],
            profile_missing=vehicle['profile_missing'], **vehicle['costs'])
    for month in model['months']:
        add('cost_month', **month)
    def service_values(wrapper):
        service = wrapper['service']
        return dict(profile_id=wrapper['profile_id'], task_id=wrapper['task_id'], task_title=wrapper['task_title'],
                    **{k: v for k, v in service.items() if k not in ('mileage', 'total_cost_cents')},
                    service_mileage=service['mileage'], service_total_cost_cents=service.get('total_cost_cents'),
                    service_legacy=set(service) == LEGACY_SERVICE_KEYS)
    for wrapper in model['recent_services']:
        add('recent_service', **service_values(wrapper))
    for row in model['approaching']:
        add('approaching_service', **{k: v for k, v in row.items() if k in COLUMNS})
    # Already-due/overdue items remain visible as task_forecast status rows, separately from approaches.
    for wrapper in model['service_history']:
        add('service_history', **service_values(wrapper))
    return rows


def display(value):
    return '' if value is None else str(value).lower() if type(value) is bool else str(value)


def safe_cell(value):
    if not isinstance(value, str):
        return display(value)
    stripped = value
    while stripped and (stripped[0].isspace() or ord(stripped[0]) <= 32 or stripped[0] in '\x7f\ufeff'):
        stripped = stripped[1:]
    return (("'" + value) if stripped.startswith(('=', '+', '-', '@')) else value).replace('\x00', '\\u0000')


def render_insights_csv(model):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(COLUMNS)
    for row in insights_rows(model):
        writer.writerow([safe_cell(row.get(k)) for k in COLUMNS])
    return '\ufeff' + stream.getvalue()


def render_insights_html(model):
    def escape(value):
        return html.escape(display(value), quote=True)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Maintenance Insights</title>',
             '<style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse;margin:1em 0}th,td{border:1px solid #aaa;padding:.4em;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}</style>',
             '</head><body><h1>Maintenance Insights and Forecasting</h1>']
    cost_keys = {k for k in COLUMNS if k.endswith('_cents')}
    for row in insights_rows(model):
        parts.append('<section><h2>' + escape(row['row_type']) + '</h2><table>')
        keys = [k for k in COLUMNS if k in row]
        if row['row_type'] in ('service_history', 'recent_service'):
            keys += [k for k in ('provider', 'invoice_reference', 'labor_cost_cents', 'parts_cost_cents', 'tax_cents', 'service_total_cost_cents') if k not in keys]
        for key in keys:
            value = row.get(key)
            if key in cost_keys:
                value = format_money(value)
            elif key in ('provider', 'invoice_reference') and value is None:
                value = 'Not recorded'
            parts.append('<tr><th>' + escape(key) + '</th><td>' + escape(value) + '</td></tr>')
        parts.append('</table></section>')
    return ''.join(parts) + '</body></html>\n'


def output_fingerprint(path):
    path = Path(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('Insights output must be a regular file without symlinks or hard links.')
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise ValueError('Output changed; confirm overwrite again.')
        for block in iter(lambda: stream.read(64 * 1024), b''):
            digest.update(block)
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise ValueError('Output changed; confirm overwrite again.')
    if identity(path.lstat()) != identity(before):
        raise ValueError('Output changed; confirm overwrite again.')
    return identity(before), digest.digest()


def _export(snapshot, output_path, renderer, *, overwrite=False, runtime_paths=(), expected_output=_UNSET):
    if not isinstance(snapshot, InsightsSnapshot) or type(overwrite) is not bool:
        raise ValueError('A captured Insights snapshot and boolean overwrite flag are required.')
    output = Path(output_path).absolute()
    parent = parent_identity(output)
    protected = [*snapshot.source_paths, *runtime_paths]
    for directory in (*[p.parent for p in snapshot.source_paths], output.parent, Path(__file__).resolve().parent):
        protected.extend(directory / name for name in NAMES)
    reject_output_alias(output, protected)
    previous = output_fingerprint(output)
    if previous is not None and (not overwrite or expected_output is _UNSET):
        raise ValueError('Existing Insights output requires explicit overwrite confirmation and its fingerprint.')
    if expected_output is not _UNSET and previous != expected_output:
        raise ValueError('Output changed; confirm overwrite again.')
    snapshot.revalidate()
    payload = renderer(snapshot.model).encode('utf-8')
    temporary = None
    try:
        # Resolve once, then check the original parent again before publication.
        with tempfile.NamedTemporaryFile(dir=parent[0], prefix=f'.{output.name}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if parent_identity(output) != parent:
            raise ValueError('Output parent changed; select destination again.')
        reject_output_alias(output, protected)
        if output_fingerprint(output) != previous:
            raise ValueError('Output changed; confirm overwrite again.')
        snapshot.revalidate()
        destination = Path(parent[0]) / output.name
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def export_insights_csv(snapshot, output_path, *, overwrite=False, runtime_paths=(), expected_output=_UNSET):
    return _export(snapshot, output_path, render_insights_csv, overwrite=overwrite,
                   runtime_paths=runtime_paths, expected_output=expected_output)


def export_insights_html(snapshot, output_path, *, overwrite=False, runtime_paths=(), expected_output=_UNSET):
    return _export(snapshot, output_path, render_insights_html, overwrite=overwrite,
                   runtime_paths=runtime_paths, expected_output=expected_output)
