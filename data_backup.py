"""Bounded ZIP backups and rollback-protected restore, independent of tkinter.

Individual replacements are atomic. Power loss or failed rollback can prevent a
multi-file transaction; retained recovery paths are included in RestoreError.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import zipfile
import zlib

from inspection_history import validate_history, validate_checklist_snapshot
from vehicle_profiles import validate_profiles
from maintenance_schedule import validate_schedule

NAMES = ('checklist_data.json', 'inspection_history.json', 'vehicle_profiles.json', 'maintenance_schedule.json')
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_MANIFEST = 64 * 1024


@dataclass(frozen=True)
class RuntimePaths:
    checklist: Path
    history: Path
    profiles: Path

    maintenance: Path | None = None

    def mapping(self):
        maintenance = self.maintenance if self.maintenance is not None else Path(self.checklist).with_name("maintenance_schedule.json")
        return dict(zip(NAMES, (Path(self.checklist), Path(self.history), Path(self.profiles), Path(maintenance))))


@dataclass(frozen=True)
class ValidatedBackup:
    created_at: str
    files: tuple[tuple[str, bytes], ...]
    source_path: Path | None = None


@dataclass(frozen=True)
class RestoreResult:
    restored: tuple[str, ...]
    retained: tuple[str, ...]


class BackupValidationError(ValueError):
    pass


class RestoreError(OSError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupValidationError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _json(payload):
    def invalid(value):
        raise BackupValidationError(f'Invalid JSON constant: {value}')
    try:
        return json.loads(payload.decode('utf-8'), object_pairs_hook=_unique, parse_constant=invalid)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BackupValidationError(f'Invalid JSON: {error}') from error


def validate_runtime_bytes(filename: str, payload: bytes) -> None:
    if filename not in NAMES or not isinstance(payload, bytes) or len(payload) > MAX_FILE:
        raise BackupValidationError('Unknown runtime filename or oversized payload.')
    validators = dict(zip(NAMES, (validate_checklist_snapshot, validate_history, validate_profiles, validate_schedule)))
    try:
        validators[filename](_json(payload))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise BackupValidationError(f'{filename}: {error}') from error


def _regular(path):
    path = Path(path)
    # Parent aliases (including macOS /var) are resolved in alias comparisons.
    if path.is_symlink():
        raise BackupValidationError(f'Symlink destination is not allowed: {path}')
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise BackupValidationError(f'Not a regular file: {path}')
    return info


def reject_output_alias(output_path, runtime_paths):
    output = Path(output_path)
    _regular(output)
    for runtime in runtime_paths:
        runtime = Path(runtime)
        _regular(runtime)
        if output.resolve() == runtime.resolve() or (output.exists() and runtime.exists() and os.path.samefile(output, runtime)):
            raise BackupValidationError(f'Output aliases runtime data: {runtime}')


def _paths(paths):
    mapping = paths.mapping()
    for name, path in mapping.items():
        _regular(path)
        reject_output_alias(path, [p for n, p in mapping.items() if n != name])
        if not path.parent.is_dir():
            raise BackupValidationError(f'Destination directory does not exist: {path.parent}')
    return mapping


def _read(path, limit):
    _regular(path)
    with Path(path).open('rb') as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise BackupValidationError(f'Oversized file: {path}')
    return payload


def _sync_dir(directory):
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EBADF):
            raise


def _stage(path, payload, suffix='.tmp'):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.', suffix=suffix, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def write_output(output_path, payload, *, overwrite=False):
    """Publish complete output; callers must explicitly authorize overwrite."""
    path = Path(output_path)
    reject_output_alias(path, [path.parent / name for name in NAMES])
    temporary = _stage(path, payload)
    try:
        # A hard link publishes complete bytes and fails if the destination exists.
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def create_backup(paths: RuntimePaths, output_dir: Path, *, timestamp=None) -> Path:
    mapping = _paths(paths)
    files = []
    for name, path in mapping.items():
        if path.exists():
            payload = _read(path, MAX_FILE)
            validate_runtime_bytes(name, payload)
            files.append((name, payload))
    if not files:
        raise BackupValidationError('Cannot create an empty backup.')
    if sum(len(payload) for _, payload in files) > MAX_TOTAL:
        raise BackupValidationError('Oversized total runtime data.')
    stamp = datetime.now(timezone.utc) if timestamp is None else timestamp
    if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
        raise BackupValidationError('Backup timestamp must be timezone-aware.')
    stamp = stamp.astimezone(timezone.utc)
    manifest = {'manifest_version': 1, 'application': 'vehicle-maintenance-checklist',
                'release': '1.6.0', 'created_at': stamp.isoformat(),
                'files': [{'filename': name, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
                          for name, payload in sorted(files)]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        for name, payload in sorted(files):
            archive.writestr(name, payload)
    output = Path(output_dir) / f"vehicle-checklist-backup-{stamp:%Y%m%dT%H%M%S%fZ}.zip"
    reject_output_alias(output, mapping.values())
    return write_output(output, buffer.getvalue())


def validate_backup(archive_path: Path) -> ValidatedBackup:
    try:
        raw = _read(archive_path, MAX_ARCHIVE)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if not 2 <= len(infos) <= 5 or len(set(names)) != len(names) or 'manifest.json' not in names:
                raise BackupValidationError('Missing manifest, empty backup, or duplicate members.')
            payloads = {}
            for info in infos:
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (info.filename not in (*NAMES, 'manifest.json') or info.orig_filename != info.filename
                        or info.is_dir() or kind not in (0, stat.S_IFREG)
                        or info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise BackupValidationError(f'Unsafe or unsupported member: {info.filename!r}')
                limit = MAX_MANIFEST if info.filename == 'manifest.json' else MAX_FILE
                if info.file_size > limit:
                    raise BackupValidationError('Oversized ZIP member.')
                with archive.open(info) as stream:
                    payload = stream.read(limit + 1)
                if len(payload) != info.file_size or len(payload) > limit:
                    raise BackupValidationError('ZIP size mismatch.')
                payloads[info.filename] = payload
        manifest = _json(payloads.pop('manifest.json'))
        _validate_manifest(manifest, payloads)
        return ValidatedBackup(manifest['created_at'], tuple(sorted(payloads.items())), Path(archive_path))
    except BackupValidationError:
        raise
    except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError, zipfile.BadZipFile, zlib.error) as error:
        raise BackupValidationError(f'Invalid backup: {error}') from error


def _validate_manifest(manifest, payloads):
    if (not isinstance(manifest, dict) or set(manifest) != {'manifest_version', 'application', 'release', 'created_at', 'files'}
            or type(manifest['manifest_version']) is not int or manifest['manifest_version'] != 1
            or manifest['application'] != 'vehicle-maintenance-checklist' or manifest['release'] not in ('1.4.0', '1.5.0', '1.6.0')
            or not isinstance(manifest['files'], list)):
        raise BackupValidationError('Malformed or unsupported manifest.')
    try:
        stamp = datetime.fromisoformat(manifest['created_at'])
        if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
            raise ValueError
    except (TypeError, ValueError):
        raise BackupValidationError('Invalid manifest timestamp.') from None
    if manifest['release'] == '1.4.0' and 'maintenance_schedule.json' in payloads:
        raise BackupValidationError('Maintenance data requires release 1.5.0.')
    if manifest['release'] == '1.5.0' and 'maintenance_schedule.json' in payloads:
        validate_runtime_bytes('maintenance_schedule.json', payloads['maintenance_schedule.json'])
        if _json(payloads['maintenance_schedule.json'])['schema_version'] != 1:
            raise BackupValidationError('Maintenance schema 2 requires release 1.6.0.')
    seen = set()
    for entry in manifest['files']:
        if not isinstance(entry, dict) or set(entry) != {'filename', 'size', 'sha256'}:
            raise BackupValidationError('Malformed manifest entry.')
        name = entry['filename']
        if not isinstance(name, str) or name not in payloads or name in seen:
            raise BackupValidationError('Manifest member mismatch or duplicate.')
        seen.add(name)
        payload = payloads[name]
        if (type(entry['size']) is not int or entry['size'] != len(payload)
                or not isinstance(entry['sha256'], str) or entry['sha256'] != hashlib.sha256(payload).hexdigest()):
            raise BackupValidationError('Manifest size or SHA-256 mismatch.')
        validate_runtime_bytes(name, payload)
    if seen != set(payloads) or not seen or sum(map(len, payloads.values())) > MAX_TOTAL:
        raise BackupValidationError('Manifest membership or total size invalid.')


def _fingerprint(path):
    info = _regular(path)
    if info is None:
        return None
    payload = _read(path, MAX_ARCHIVE)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, hashlib.sha256(payload).digest())


def restore_backup(backup: ValidatedBackup, paths: RuntimePaths, *, confirmed=False) -> RestoreResult:
    if confirmed is not True:
        raise BackupValidationError('Restore requires explicit confirmation.')
    if not isinstance(backup, ValidatedBackup) or not backup.files or len(backup.files) > 4:
        raise BackupValidationError('Invalid or empty backup.')
    payloads = {}
    for name, payload in backup.files:
        if name in payloads:
            raise BackupValidationError('Duplicate restore payload.')
        validate_runtime_bytes(name, payload)
        payloads[name] = payload
    if sum(map(len, payloads.values())) > MAX_TOTAL:
        raise BackupValidationError('Oversized backup.')
    mapping = _paths(paths)
    if backup.source_path is not None:
        reject_output_alias(backup.source_path, mapping.values())
    fingerprints = {name: _fingerprint(path) for name, path in mapping.items()}
    staged, recovery, replaced = {}, {}, []
    keep_recovery = False
    try:
        for name, payload in payloads.items():
            path = mapping[name]
            staged[name] = _stage(path, payload)
            recovery[name] = (_stage(path, _read(path, MAX_ARCHIVE), '.recovery')
                              if fingerprints[name] is not None else None)
        for parent in {mapping[name].parent for name in payloads}:
            _sync_dir(parent)
        _paths(paths)
        if any(_fingerprint(path) != fingerprints[name] for name, path in mapping.items()):
            raise RestoreError('Runtime data changed during restore preparation; nothing replaced.')
        for name in payloads:
            os.replace(staged[name], mapping[name])
            replaced.append(name)
            _sync_dir(mapping[name].parent)
    except BaseException as error:
        failures = []
        for name in reversed(replaced):
            try:
                if recovery[name] is None:
                    mapping[name].unlink()
                else:
                    # Keep recovery bytes even if a later rollback fails.
                    rollback = _stage(mapping[name], recovery[name].read_bytes())
                    try:
                        os.replace(rollback, mapping[name])
                    finally:
                        rollback.unlink(missing_ok=True)
                _sync_dir(mapping[name].parent)
            except BaseException as rollback_error:
                failures.append(f'{mapping[name]}: {rollback_error}')
        if failures:
            keep_recovery = True
            locations = [str(p) for p in recovery.values() if p is not None]
            absent = [str(mapping[n]) + ' (originally absent)' for n in replaced if recovery[n] is None]
            raise RestoreError('Restore FAILED; rollback incomplete: ' + '; '.join(failures)
                               + '. Recovery files: ' + ', '.join(locations + absent)) from error
        raise RestoreError(f'Restore failed; original files preserved: {error}') from error
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        if not keep_recovery:
            for temporary in recovery.values():
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return RestoreResult(tuple(payloads), tuple(name for name in NAMES if name not in payloads))
