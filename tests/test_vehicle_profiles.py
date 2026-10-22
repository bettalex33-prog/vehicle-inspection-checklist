"""Profile persistence tests confined to injected temporary paths."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import vehicle_profiles as profiles


class TestVehicleProfiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "vehicle_profiles.json"

    def create(self):
        return profiles.create_profile(self.path, " Unit ", " 002020 ", " Ford ", " Transit ", "0042")

    def test_missing_read_and_crud_stable_ids(self):
        self.assertEqual(profiles.load_profiles(self.path), {"schema_version": 1, "profiles": []})
        self.assertFalse(self.path.exists())
        original = self.create()
        self.assertEqual(str(UUID(original["profile_id"])), original["profile_id"])
        duplicate_name = self.create()
        self.assertNotEqual(original["profile_id"], duplicate_name["profile_id"])
        updated = profiles.update_profile(self.path, original["profile_id"], "Renamed", "", "", "", "0")
        self.assertEqual(updated, {"profile_id": original["profile_id"], "vehicle_name": "Renamed",
                                   "year": None, "make": "", "model": "", "mileage": 0})
        updated["vehicle_name"] = "caller mutation"
        self.assertEqual(profiles.load_profiles(self.path)["profiles"][0]["vehicle_name"], "Renamed")
        profiles.delete_profile(self.path, original["profile_id"])
        self.assertEqual(profiles.load_profiles(self.path)["profiles"], [duplicate_name])
        before = self.path.read_bytes()
        for action in (lambda: profiles.delete_profile(self.path, original["profile_id"]),
                       lambda: profiles.update_profile(self.path, original["profile_id"], "X", "", "", "", "0")):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(self.path.read_bytes(), before)

    def test_form_validation(self):
        valid = dict(vehicle_name="Unit", year="", make="", model="", mileage="0")
        self.assertEqual(profiles.validate_profile_details(**valid)["year"], None)
        for key, values in {
            "vehicle_name": ("", " \n", None, 1),
            "year": ("-1", "+1", "1.0", "1e3", "１", True, 1, None),
            "mileage": ("", " ", "-1", "+1", "1.0", "1,000", "1e3", "１", True, 1, None),
            "make": (None, 1, True), "model": (None, 1, True),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    profiles.validate_profile_details(**{**valid, key: value})

    def test_corrupt_partial_duplicate_unsupported_never_overwritten(self):
        profile = self.create()
        invalid = ["", "broken", "[]", '{"schema_version":2,"profiles":[]}',
                   '{"schema_version":true,"profiles":[]}',
                   '{"schema_version":1,"profiles":[],"profiles":[]}',
                   json.dumps({"schema_version": 1, "profiles": [profile, profile]})]
        for mutation in (lambda p: p.pop("year"), lambda p: p.update(extra="x"),
                         lambda p: p.update(profile_id="bad"), lambda p: p.update(vehicle_name=" Unit "),
                         lambda p: p.update(year=True), lambda p: p.update(year=-1),
                         lambda p: p.update(mileage=True), lambda p: p.update(mileage=1.0),
                         lambda p: p.update(make=None), lambda p: p.update(model=" X ")):
            malformed = deepcopy(profile)
            mutation(malformed)
            invalid.append(json.dumps({"schema_version": 1, "profiles": [profile, malformed]}))
        invalid.append(json.dumps({"schema_version": 1, "profiles": [profile]}).replace(
            '"make": "Ford"', '"make": "Ford", "make": "Other"'))
        for text in invalid:
            with self.subTest(text=text):
                self.path.write_text(text)
                before = self.path.read_bytes()
                for action in (lambda: profiles.load_profiles(self.path), self.create,
                               lambda: profiles.update_profile(self.path, profile["profile_id"], "X", "", "", "", "0"),
                               lambda: profiles.delete_profile(self.path, profile["profile_id"])):
                    with self.assertRaises(ValueError):
                        action()
                    self.assertEqual(self.path.read_bytes(), before)

    def test_atomic_failures_and_cleanup_for_all_mutations(self):
        original = self.create()
        before = self.path.read_bytes()
        for target in ("json.dump", "os.fsync", "os.replace"):
            for action in (self.create,
                           lambda: profiles.update_profile(self.path, original["profile_id"], "X", "", "", "", "0"),
                           lambda: profiles.delete_profile(self.path, original["profile_id"])):
                with patch("vehicle_profiles." + target, side_effect=OSError("failure")):
                    with self.assertRaises(OSError):
                        action()
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(list(self.directory.iterdir()), [self.path])
        real_replace = profiles.os.replace
        with patch.object(profiles.os, "replace", wraps=real_replace) as replace:
            self.create()
        source, destination = replace.call_args.args
        self.assertEqual(source.parent, self.directory)
        self.assertEqual(destination, self.path)

    def test_first_write_failure_leaves_no_file(self):
        with patch.object(profiles.os, "replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(list(self.directory.iterdir()), [])
