"""Validated vehicle profiles and atomic persistence, independent of tkinter."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4


DETAIL_KEYS = {"vehicle_name", "year", "make", "model", "mileage"}


def _whole_number(value, label, optional=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a nonnegative whole number (digits only).")
    value = value.strip()
    if optional and not value:
        return None
    if not value or any(char not in "0123456789" for char in value):
        raise ValueError(f"{label} must be a nonnegative whole number (digits only).")
    return int(value)


def validate_profile_details(vehicle_name, year, make, model, mileage):
    """Normalize form strings into independent, typed profile details."""
    if not isinstance(vehicle_name, str) or not vehicle_name.strip():
        raise ValueError("Vehicle name or unit number is required.")
    if not isinstance(make, str) or not isinstance(model, str):
        raise ValueError("Make and model must be text.")
    return {"vehicle_name": vehicle_name.strip(),
            "year": _whole_number(year, "Year", optional=True),
            "make": make.strip(), "model": model.strip(),
            "mileage": _whole_number(mileage, "Mileage")}


def validate_profile(profile):
    """Reject malformed persisted profiles; never normalize saved data silently."""
    if not isinstance(profile, dict) or set(profile) != DETAIL_KEYS | {"profile_id"}:
        raise ValueError("Invalid vehicle profile fields.")
    profile_id = profile["profile_id"]
    try:
        if not isinstance(profile_id, str) or str(UUID(profile_id)) != profile_id:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Invalid profile ID.") from None
    if (profile["year"] is not None and
            (type(profile["year"]) is not int or profile["year"] < 0)):
        raise ValueError("Invalid saved year.")
    if type(profile["mileage"]) is not int or profile["mileage"] < 0:
        raise ValueError("Invalid saved mileage.")
    details = validate_profile_details(
        profile["vehicle_name"], "" if profile["year"] is None else str(profile["year"]),
        profile["make"], profile["model"], str(profile["mileage"]))
    if details != {key: profile[key] for key in DETAIL_KEYS}:
        raise ValueError("Saved profile details must be normalized.")
    return deepcopy(profile)


def validate_profiles(data):
    if (not isinstance(data, dict) or set(data) != {"schema_version", "profiles"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or not isinstance(data["profiles"], list)):
        raise ValueError("Invalid or unsupported vehicle profiles format.")
    ids = set()
    for profile in data["profiles"]:
        validate_profile(profile)
        if profile["profile_id"] in ids:
            raise ValueError("Duplicate profile ID.")
        ids.add(profile["profile_id"])
    return deepcopy(data)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate profile key: {key}")
        result[key] = value
    return result


def load_profiles(profiles_path):
    """A missing store is empty; reading never creates or repairs it."""
    try:
        with Path(profiles_path).open(encoding="utf-8") as stream:
            return validate_profiles(json.load(stream, object_pairs_hook=_unique_object))
    except FileNotFoundError:
        return {"schema_version": 1, "profiles": []}


def _write_profiles_atomic(profiles_path, data):
    data = validate_profiles(data)
    path = Path(profiles_path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def create_profile(profiles_path, vehicle_name, year, make, model, mileage):
    data = load_profiles(profiles_path)
    profile = {"profile_id": str(uuid4()),
               **validate_profile_details(vehicle_name, year, make, model, mileage)}
    data["profiles"].append(profile)
    _write_profiles_atomic(profiles_path, data)
    return deepcopy(profile)


def update_profile(profiles_path, profile_id, vehicle_name, year, make, model, mileage):
    data = load_profiles(profiles_path)
    for index, profile in enumerate(data["profiles"]):
        if profile["profile_id"] == profile_id:
            updated = {"profile_id": profile_id,
                       **validate_profile_details(vehicle_name, year, make, model, mileage)}
            data["profiles"][index] = updated
            _write_profiles_atomic(profiles_path, data)
            return deepcopy(updated)
    raise ValueError("Vehicle profile no longer exists. Reload profiles.")


def delete_profile(profiles_path, profile_id):
    data = load_profiles(profiles_path)
    remaining = [p for p in data["profiles"] if p["profile_id"] != profile_id]
    if len(remaining) == len(data["profiles"]):
        raise ValueError("Vehicle profile no longer exists. Reload profiles.")
    data["profiles"] = remaining
    _write_profiles_atomic(profiles_path, data)
