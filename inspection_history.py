"""Validated inspection snapshots and atomic persistence, independent of tkinter."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

from checklist import ALL_ITEMS


def validate_vehicle_details(vehicle_name, mileage, notes=""):
    """Normalize form values or raise ValueError without writing anything."""
    if not isinstance(vehicle_name, str) or not vehicle_name.strip():
        raise ValueError("Vehicle name or unit number is required.")
    if not isinstance(mileage, str) or not mileage.strip() or any(
        char not in "0123456789" for char in mileage.strip()
    ):
        raise ValueError("Mileage must be a nonnegative whole number (digits only).")
    if not isinstance(notes, str):
        raise ValueError("Notes must be text.")
    return vehicle_name.strip(), int(mileage.strip()), notes


def _validate_items(items):
    if not isinstance(items, dict) or set(items) != set(ALL_ITEMS):
        raise ValueError("Inspection must contain all seven checklist items.")
    for item in items.values():
        if (not isinstance(item, dict) or set(item) != {"name", "complete"}
                or not isinstance(item["name"], str)
                or type(item["complete"]) is not bool):
            raise ValueError("Invalid checklist item name or completion status.")


def create_inspection_record(vehicle_name, mileage, notes, checklist_data, *, timestamp=None):
    """Return an independent snapshot with a timezone-aware UTC timestamp."""
    vehicle_name, mileage, notes = validate_vehicle_details(vehicle_name, mileage, notes)
    _validate_items(checklist_data)
    timestamp = datetime.now(timezone.utc) if timestamp is None else timestamp
    if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware.")
    completed = sum(item["complete"] for item in checklist_data.values())
    total = len(checklist_data)
    return {
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "vehicle_name": vehicle_name, "mileage": mileage, "notes": notes,
        "items": deepcopy(checklist_data),
        "progress": {"completed": completed, "total": total,
                     "percentage": completed / total * 100},
    }


def validate_history(history):
    """Reject malformed or unsupported history rather than discarding records."""
    if (not isinstance(history, dict) or set(history) != {"schema_version", "inspections"}
            or type(history["schema_version"]) is not int or history["schema_version"] != 1
            or not isinstance(history["inspections"], list)):
        raise ValueError("Invalid or unsupported inspection history format.")
    for record in history["inspections"]:
        if not isinstance(record, dict) or set(record) != {
            "timestamp", "vehicle_name", "mileage", "notes", "items", "progress"
        }:
            raise ValueError("Invalid inspection record.")
        if type(record["mileage"]) is not int or record["mileage"] < 0:
            raise ValueError("Invalid saved mileage.")
        name, _, _ = validate_vehicle_details(
            record["vehicle_name"], str(record["mileage"]), record["notes"])
        if name != record["vehicle_name"]:
            raise ValueError("Invalid saved vehicle name.")
        try:
            stamp = datetime.fromisoformat(record["timestamp"])
        except (TypeError, ValueError):
            raise ValueError("Invalid saved timestamp.") from None
        if stamp.utcoffset() != timedelta(0):
            raise ValueError("Saved timestamp must be timezone-aware UTC.")
        _validate_items(record["items"])
        expected = create_inspection_record(name, str(record["mileage"]),
                                            record["notes"], record["items"], timestamp=stamp)
        progress = record["progress"]
        if (not isinstance(progress, dict) or set(progress) != {"completed", "total", "percentage"}
                or type(progress["completed"]) is not int or type(progress["total"]) is not int
                or type(progress["percentage"]) not in (int, float)
                or progress != expected["progress"]):
            raise ValueError("Saved progress does not match checklist statuses.")
    return history


def _unique_object(pairs):
    """Do not silently discard duplicate JSON keys in an existing file."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate history key: {key}")
        result[key] = value
    return result


def load_history(history_path):
    """Read history; a missing file is empty and is not created by reading."""
    path = Path(history_path)
    try:
        with path.open(encoding="utf-8") as stream:
            return validate_history(json.load(stream, object_pairs_hook=_unique_object))
    except FileNotFoundError:
        return {"schema_version": 1, "inspections": []}


def append_inspection(history_path, record):
    """Append via a flushed temporary file and atomic same-directory replacement."""
    path = Path(history_path)
    history = load_history(path)
    history["inspections"].append(deepcopy(record))
    validate_history(history)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump(history, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
