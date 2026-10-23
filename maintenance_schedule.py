"""Strict maintenance schedules, pure due calculations, and atomic persistence."""
from copy import deepcopy
from datetime import date, timedelta
import json
import os
from pathlib import Path
import stat
import tempfile
from uuid import UUID, uuid4

from vehicle_profiles import load_profiles, validate_profile

EDIT_KEYS = {'title', 'notes', 'enabled', 'interval_days', 'interval_miles',
             'warning_days', 'warning_miles', 'baseline_date', 'baseline_mileage', 'scheduled_date'}
TASK_KEYS = EDIT_KEYS | {'task_id', 'profile_id', 'profile_snapshot', 'archived', 'services'}
MAX_FILE = 16 * 1024 * 1024


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Invalid canonical UUID.')


def _date(value):
    if not isinstance(value, str):
        raise ValueError('Date must use YYYY-MM-DD.')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Date must use YYYY-MM-DD.')
    return parsed


def _number(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label} must be a whole number >= {minimum}.')
    return value


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or '\x00' in value or len(value) > limit:
        raise ValueError(f'{label} must be text without NUL, at most {limit} characters.')
    if required and (not value.strip() or value != value.strip()):
        raise ValueError(f'{label} must be nonempty and trimmed.')


def _anchor(task):
    if task['services']:
        last = task['services'][-1]
        return last['completed_date'], last['mileage']
    return task['baseline_date'], task['baseline_mileage']


def validate_task(task):
    if not isinstance(task, dict) or set(task) != TASK_KEYS:
        raise ValueError('Invalid maintenance task fields.')
    _uuid(task['task_id']); _uuid(task['profile_id'])
    snapshot = task['profile_snapshot']
    if not isinstance(snapshot, dict) or 'profile_id' in snapshot:
        raise ValueError('Invalid profile snapshot.')
    validate_profile({'profile_id': task['profile_id'], **snapshot})
    _text(task['title'], 'Title', 200, True)
    _text(task['notes'], 'Notes', 10000)
    if type(task['enabled']) is not bool or type(task['archived']) is not bool:
        raise ValueError('Enabled and archived must be booleans.')
    if task['archived'] and task['enabled']:
        raise ValueError('Archived tasks must be disabled.')
    if task['interval_days'] is None and task['interval_miles'] is None:
        raise ValueError('Configure at least one interval.')
    for interval, warning, baseline in (('interval_days', 'warning_days', 'baseline_date'),
                                       ('interval_miles', 'warning_miles', 'baseline_mileage')):
        if task[interval] is None:
            if task[warning] is not None:
                raise ValueError('Unused interval warnings must be null.')
        else:
            _number(task[interval], interval, 1)
            _number(task[warning], warning)
            if task[warning] > task[interval] or task[baseline] is None:
                raise ValueError('Warning exceeds interval or baseline is missing.')
    if task['baseline_date'] is not None:
        _date(task['baseline_date'])
    if task['baseline_mileage'] is not None:
        _number(task['baseline_mileage'], 'Baseline mileage')
    if task['scheduled_date'] is not None:
        _date(task['scheduled_date'])
    if not isinstance(task['services'], list):
        raise ValueError('Services must be a list.')
    previous_date, previous_mileage = task['baseline_date'], task['baseline_mileage']
    ids = set()
    for service in task['services']:
        if not isinstance(service, dict) or set(service) != {'service_id', 'completed_date', 'mileage', 'notes'}:
            raise ValueError('Invalid service fields.')
        _uuid(service['service_id'])
        if service['service_id'] in ids:
            raise ValueError('Duplicate service ID.')
        ids.add(service['service_id'])
        _date(service['completed_date']); _number(service['mileage'], 'Service mileage')
        _text(service['notes'], 'Service notes', 10000)
        if ((previous_date is not None and service['completed_date'] < previous_date)
                or (previous_mileage is not None and service['mileage'] < previous_mileage)):
            raise ValueError('Services must have nondecreasing dates and mileage.')
        previous_date, previous_mileage = service['completed_date'], service['mileage']
    anchor_date, _ = _anchor(task)
    if task['interval_days'] is not None:
        try:
            _date(anchor_date) + timedelta(days=task['interval_days'])
        except OverflowError:
            raise ValueError('Date interval exceeds the supported calendar.') from None
    return deepcopy(task)


def validate_schedule(data):
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'tasks'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or not isinstance(data['tasks'], list)):
        raise ValueError('Invalid or unsupported maintenance schedule.')
    task_ids, service_ids = set(), set()
    for task in data['tasks']:
        validate_task(task)
        if task['task_id'] in task_ids:
            raise ValueError('Duplicate task ID.')
        task_ids.add(task['task_id'])
        for service in task['services']:
            if service['service_id'] in service_ids:
                raise ValueError('Duplicate service ID.')
            service_ids.add(service['service_id'])
    return deepcopy(data)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate maintenance key: {key}')
        result[key] = value
    return result


def _invalid(value):
    raise ValueError(f'Invalid JSON constant: {value}')


def _read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Maintenance store cannot be a symlink.')
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Maintenance store must be a regular file without hard links.')
    with path.open('rb') as stream:
        payload = stream.read(MAX_FILE + 1)
    if len(payload) > MAX_FILE:
        raise ValueError('Maintenance store exceeds 16 MiB.')
    return payload


def _decode(payload):
    if payload is None:
        return {'schema_version': 1, 'tasks': []}
    try:
        return validate_schedule(json.loads(payload.decode('utf-8'), object_pairs_hook=_unique,
                                            parse_constant=_invalid))
    except RecursionError:
        raise ValueError('Maintenance JSON nesting is too deep.') from None


def load_schedule(path):
    return _decode(_read(path))


def _write_schedule_atomic(path, data, *, expected_bytes):
    path = Path(path)
    payload = (json.dumps(validate_schedule(data), indent=2, ensure_ascii=False,
                          allow_nan=False) + '\n').encode('utf-8')
    if len(payload) > MAX_FILE:
        raise ValueError('Maintenance store exceeds 16 MiB.')
    # Lazy import avoids a validator import cycle.
    from data_backup import reject_output_alias
    reject_output_alias(path, [path.parent / name for name in
                        ('checklist_data.json', 'inspection_history.json', 'vehicle_profiles.json')])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path) != expected_bytes:
            raise ValueError('Maintenance data changed; refresh before saving.')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _form_number(value, label):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not value.strip() or any(c not in '0123456789' for c in value.strip()):
        raise ValueError(f'{label} must contain digits only.')
    return int(value.strip())


def validate_task_details(*, today, **values):
    if set(values) != EDIT_KEYS:
        raise ValueError('Invalid task form fields.')
    result = dict(values)
    if not isinstance(result['title'], str):
        raise ValueError('Title must be text.')
    result['title'] = result['title'].strip()
    for key in ('interval_days', 'interval_miles', 'warning_days', 'warning_miles', 'baseline_mileage'):
        result[key] = _form_number(result[key], key)
    for key in ('baseline_date', 'scheduled_date'):
        value = result[key]
        if value is not None and not isinstance(value, str):
            raise ValueError('Date must be text.')
        result[key] = value.strip() if value and value.strip() else None
        if result[key] is not None:
            parsed = _date(result[key])
            if key == 'baseline_date' and parsed > today:
                raise ValueError('Baseline cannot be in the future.')
    return result


def validate_service_details(completed_date, mileage, notes, *, today):
    if not isinstance(completed_date, str) or _date(completed_date.strip()) > today:
        raise ValueError('Service date cannot be in the future.')
    number = _form_number(mileage, 'Mileage')
    _number(number, 'Mileage'); _text(notes, 'Service notes', 10000)
    return {'completed_date': completed_date.strip(), 'mileage': number, 'notes': notes}


def _profile(profiles_path, profile_id):
    for profile in load_profiles(profiles_path)['profiles']:
        if profile['profile_id'] == profile_id:
            return profile
    raise ValueError('Vehicle profile no longer exists. Reload profiles.')


def create_task(path, profile_id, details, *, profiles_path, today):
    if not isinstance(details, dict) or set(details) != EDIT_KEYS:
        raise ValueError('Invalid editable task fields.')
    original = _read(path); data = _decode(original)
    profile = deepcopy(_profile(profiles_path, profile_id)); profile.pop('profile_id')
    task = {**deepcopy(details), 'task_id': str(uuid4()), 'profile_id': profile_id,
            'profile_snapshot': profile, 'archived': False, 'services': []}
    _check_dates(task, None, today)
    validate_task(task)
    data['tasks'].append(task)
    _write_schedule_atomic(path, data, expected_bytes=original)
    return deepcopy(task)


def _check_dates(task, old, today):
    for key in ('baseline_date', 'scheduled_date'):
        if task[key] is None or (old is not None and task[key] == old[key]):
            continue
        parsed = _date(task[key])
        if key == 'baseline_date' and parsed > today:
            raise ValueError('Baseline cannot be in the future.')
        if key == 'scheduled_date' and parsed < today:
            raise ValueError('New appointments cannot be in the past.')


def _mutate(path, task_id, expected_task, change):
    original = _read(path); data = _decode(original)
    for task in data['tasks']:
        if task['task_id'] == task_id:
            if task != expected_task:
                raise ValueError('Task changed; refresh before saving.')
            result = change(task, data)
            _write_schedule_atomic(path, data, expected_bytes=original)
            return deepcopy(result)
    raise ValueError('Task no longer exists. Refresh maintenance.')


def update_task(path, task_id, details, *, expected_task, today):
    if not isinstance(details, dict) or set(details) != EDIT_KEYS:
        raise ValueError('Invalid editable task fields.')
    def change(task, data):
        if task['archived']:
            raise ValueError('Archived tasks are read-only.')
        if task['services'] and any(details[k] != task[k] for k in ('baseline_date', 'baseline_mileage')):
            raise ValueError('Baseline cannot change after recording service.')
        updated = {**task, **deepcopy(details)}
        _check_dates(updated, task, today)
        task.update(updated)
        return task
    return _mutate(path, task_id, expected_task, change)


def delete_task(path, task_id, *, expected_task):
    def change(task, data):
        if task['services']:
            raise ValueError('Tasks with services must be archived, never deleted.')
        data['tasks'].remove(task)
    return _mutate(path, task_id, expected_task, change)


def archive_task(path, task_id, *, expected_task):
    def change(task, data):
        task['archived'] = True
        task['enabled'] = False
        return task
    return _mutate(path, task_id, expected_task, change)


def record_service(path, task_id, details, *, profiles_path, expected_task, today):
    def change(task, data):
        _profile(profiles_path, task['profile_id'])
        if task['archived'] or not task['enabled']:
            raise ValueError('Enable an active task before recording service.')
        if not isinstance(details, dict) or set(details) != {'completed_date', 'mileage', 'notes'}:
            raise ValueError('Invalid service details.')
        if _date(details['completed_date']) > today:
            raise ValueError('Service cannot be in the future.')
        task['services'].append({'service_id': str(uuid4()), **deepcopy(details)})
        task['scheduled_date'] = None
        return task
    return _mutate(path, task_id, expected_task, change)


def calculate_due(task, *, today, current_mileage):
    task = validate_task(task)
    anchor_date, anchor_mileage = _anchor(task)
    if current_mileage is not None:
        _number(current_mileage, 'Current mileage')
        if anchor_mileage is not None and current_mileage < anchor_mileage:
            raise ValueError('Current mileage is below the latest service baseline.')
    next_date = (_date(anchor_date) + timedelta(days=task['interval_days'])
                 if task['interval_days'] is not None else None)
    next_mileage = (anchor_mileage + task['interval_miles']
                    if task['interval_miles'] is not None else None)
    days = (next_date - today).days if next_date else None
    miles = next_mileage - current_mileage if next_mileage is not None and current_mileage is not None else None
    def status(remaining, warning):
        if remaining is None:
            return None
        return 'Overdue' if remaining < 0 else 'Due' if remaining == 0 else 'Due soon' if remaining <= warning else 'Upcoming'
    date_status = status(days, task['warning_days'])
    mileage_status = status(miles, task['warning_miles'])
    unknown = next_mileage is not None and current_mileage is None
    result = 'Mileage needed' if unknown else 'Upcoming'
    for candidate in ('Due soon', 'Due', 'Overdue'):
        if candidate in (date_status, mileage_status):
            result = candidate
    if task['archived']:
        result = 'Archived'
    elif not task['enabled']:
        result = 'Disabled'
    return {'status': result, 'date_status': date_status, 'mileage_status': mileage_status,
            'mileage_unknown': unknown, 'next_due_date': next_date.isoformat() if next_date else None,
            'next_due_mileage': next_mileage, 'remaining_days': days, 'remaining_miles': miles,
            'appointment_passed': task['scheduled_date'] is not None and _date(task['scheduled_date']) < today}
