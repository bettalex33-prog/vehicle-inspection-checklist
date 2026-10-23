"""Deterministic dashboard reports and source-revalidated atomic publication."""
from dataclasses import dataclass
from datetime import date
import csv
import html
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile

from data_backup import NAMES, reject_output_alias
from fleet_dashboard import build_dashboard, STATUSES, BUCKETS
from maintenance_schedule import format_money, MAX_FILE, validate_schedule
from vehicle_profiles import validate_profiles

STATUS_COLUMNS = ('overdue_count', 'due_count', 'due_soon_count', 'upcoming_count',
                  'mileage_needed_count', 'disabled_count', 'archived_count')
COLUMNS = ('row_type', 'evaluated_on', 'scope_profile_id', 'cost_start_date', 'cost_end_date',
           'profile_id', 'vehicle_name', 'profile_missing', 'task_id', 'task_title',
           'status', 'count', 'task_count', *STATUS_COLUMNS,
           'enabled', 'archived', 'current_mileage', 'mileage_unknown', 'date_status', 'mileage_status',
           'next_due_date', 'next_due_mileage', 'remaining_days', 'remaining_miles',
           'scheduled_date', 'appointment_bucket', 'appointment_passed', 'service_count',
           'priced_service_count', 'unpriced_service_count', 'legacy_service_count',
           'total_cost_cents', 'total_cost')
_UNSET = object()


def output_fingerprint(path):
    """Capture the exact file being confirmed, without loading a report into memory."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Dashboard output must not be a symlink.')
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('Dashboard output must be a regular file without hard links.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(64 * 1024), b''):
            digest.update(block)
    after = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_nlink)
    if identity(before) != identity(after):
        raise ValueError('Dashboard output changed; confirm overwrite again.')
    return identity(after), digest.digest()


def _source(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Dashboard source must not be a symlink.')
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Dashboard source must be a regular file without hard links.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError('Dashboard source exceeds 16 MiB.')
    after = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(info) != identity(after):
        raise ValueError('Dashboard source changed; refresh before continuing.')
    return identity(after), raw


def _decode(token, empty, validator):
    if token is None:
        return validator(empty)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate dashboard source key: {key}')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f'Invalid JSON constant: {value}')
    try:
        return validator(json.loads(token[1].decode('utf-8'), object_pairs_hook=unique,
                                    parse_constant=invalid))
    except RecursionError:
        raise ValueError('Dashboard source nesting is too deep.') from None


@dataclass(frozen=True)
class DashboardSnapshot:
    """Captured sources plus immutable evaluation arguments; model is rebuilt fresh."""
    schedule_path: Path
    profiles_path: Path
    schedule_token: tuple | None
    profiles_token: tuple | None
    today: date
    selected_profile_id: str | None = None
    current_mileage: int | None = None
    cost_start_date: str | None = None
    cost_end_date: str | None = None

    @property
    def model(self):
        return build_dashboard(
            _decode(self.schedule_token, {'schema_version': 1, 'tasks': []}, validate_schedule),
            _decode(self.profiles_token, {'schema_version': 1, 'profiles': []}, validate_profiles),
            today=self.today, selected_profile_id=self.selected_profile_id,
            current_mileage=self.current_mileage, cost_start_date=self.cost_start_date,
            cost_end_date=self.cost_end_date)

    def revalidate(self):
        if (_source(self.schedule_path) != self.schedule_token
                or _source(self.profiles_path) != self.profiles_token):
            raise ValueError('Dashboard sources changed; refresh before exporting or navigating.')


def capture_dashboard(schedule_path, profiles_path, *, today, selected_profile_id=None,
                      current_mileage=None, cost_start_date=None, cost_end_date=None):
    schedule_path, profiles_path = Path(schedule_path).absolute(), Path(profiles_path).absolute()
    reject_output_alias(schedule_path, [profiles_path])
    snapshot = DashboardSnapshot(schedule_path, profiles_path, _source(schedule_path),
                                 _source(profiles_path), today, selected_profile_id,
                                 current_mileage, cost_start_date, cost_end_date)
    snapshot.model  # Validate before exposing a successful capture.
    snapshot.revalidate()
    return snapshot


def dashboard_rows(model):
    context = {key: model[key] for key in
               ('evaluated_on', 'scope_profile_id', 'cost_start_date', 'cost_end_date')}
    rows = []
    def add(kind, **values):
        rows.append({**context, 'row_type': kind, **values})
    def costs(values):
        return {**values, 'total_cost': format_money(values['total_cost_cents'])}
    for status in STATUSES:
        add('status_total', status=status, count=model['status_totals'][status],
            current_mileage=model['explicit_current_mileage'])
    for vehicle in model['vehicles']:
        identity = {key: vehicle[key] for key in ('profile_id', 'vehicle_name', 'profile_missing')}
        add('vehicle_summary', **identity, task_count=vehicle['task_count'],
            **dict(zip(STATUS_COLUMNS, (vehicle['status_totals'][s] for s in STATUSES))))
    for row in model['tasks']:
        task = row['task']
        values = {key: row[key] for key in ('profile_id', 'vehicle_name', 'profile_missing',
                  'status', 'current_mileage', 'mileage_unknown', 'date_status', 'mileage_status',
                  'next_due_date', 'next_due_mileage', 'remaining_days', 'remaining_miles',
                  'appointment_bucket', 'appointment_passed')}
        values.update(task_id=task['task_id'], task_title=task['title'], enabled=task['enabled'],
                      archived=task['archived'], scheduled_date=task['scheduled_date'])
        add('task', **values)
    for bucket in BUCKETS:
        for row in model['appointments'][bucket]:
            add('appointment', profile_id=row['profile_id'], vehicle_name=row['vehicle_name'],
                profile_missing=row['profile_missing'], task_id=row['task']['task_id'],
                task_title=row['task']['title'], scheduled_date=row['task']['scheduled_date'],
                appointment_bucket=bucket, appointment_passed=row['appointment_passed'])
    add('cost_overall', **costs(model['costs']))
    for vehicle in model['vehicles']:
        add('cost_vehicle', profile_id=vehicle['profile_id'], vehicle_name=vehicle['vehicle_name'],
            profile_missing=vehicle['profile_missing'], **costs(vehicle['costs']))
    for row in model['tasks']:
        add('cost_task', profile_id=row['profile_id'], vehicle_name=row['vehicle_name'],
            profile_missing=row['profile_missing'], task_id=row['task']['task_id'],
            task_title=row['task']['title'], **costs(row['costs']))
    return rows


def _display(value):
    if value is None:
        return ''
    if type(value) is bool:
        return str(value).lower()
    return str(value)


def _safe_cell(value):
    text = _display(value)
    stripped = text.lstrip(''.join(chr(i) for i in range(33)) + '\x7f\ufeff')
    # Also cover Unicode whitespace before a formula prefix.
    while stripped and (stripped[0].isspace() or ord(stripped[0]) <= 32 or stripped[0] in '\x7f\ufeff'):
        stripped = stripped[1:]
    if stripped.startswith(('=', '+', '-', '@')):
        text = "'" + text
    return text.replace('\x00', '\\u0000')


def render_dashboard_csv(model):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(COLUMNS)
    for row in dashboard_rows(model):
        writer.writerow([_safe_cell(row.get(key)) for key in COLUMNS])
    return '\ufeff' + stream.getvalue()


def render_dashboard_html(model):
    def e(value):
        return html.escape(_display(value), quote=True)
    def table(values):
        return '<table>' + ''.join('<tr><th>' + e(k) + '</th><td>' + e(values[k]) + '</td></tr>'
                                  for k in sorted(values)) + '</table>'
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Fleet dashboard</title>',
             '<style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse;margin:1em 0}th,td{border:1px solid #aaa;padding:.4em;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}article{break-inside:avoid}</style>',
             '</head><body><h1>Fleet dashboard</h1>',
             '<p>Fleet-wide totals. Current mileage is explicitly entered for one selected profile only; never inferred or saved. Cost bounds are inclusive and apply only to cost summaries.</p>',
             table({key: model[key] for key in ('evaluated_on', 'scope_profile_id',
                    'explicit_current_mileage', 'cost_start_date', 'cost_end_date')})]
    for row in dashboard_rows(model):
        parts.append('<section><h2>' + e(row['row_type']) + '</h2>')
        if row.get('task_id'):
            parts.append('<a href="#task-' + e(row['task_id']) + '">Task and complete history</a>')
        parts.extend((table(row), '</section>'))
    for row in model['tasks']:
        task = row['task']
        parts.extend(('<article id="task-' + e(task['task_id']) + '"><h2>' + e(task['title']) + '</h2>',
                      '<h3>Task details</h3>', table({k: task[k] for k in sorted(task)
                          if k not in ('services', 'profile_snapshot')}),
                      '<h3>Captured profile snapshot</h3>', table(task['profile_snapshot']),
                      '<h3>Complete service history (unfiltered)</h3>'))
        for service in task['services']:
            values = dict(service)
            for key in ('provider', 'invoice_reference', 'labor_cost_cents', 'parts_cost_cents',
                        'tax_cents', 'total_cost_cents'):
                value = service.get(key)
                values[key] = format_money(value) if key.endswith('_cents') else (value or 'Not recorded')
            parts.append(table(values))
        if not task['services']:
            parts.append('<p>No recorded services.</p>')
        parts.append('</article>')
    return ''.join(parts) + '</body></html>\n'


def _export(snapshot, output_path, renderer, *, overwrite=False, runtime_paths=(), expected_output=_UNSET):
    if not isinstance(snapshot, DashboardSnapshot) or type(overwrite) is not bool:
        raise ValueError('A captured dashboard and explicit overwrite flag are required.')
    output = Path(output_path)
    protected = [snapshot.schedule_path, snapshot.profiles_path, *runtime_paths]
    for directory in (snapshot.schedule_path.parent, snapshot.profiles_path.parent,
                      output.parent, Path(__file__).resolve().parent):
        protected.extend(directory / name for name in NAMES)
    reject_output_alias(output, protected)
    previous_output = output_fingerprint(output)
    if expected_output is not _UNSET and previous_output != expected_output:
        raise ValueError('Dashboard output changed; confirm overwrite again.')
    snapshot.revalidate()
    payload = renderer(snapshot.model).encode('utf-8')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f'.{output.name}.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        reject_output_alias(output, protected)
        if overwrite and output_fingerprint(output) != previous_output:
            raise ValueError('Dashboard output changed; confirm overwrite again.')
        snapshot.revalidate()
        if overwrite:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def export_dashboard_csv(snapshot, output_path, *, overwrite=False, runtime_paths=(), expected_output=_UNSET):
    return _export(snapshot, output_path, render_dashboard_csv, overwrite=overwrite,
                   runtime_paths=runtime_paths, expected_output=expected_output)


def export_dashboard_html(snapshot, output_path, *, overwrite=False, runtime_paths=(), expected_output=_UNSET):
    return _export(snapshot, output_path, render_dashboard_html, overwrite=overwrite,
                   runtime_paths=runtime_paths, expected_output=expected_output)
