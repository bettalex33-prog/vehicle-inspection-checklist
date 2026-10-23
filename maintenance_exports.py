"""Pure maintenance reports and validated atomic exports, without Tkinter."""
import csv
import html
import io
import os
from pathlib import Path
import tempfile

from data_backup import NAMES, reject_output_alias
from maintenance_schedule import (NOT_RECORDED, SERVICE_KEYS, calculate_due,
                                  format_money, load_schedule, summarize_costs, validate_schedule)


def _display(key, value):
    if key.endswith('_cents'):
        return format_money(value)
    if value is None:
        return NOT_RECORDED
    if type(value) is bool:
        return str(value).lower()
    return str(value)


def _safe_cell(value):
    # Whitespace/control characters must not hide a spreadsheet formula prefix.
    prefix = 0
    while prefix < len(value) and (value[prefix].isspace() or ord(value[prefix]) <= 32
                                 or value[prefix] in '\x7f\ufeff'):
        prefix += 1
    if value[prefix:].startswith(('=', '+', '-', '@')):
        value = "'" + value
    return value.replace('\x00', '\\u0000')


def _report(schedule, today, current_mileages):
    schedule = validate_schedule(schedule)
    mileages = {} if current_mileages is None else current_mileages
    if not isinstance(mileages, dict):
        raise ValueError('Current mileages must map profile IDs to explicitly entered integers.')
    for profile_id, mileage in mileages.items():
        if not isinstance(profile_id, str) or (mileage is not None and (type(mileage) is not int or mileage < 0)):
            raise ValueError('Invalid current mileage.')
    rows = []
    summaries = summarize_costs(schedule)
    for task in sorted(schedule['tasks'], key=lambda t: (t['profile_id'], t['task_id'])):
        due = calculate_due(task, today=today, current_mileage=mileages.get(task['profile_id']))
        base = {'evaluated_on': today.isoformat(), 'current_mileage': mileages.get(task['profile_id'])}
        base.update((key, task[key]) for key in sorted(task) if key not in ('profile_snapshot', 'services'))
        base.update(('snapshot_' + key, value) for key, value in sorted(task['profile_snapshot'].items()))
        base.update(('due_' + key, value) for key, value in sorted(due.items()))
        base.update(('task_' + key, value) for key, value in summaries['by_task'][task['task_id']].items())
        services = sorted(task['services'], key=lambda s: (s['completed_date'], s['mileage'], s['service_id']))
        for service in services or [None]:
            row = dict(base, service_recorded=service is not None)
            row.update(('service_' + key, None if service is None else service.get(key))
                       for key in sorted(SERVICE_KEYS))
            rows.append({key: _display(key, value) for key, value in row.items()})
    return rows, summaries


def render_maintenance_csv(schedule, *, today, current_mileages=None):
    rows, _ = _report(schedule, today, current_mileages)
    columns = sorted(set().union(*(row.keys() for row in rows))) if rows else ['evaluated_on', 'task_id', 'service_recorded']
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_safe_cell(row[key]) for key in columns])
    return '\ufeff' + stream.getvalue()


def render_maintenance_html(schedule, *, today, current_mileages=None):
    rows, summaries = _report(schedule, today, current_mileages)
    def e(value):
        return html.escape(str(value), quote=True)
    def table(values):
        return '<table>' + ''.join('<tr><th>' + e(k) + '</th><td>' + e(_display(k, v)) + '</td></tr>'
                                  for k, v in values.items()) + '</table>'
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Maintenance report</title>',
             '<style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse;margin:1em 0}th,td{border:1px solid #aaa;padding:.4em;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}article{break-inside:avoid}</style>',
             '</head><body><h1>Maintenance and service history</h1>',
             '<p>Evaluated on ' + e(today.isoformat()) + '. Includes archived tasks. Unpriced services are counted separately.</p>',
             '<h2>All history costs</h2>', table(summaries['overall'])]
    for vehicle in summaries['by_vehicle']:
        parts.extend(['<h2>Vehicle snapshot costs</h2>', table({k: v for k, v in vehicle.items() if k != 'profile_snapshot'}),
                      table(vehicle['profile_snapshot'])])
    for row in rows:
        # Rows are already display strings, including formatted currency.
        parts.extend(['<article><h2>Task and service</h2><table>',
                      ''.join('<tr><th>' + e(k) + '</th><td>' + e(v) + '</td></tr>' for k, v in row.items()),
                      '</table></article>'])
    if not rows:
        parts.append('<p>No maintenance tasks.</p>')
    return ''.join(parts) + '</body></html>\n'


def _export(source_path, output_path, render, *, today, current_mileages, overwrite, runtime_paths):
    source, output = Path(source_path), Path(output_path)
    protected = [source, *runtime_paths]
    for directory in (source.parent, output.parent, Path(__file__).resolve().parent):
        protected.extend(directory / name for name in NAMES)
    reject_output_alias(output, protected)
    payload = render(load_schedule(source), today=today, current_mileages=current_mileages).encode('utf-8')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f'.{output.name}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        reject_output_alias(output, protected)
        if overwrite:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def export_maintenance_csv(source_path, output_path, *, today, current_mileages=None, overwrite=False, runtime_paths=()):
    return _export(source_path, output_path, render_maintenance_csv, today=today,
                   current_mileages=current_mileages, overwrite=overwrite, runtime_paths=runtime_paths)


def export_maintenance_html(source_path, output_path, *, today, current_mileages=None, overwrite=False, runtime_paths=()):
    return _export(source_path, output_path, render_maintenance_html, today=today,
                   current_mileages=current_mileages, overwrite=overwrite, runtime_paths=runtime_paths)
