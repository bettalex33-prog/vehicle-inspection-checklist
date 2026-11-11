"""Read-only assessment of one explicit archive against four explicit stores.

assess_backup_health(archive_path, paths=RuntimePaths(...)) also accepts the four
individual *_path keywords. RuntimePaths.maintenance must be explicit; its naming
fallback is intentionally not used. None archive means 'not supplied', never
'never backed up'. No directory search, extraction, clock, or write occurs.

The returned snapshot's model is an owned dictionary. Both model access and
revalidate() check freshness, raising BackupHealthChangedError on changed evidence.
Revalidation is a bounded observation, not a cross-process transaction. Coverage
describes exact recorded bytes, not backup age, domain correctness, or durability.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import zipfile
import zlib

from data_backup import (NAMES, MAX_ARCHIVE, MAX_FILE, MAX_MANIFEST, MAX_TOTAL,
                         RuntimePaths, BackupValidationError, _json, _validate_manifest)

# Backup-format metadata, independent of the Dashboard/UI version. The shared
# pure validator below remains authoritative for release/payload compatibility.
MANIFEST_VERSION = 1
APPLICATION = 'vehicle-maintenance-checklist'
BACKUP_RELEASES = ('1.4.0', '1.5.0', '1.6.0', '1.7.0', '1.8.0', '1.9.0')
STORE_NAMES = tuple(sorted(NAMES))
TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:\+00:00|Z)')


class BackupHealthInputError(ValueError):
    """Ambiguous, incomplete, or aliased explicit inputs."""


class BackupHealthChangedError(ValueError):
    """Evidence changed; request a new assessment before consuming it."""


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _location(path):
    # Observe only ancestors of the supplied path, including missing parents and
    # parent symlink targets. Directory entry mtimes are not file identities.
    parents = []
    resolved = path.parent.resolve(strict=False)
    for parent in dict.fromkeys((*path.parents, resolved, *resolved.parents)):
        try:
            info = parent.lstat()
            identity = (info.st_dev, info.st_ino, info.st_mode)
        except FileNotFoundError:
            identity = None
        parents.append((str(parent), identity))
    return str(resolved), tuple(parents)


def _read(path, limit):
    """Return immutable evidence token and bounded bytes, without following leaves."""
    if path is None:
        return ('Not supplied', 'Unknown', 'not_supplied', None, None, None), None
    location, identity = None, None
    try:
        location = _location(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return ('Missing', 'Unknown', 'path_missing', location, None, None), None
        identity = _identity(info)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return ('Present', 'Unsafe', 'nonregular_symlink_or_hardlink', location, identity, None), None
        if info.st_size > limit:
            return ('Present', 'Oversized', 'size_limit', location, identity, None), None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        try:
            stream = os.fdopen(descriptor, 'rb')
        except BaseException:
            # Ownership transfers only when fdopen returns successfully. Keep
            # the constructor's original exception, even if it closed the fd.
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        with stream:
            if _identity(os.fstat(stream.fileno())) != identity:
                raise BackupHealthChangedError('Input changed while opening.')
            raw = stream.read(limit + 1)
            if _identity(os.fstat(stream.fileno())) != identity:
                raise BackupHealthChangedError('Input changed while reading.')
        if _identity(path.lstat()) != identity or _location(path) != location:
            raise BackupHealthChangedError('Input path or parent changed while reading.')
        if len(raw) > limit or len(raw) != info.st_size:
            raise BackupHealthChangedError('Input size changed while reading.')
        return ('Present', 'Readable', None, location, identity, hashlib.sha256(raw).hexdigest()), raw
    except OSError as error:
        if identity is not None and isinstance(error, FileNotFoundError):
            raise BackupHealthChangedError('Input disappeared while reading.') from error
        return ('Unknown' if identity is None else 'Present', 'Unreadable',
                'os_error_' + str(error.errno), location, identity, None), None


def _reject_aliases(paths):
    seen_paths, seen_files = set(), set()
    for path in paths:
        if path is None:
            continue
        resolved, identity = str(path), None
        try:
            resolved = str(path.resolve(strict=False))
            try:
                info = path.lstat()
                identity = (info.st_dev, info.st_ino)
            except FileNotFoundError:
                identity = None
        except (OSError, RuntimeError) as error:
            if not isinstance(error, OSError) or error.errno not in (errno.EACCES, errno.EPERM):
                raise BackupHealthInputError('Cannot establish input path identity.') from error
            # _read records inaccessible evidence; it must never become a match.
        if resolved in seen_paths or (identity is not None and identity in seen_files):
            raise BackupHealthInputError('Archive/current-store inputs alias one another.')
        seen_paths.add(resolved)
        if identity is not None:
            seen_files.add(identity)


def _unpack(raw, facts):
    """Validate ZIP metadata before bounded direct member reads; never extract."""
    payloads = {}
    try:
        # Bound central-directory allocation before ZipFile creates ZipInfo
        # objects. This format does not require multidisk or ZIP64 archives.
        end_record = raw.rfind(b'PK\x05\x06', max(0, len(raw) - 65557))
        if end_record < 0 or end_record + 22 > len(raw):
            raise zipfile.BadZipFile('Missing end record')
        disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from(
            '<HHHHIIH', raw, end_record + 4)
        if (end_record + 22 + comment != len(raw) or disk or start_disk or disk_count != count
                or offset + size != end_record):
            raise zipfile.BadZipFile('Unsupported or inconsistent central directory')
        if count > len(NAMES) + 1:
            facts.update(zip_valid=True, members_safe=False, zip_reason='duplicate_or_excess_members')
            return None
        cursor = offset
        for _ in range(count):
            if cursor + 46 > end_record or raw[cursor:cursor + 4] != b'PK\x01\x02':
                raise zipfile.BadZipFile('Invalid central directory entry')
            name_size, extra_size, comment_size, member_disk = struct.unpack_from('<HHHH', raw, cursor + 28)
            if member_disk:
                raise zipfile.BadZipFile('Multidisk member')
            cursor += 46 + name_size + extra_size + comment_size
        if cursor != end_record:
            raise zipfile.BadZipFile('Central directory count or length mismatch')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            facts['zip_valid'] = True
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > len(NAMES) + 1 or len(set(names)) != len(names):
                facts.update(members_safe=False, zip_reason='duplicate_or_excess_members')
                return None
            intervals, members, total = [], [], 0
            for info in infos:
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (info.filename not in (*NAMES, 'manifest.json') or info.orig_filename != info.filename
                        or info.is_dir() or mode not in (0, stat.S_IFREG)):
                    facts.update(members_safe=False, zip_reason='unsafe_member')
                    return None
                limit = MAX_MANIFEST if info.filename == 'manifest.json' else MAX_FILE
                total += info.file_size
                if info.file_size > limit or total > MAX_TOTAL + MAX_MANIFEST:
                    facts.update(members_safe=False, zip_reason='member_or_total_size_limit')
                    return None
                if (info.flag_bits & ~0x806 or info.extract_version > 20
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or info.compress_size > len(raw)
                        or (info.file_size and not info.compress_size)
                        or (info.compress_type == zipfile.ZIP_STORED
                            and (info.compress_size != info.file_size or info.flag_bits & 6))):
                    facts.update(members_safe=False, zip_reason='unsafe_compression_metadata')
                    return None
                start = info.header_offset
                if start < 0 or start + 30 > archive.start_dir or raw[start:start + 4] != b'PK\x03\x04':
                    facts.update(members_safe=False, zip_reason='unsafe_local_header')
                    return None
                flags, method = struct.unpack_from('<HH', raw, start + 6)
                crc, compressed, size = struct.unpack_from('<III', raw, start + 14)
                name_size, extra_size = struct.unpack_from('<HH', raw, start + 26)
                data_start = start + 30 + name_size + extra_size
                end = data_start + info.compress_size
                if (flags & 8 or flags != info.flag_bits or method != info.compress_type or end > archive.start_dir
                        or raw[start + 30:start + 30 + name_size] != info.filename.encode('ascii')
                        or (crc, compressed, size) != (info.CRC, info.compress_size, info.file_size)
                        or any(start < previous_end and previous_start < end
                               for previous_start, previous_end in intervals)):
                    facts.update(members_safe=False, zip_reason='inconsistent_or_overlapping_members')
                    return None
                intervals.append((start, end))
                members.append((info, data_start, end))
            facts['members_safe'] = True
            facts['included_stores'] = sorted(name for name in names if name in NAMES)
            for info, data_start, end in members:
                limit = MAX_MANIFEST if info.filename == 'manifest.json' else MAX_FILE
                compressed = raw[data_start:end]
                if info.compress_type == zipfile.ZIP_STORED:
                    payload = compressed
                else:
                    decoder = zlib.decompressobj(-15)
                    payload = decoder.decompress(compressed, limit + 1)
                    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                        raise zipfile.BadZipFile('Incomplete, oversized, or trailing deflate data')
                if (len(payload) != info.file_size or len(payload) > limit
                        or zlib.crc32(payload) & 0xffffffff != info.CRC):
                    raise zipfile.BadZipFile('Member length mismatch')
                payloads[info.filename] = payload
        return payloads
    except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError,
            zipfile.BadZipFile, zlib.error, struct.error):
        facts.update(zip_valid=False, zip_reason='invalid_zip_or_crc')
        return None


def _manifest(payloads, facts):
    checksums = {}
    if 'manifest.json' not in payloads:
        facts.update(manifest_valid=False, manifest_reason='missing_manifest')
        return checksums
    try:
        manifest = _json(payloads['manifest.json'])
    except BackupValidationError:
        facts.update(manifest_valid=False, manifest_reason='invalid_manifest_json')
        return checksums
    if not isinstance(manifest, dict):
        facts.update(manifest_valid=False, manifest_reason='malformed_manifest')
        return checksums
    for field in ('manifest_version', 'application', 'release', 'created_at'):
        value = manifest.get(field)
        facts[field] = value if type(value) in (str, int) else None
    facts['compatible'] = (type(manifest.get('manifest_version')) is int
        and manifest['manifest_version'] == MANIFEST_VERSION
        and manifest.get('application') == APPLICATION and manifest.get('release') in BACKUP_RELEASES)
    facts['compatibility_reason'] = None if facts['compatible'] else 'unsupported_schema_application_or_release'
    try:
        recorded = manifest.get('created_at')
        if not isinstance(recorded, str) or TIMESTAMP.fullmatch(recorded) is None:
            raise ValueError
        calendar = recorded[:-1] if recorded.endswith('Z') else recorded[:-6]
        # Python 3.10 accepts only three or six fractional digits. Parsing a
        # microsecond projection gives every interpreter the same grammar;
        # the report retains the complete original string.
        if '.' in calendar:
            whole, fraction = calendar.split('.')
            calendar = whole + '.' + (fraction + '000000')[:6]
        # Construct components explicitly: newer fromisoformat versions also
        # accept 24:00:00, which is outside this format's clock grammar.
        stamp = datetime(int(calendar[:4]), int(calendar[5:7]), int(calendar[8:10]),
                         int(calendar[11:13]), int(calendar[14:16]), int(calendar[17:19]),
                         int(calendar[20:]) if '.' in calendar else 0, tzinfo=timezone.utc)
        if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
            raise ValueError
        facts['timestamp_valid'] = True
    except ValueError:
        facts.update(timestamp_valid=False, manifest_valid=False, manifest_reason='invalid_recorded_timestamp')
    entries = manifest.get('files')
    if (set(manifest) != {'manifest_version', 'application', 'release', 'created_at', 'files'}
            or not isinstance(entries, list) or not 1 <= len(entries) <= len(NAMES)):
        facts.update(manifest_valid=False, manifest_reason='malformed_manifest')
        return checksums
    stores = {name: raw for name, raw in payloads.items() if name != 'manifest.json'}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'filename', 'size', 'sha256'}:
            facts.update(manifest_valid=False, manifest_reason='malformed_manifest_entry')
            return checksums
        name, size, digest = entry['filename'], entry['size'], entry['sha256']
        if (type(name) is not str or name not in stores or name in checksums
                or type(size) is not int or size < 0 or type(digest) is not str
                or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
            facts.update(manifest_valid=False, manifest_reason='invalid_manifest_membership_or_entry')
            return checksums
        checksums[name] = size == len(stores[name]) and digest == hashlib.sha256(stores[name]).hexdigest()
    if set(checksums) != set(stores) or sum(map(len, stores.values())) > MAX_TOTAL:
        facts.update(manifest_valid=False, manifest_reason='manifest_membership_or_total_size')
        return checksums
    facts.update(manifest_valid=facts['timestamp_valid'], checksum_valid=all(checksums.values()))
    if facts['manifest_valid'] and facts['compatible'] and facts['checksum_valid']:
        try:
            # Shared pure validation preserves v1.9.0 payload/schema rules.
            # Pass the parsed projection in its portable form; displayed
            # created_at remains exactly the recorded manifest string.
            normalized = dict(manifest, created_at=stamp.isoformat())
            _validate_manifest(normalized, stores)
            facts['payload_valid'] = True
        except (BackupValidationError, TypeError, KeyError, OverflowError, RecursionError) as error:
            facts.update(payload_valid=False, validation_reason=str(error))
            if str(error).startswith('Maintenance data requires') or str(error).startswith('Maintenance schema 2 requires'):
                facts.update(compatible=False, compatibility_reason='release_payload_incompatible')
    return checksums


def _report(paths, observations):
    archive_token, raw = observations[0]
    presence, readability, reason = archive_token[:3]
    archive = dict(path=None if paths[0] is None else str(paths[0]), presence=presence,
        readability=readability, read_reason=reason, zip_valid=None, zip_reason=None,
        members_safe=None, manifest_valid=None, manifest_reason=None, compatible=None,
        compatibility_reason=None, manifest_version=None, application=None, release=None,
        created_at=None, timestamp_valid=None, included_stores=[], checksum_valid=None,
        payload_valid=None, validation_reason=None)
    checksums, payloads = {}, None
    if raw is not None:
        payloads = _unpack(raw, archive)
        if payloads is not None:
            checksums = _manifest(payloads, archive)
    valid = (archive['zip_valid'] is True and archive['members_safe'] is True
             and archive['manifest_valid'] is True and archive['compatible'] is True
             and archive['checksum_valid'] is True and archive['payload_valid'] is True)
    stores = []
    for name, (token, current) in zip(STORE_NAMES, observations[1:]):
        included = name in archive['included_stores'] if archive['members_safe'] is True else None
        match = None
        match_reason = 'archive_not_validated'
        if valid:
            if not included:
                match_reason = 'not_archived'
            elif token[0] != 'Present' or token[1] != 'Readable':
                match_reason = 'current_store_missing_or_unreadable'
            else:
                match = payloads[name] == current
                match_reason = 'exact_bytes_match' if match else 'different_bytes'
        stores.append(dict(name=name, path=str(paths[len(stores) + 1]), archived=included,
            archive_checksum_valid=checksums.get(name), current_presence=token[0],
            current_readability=token[1], current_reason=token[2],
            exact_byte_match=match, match_reason=match_reason))
    if presence == 'Not supplied':
        availability, coverage, why = 'Unavailable', 'unknown', 'no_archive_supplied'
    elif presence == 'Missing':
        availability, coverage, why = 'Unavailable', 'unknown', 'archive_missing'
    elif readability in ('Unsafe', 'Oversized'):
        availability, coverage, why = 'Invalid', 'invalid', 'unsafe_or_oversized_archive'
    elif readability != 'Readable':
        availability, coverage, why = 'Unavailable', 'unknown', 'archive_unreadable'
    elif not valid:
        availability, coverage = 'Invalid', 'invalid'
        why = (archive['zip_reason'] or archive['manifest_reason'] or archive['compatibility_reason']
               or ('checksum_mismatch' if archive['checksum_valid'] is False else 'invalid_archived_data'))
    elif any(store['exact_byte_match'] is False for store in stores):
        availability, coverage, why = 'Available', 'changed', 'current_bytes_differ'
    elif len(archive['included_stores']) != len(STORE_NAMES):
        availability, coverage, why = 'Available', 'partial', 'stores_not_archived'
    elif any(store['exact_byte_match'] is None for store in stores):
        availability, coverage, why = 'Unavailable', 'unknown', 'current_store_evidence_missing'
    else:
        availability, coverage, why = 'Available', 'complete', 'all_stores_match'
    return dict(availability=availability, unavailable_reason=None if availability == 'Available' else why,
                coverage=coverage, coverage_reason=why, healthy=coverage == 'complete',
                archive=archive, stores=stores)


@dataclass(frozen=True)
class BackupHealthAssessment:
    """Immutable evidence fingerprints and report; model returns owned fresh data."""
    source_paths: tuple
    fingerprints: tuple
    _report_json: str

    def revalidate(self):
        try:
            _reject_aliases(self.source_paths)
            tokens = tuple(_read(path, MAX_ARCHIVE if index == 0 else MAX_FILE)[0]
                           for index, path in enumerate(self.source_paths))
        except (BackupHealthInputError, OSError) as error:
            raise BackupHealthChangedError('Input identities can no longer be verified.') from error
        if tokens != self.fingerprints:
            raise BackupHealthChangedError('Backup/current-store evidence changed; assess again.')

    @property
    def model(self):
        self.revalidate()
        return json.loads(self._report_json)


def assess_backup_health(archive_path, paths=None, *, checklist_path=None, history_path=None,
                         profiles_path=None, maintenance_path=None):
    """Assess one supplied archive, or None, against exactly four explicit paths.

    Invalid/absent evidence is returned as facts. Ambiguous inputs or aliased
    paths raise BackupHealthInputError. Concurrent changes raise
    BackupHealthChangedError. No absent parent, file, or directory is created.
    """
    explicit = (checklist_path, history_path, profiles_path, maintenance_path)
    if paths is not None:
        if not isinstance(paths, RuntimePaths) or any(p is not None for p in explicit):
            raise BackupHealthInputError('Supply RuntimePaths or four paths, not both.')
        explicit = (paths.checklist, paths.history, paths.profiles, paths.maintenance)
    if any(path is None for path in explicit):
        raise BackupHealthInputError('All four current-store paths must be explicit.')
    by_name = dict(zip(NAMES, explicit))
    sources = (None if archive_path is None else Path(archive_path).absolute(),
               *(Path(by_name[name]).absolute() for name in STORE_NAMES))
    _reject_aliases(sources)
    observations = tuple(_read(path, MAX_ARCHIVE if index == 0 else MAX_FILE)
                         for index, path in enumerate(sources))
    assessment = BackupHealthAssessment(sources, tuple(token for token, raw in observations),
                                        json.dumps(_report(sources, observations)))
    assessment.revalidate()
    return assessment
