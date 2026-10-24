"""Bounded immutable source capture for Insights; never creates a store."""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import stat

from data_backup import reject_output_alias
from inspection_history import validate_history
from maintenance_schedule import MAX_FILE, validate_schedule
from vehicle_profiles import validate_profiles
from maintenance_insights import build_insights, warning


def parent_identity(path):
    parent = Path(path).parent.resolve(strict=True)
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Source/output parent must be a directory.')
    return str(parent), info.st_dev, info.st_ino


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_source(path):
    path = Path(path)
    parent = parent_identity(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return parent, None, None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('Insights sources must be regular files without symlinks or hard links.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != _identity(before):
            raise ValueError('Source changed while opening; refresh required.')
        raw = stream.read(MAX_FILE + 1)
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise ValueError('Source changed while reading; refresh required.')
    if len(raw) > MAX_FILE:
        raise ValueError('Insights source exceeds 16 MiB.')
    if _identity(path.lstat()) != _identity(before) or parent_identity(path) != parent:
        raise ValueError('Source changed while reading; refresh required.')
    return parent, _identity(before), raw


def decode_source(token, empty, validator):
    if token[2] is None:
        return validator(empty)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate Insights JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Invalid JSON constant: ' + value)
    try:
        data = json.loads(token[2].decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
        stack = [(data, 0)]
        while stack:
            value, depth = stack.pop()
            if depth > 128:
                raise ValueError('Insights source nesting exceeds 128 levels.')
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
            stack.extend((child, depth + 1) for child in children)
        return validator(data)
    except (RecursionError, UnicodeError, TypeError, KeyError, OverflowError) as error:
        raise ValueError('Invalid Insights source: ' + str(error)) from None


@dataclass(frozen=True)
class InsightsSnapshot:
    schedule_path: Path
    profiles_path: Path
    history_path: Path
    tokens: tuple
    as_of: date
    selected_profile_id: str | None = None
    explicit_current_mileage: int | None = None
    cost_start_date: str | None = None
    cost_end_date: str | None = None

    @property
    def source_paths(self):
        return self.schedule_path, self.profiles_path, self.history_path

    def revalidate(self):
        for index, path in enumerate(self.source_paths):
            reject_output_alias(path, self.source_paths[index + 1:])
        if tuple(read_source(p) for p in self.source_paths) != self.tokens:
            raise ValueError('Insights sources changed; refresh before continuing.')

    @property
    def model(self):
        self.revalidate()
        model = build_insights(
            decode_source(self.tokens[0], {'schema_version': 1, 'tasks': []}, validate_schedule),
            decode_source(self.tokens[1], {'schema_version': 1, 'profiles': []}, validate_profiles),
            decode_source(self.tokens[2], {'schema_version': 1, 'inspections': []}, validate_history),
            as_of=self.as_of, selected_profile_id=self.selected_profile_id,
            explicit_current_mileage=self.explicit_current_mileage,
            cost_start_date=self.cost_start_date, cost_end_date=self.cost_end_date)
        for kind, token in zip(('maintenance', 'profiles', 'inspections'), self.tokens):
            model['sources'].append(dict(source_kind=kind, availability='Missing' if token[2] is None else 'Available',
                source_digest=hashlib.sha256(token[2]).hexdigest() if token[2] is not None else None))
            if token[2] is None:
                model['warnings'].append(warning('source_missing', 'Missing store is treated as validated empty data.', source_kind=kind))
        self.revalidate()
        return model


def capture_insights(schedule_path, profiles_path, history_path, *, as_of,
                     selected_profile_id=None, explicit_current_mileage=None,
                     cost_start_date=None, cost_end_date=None):
    if explicit_current_mileage is not None and type(explicit_current_mileage) not in (int, str, bool):
        raise ValueError('Explicit mileage must be scalar integer or form text, never mutable capture state.')
    paths = tuple(Path(p).absolute() for p in (schedule_path, profiles_path, history_path))
    for index, path in enumerate(paths):
        reject_output_alias(path, paths[index + 1:])
    snapshot = InsightsSnapshot(*paths, tuple(read_source(p) for p in paths), as_of,
        selected_profile_id, explicit_current_mileage, cost_start_date, cost_end_date)
    snapshot.model
    return snapshot
