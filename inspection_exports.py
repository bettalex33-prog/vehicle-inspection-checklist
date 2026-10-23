"""Validated, read-only inspection exports; no tkinter dependencies."""
import csv
import html
import io
from pathlib import Path

from checklist import ALL_ITEMS
from inspection_history import load_history, validate_history, validate_inspection_record

LEGACY = "Not recorded (legacy inspection)"


def render_inspection_html(record: dict, *, schema_version: int) -> str:
    record = validate_inspection_record(record, schema_version=schema_version)
    def e(value):
        return html.escape(str(value), quote=True)
    def field(label, value):
        return f"<p><strong>{e(label)}:</strong> <span>{e(value)}</span></p>"
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<title>Inspection report</title><style>body{font-family:sans-serif;max-width:70em;margin:2em auto}span,td{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td,th{border:1px solid #777;padding:.4em;text-align:left}@media print{body{margin:0}tr{break-inside:avoid}}</style></head><body><h1>Inspection report</h1>']
    for key in ('timestamp', 'vehicle_name', 'mileage'):
        parts.append(field(key.replace('_', ' ').title(), record[key]))
    p = record['progress']
    parts.append(field('Progress', f"{p['completed']}/{p['total']} ({p['percentage']:.0f}%)"))
    parts.append('<h2>Saved profile snapshot</h2>')
    if 'profile_id' in record:
        parts.append(field('Profile ID', record['profile_id']))
        for key in ('vehicle_name', 'year', 'make', 'model', 'mileage'):
            value = record['profile_snapshot'][key]
            parts.append(field(key.replace('_', ' ').title(), 'Not specified' if value is None else value))
    else:
        parts.append(field('Profile', 'Profile association/snapshot not recorded'))
    parts.append('<h2>Checklist and findings</h2><table><thead><tr><th>ID</th><th>Item</th><th>Completion</th><th>Condition</th><th>Comment</th></tr></thead><tbody>')
    for item_id in ALL_ITEMS:
        item = record['items'][item_id]
        finding = record.get('findings', {}).get(item_id, {})
        values = (item_id, item['name'], 'Complete' if item['complete'] else 'Incomplete',
                  finding.get('condition', LEGACY), finding.get('comment', LEGACY))
        parts.append('<tr>' + ''.join(f'<td>{e(v)}</td>' for v in values) + '</tr>')
    parts.append('</tbody></table>')
    parts.append(field('Overall assessment (user-selected)', record.get('assessment', LEGACY)))
    parts.append(field('Notes', record['notes']))
    return ''.join(parts) + '</body></html>\n'


def _safe_text(value):
    if isinstance(value, str):
        prefix = 0
        while prefix < len(value) and (value[prefix].isspace() or ord(value[prefix]) <= 32
                                       or value[prefix] in "\x7f\ufeff"):
            prefix += 1
        if value[prefix:].startswith(("=", "+", "-", "@")):
            value = "'" + value
        # Python 3.10 csv rejects NUL; make it visible without losing other text.
        return value.replace("\x00", "\\u0000")
    return value


def render_history_csv(history: dict) -> str:
    validate_history(history)
    columns = ['history_schema_version', 'history_record_number', 'timestamp', 'vehicle_name',
               'mileage', 'completed', 'total', 'percentage', 'findings_recorded', 'assessment',
               'notes', 'profile_recorded', 'profile_id', 'profile_vehicle_name', 'profile_year',
               'profile_make', 'profile_model', 'profile_mileage']
    columns += [f'{key}_{suffix}' for key in ALL_ITEMS for suffix in ('name', 'complete', 'condition', 'comment')]
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(columns)
    for index, record in enumerate(history['inspections'], 1):
        p = record['progress']
        snapshot = record.get('profile_snapshot', {})
        row = [history['schema_version'], index, record['timestamp'], record['vehicle_name'],
               record['mileage'], p['completed'], p['total'], p['percentage'],
               str('findings' in record).lower(), record.get('assessment', LEGACY), record['notes'],
               str('profile_id' in record).lower(), record.get('profile_id', '')]
        row += [snapshot.get(k, '') if snapshot.get(k) is not None else ''
                for k in ('vehicle_name', 'year', 'make', 'model', 'mileage')]
        for key in ALL_ITEMS:
            item = record['items'][key]
            finding = record.get('findings', {}).get(key, {})
            row += [item['name'], str(item['complete']).lower(), finding.get('condition', LEGACY),
                    finding.get('comment', LEGACY)]
        writer.writerow([_safe_text(v) for v in row])
    return '\ufeff' + stream.getvalue()


def export_inspection_html(record: dict, output_path: Path, *, schema_version: int, overwrite=False) -> Path:
    from data_backup import write_output
    return write_output(output_path, render_inspection_html(record, schema_version=schema_version).encode('utf-8'), overwrite=overwrite)


def export_history_csv(history_path: Path, output_path: Path, *, overwrite=False) -> Path:
    from data_backup import write_output, reject_output_alias
    reject_output_alias(output_path, (history_path,))
    return write_output(output_path, render_history_csv(load_history(history_path)).encode('utf-8'), overwrite=overwrite)
