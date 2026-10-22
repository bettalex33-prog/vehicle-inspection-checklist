"""History validation and persistence tests confined to temporary directories."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from checklist import create_default_data, load_data, reset_items, set_complete
import inspection_history as history


class TestInspectionHistory(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "inspection_history.json"
        self.checklist_path = self.directory / "checklist_data.json"
        self.data = create_default_data()
        self.stamp = datetime(2026, 10, 22, 12, 30, tzinfo=timezone.utc)

    def record(self, **kwargs):
        return history.create_inspection_record(" Unit 12 ", "0042", "Notes\nline 2",
                                                 self.data, findings=history.create_default_findings(), assessment="Ready for service",
                                                 timestamp=kwargs.get("timestamp", self.stamp))

    def test_vehicle_validation(self):
        self.assertEqual(history.validate_vehicle_details(" Unit ", " 000 "), ("Unit", 0, ""))
        self.assertEqual(history.validate_vehicle_details("Unit", "42", "a\nb"), ("Unit", 42, "a\nb"))
        for name in ("", " \n", None, 12):
            with self.subTest(name=name), self.assertRaises(ValueError):
                history.validate_vehicle_details(name, "0")
        for mileage in ("", " ", "-1", "+1", "1.0", "1,000", "1e3", "１２", "1 2", None, 1, True):
            with self.subTest(mileage=mileage), self.assertRaises(ValueError):
                history.validate_vehicle_details("Unit", mileage)
        with self.assertRaises(ValueError):
            history.validate_vehicle_details("Unit", "0", None)

    def test_timestamp_utc_and_injection(self):
        record = self.record(timestamp=self.stamp.astimezone(timezone(timedelta(hours=3))))
        self.assertEqual(record["timestamp"], self.stamp.isoformat())
        generated = history.create_inspection_record("Unit", "0", "", self.data, findings=history.create_default_findings(), assessment="Ready for service")
        stamp = datetime.fromisoformat(generated["timestamp"])
        self.assertEqual(stamp.utcoffset(), timedelta(0))
        self.assertLess(abs((datetime.now(timezone.utc) - stamp).total_seconds()), 5)
        with self.assertRaises(ValueError):
            self.record(timestamp=datetime(2026, 1, 1))

    def test_snapshot_and_progress_all_levels(self):
        for count in range(8):
            data = create_default_data()
            for item_id in list(data)[:count]:
                data[item_id]["complete"] = True
            record = history.create_inspection_record("Unit", "0", "", data, findings=history.create_default_findings(), assessment="Ready for service")
            self.assertEqual(record["items"], data)
            self.assertEqual(record["progress"], {
                "completed": count, "total": 7, "percentage": count / 7 * 100})
            data["oil"]["complete"] = not data["oil"]["complete"]
            self.assertNotEqual(record["items"], data)

    def test_missing_history_read_does_not_create_file(self):
        self.assertEqual(history.load_history(self.path), {"schema_version": 1, "inspections": []})
        self.assertFalse(self.path.exists())

    def test_append_reload_duplicate_and_reset_independence(self):
        set_complete(self.checklist_path, "oil")
        data = load_data(self.checklist_path)
        record = history.create_inspection_record("Unit", "42", "", data, findings=history.create_default_findings(), assessment="Ready for service")
        before = self.checklist_path.read_bytes()
        history.append_inspection(self.path, record)
        history.append_inspection(self.path, record)
        self.assertEqual(self.checklist_path.read_bytes(), before)
        reset_items(self.checklist_path)
        records = history.load_history(self.path)["inspections"]
        self.assertEqual(records, [record, record])
        self.assertTrue(records[0]["items"]["oil"]["complete"])

    def test_invalid_checklists(self):
        for change in (lambda d: d.pop("oil"),
                       lambda d: d["oil"].update(complete=1),
                       lambda d: d["oil"].update(name=None)):
            data = deepcopy(self.data)
            change(data)
            with self.assertRaises(ValueError):
                history.create_inspection_record("Unit", "0", "", data, findings=history.create_default_findings(), assessment="Ready for service")

    def test_corrupt_malformed_unsupported_history_never_overwritten(self):
        valid = {"schema_version": 2, "inspections": [self.record()]}
        invalid = ["broken", "", "[]", '{"schema_version":4,"inspections":[]}',
                   '{"schema_version":2,"schema_version":1,"inspections":[]}',
                   '{"schema_version":true,"inspections":[]}',
                   '{"schema_version":1,"inspections":{}}']
        mutations = [
            lambda r: r.update(mileage=-1), lambda r: r.update(mileage=True),
            lambda r: r.update(vehicle_name=" "), lambda r: r.update(notes=None),
            lambda r: r.update(timestamp="invalid"), lambda r: r.update(timestamp=None),
            lambda r: r.update(timestamp="2026-01-01T00:00:00"),
            lambda r: r.update(timestamp="2026-01-01T00:00:00+03:00"),
            lambda r: r["items"].pop("oil"),
            lambda r: r["items"]["oil"].update(complete=1),
            lambda r: r["progress"].update(completed=True),
            lambda r: r["progress"].update(percentage=99),
            lambda r: r["progress"].update(percentage=float("nan")),
            lambda r: r.pop("notes"), lambda r: r.update(unrecognized="data"),
        ]
        for mutation in mutations:
            malformed = deepcopy(valid)
            mutation(malformed["inspections"][0])
            invalid.append(json.dumps(malformed))
        for text in invalid:
            with self.subTest(text=text):
                self.path.write_text(text)
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    history.load_history(self.path)
                with self.assertRaises(ValueError):
                    history.append_inspection(self.path, self.record())
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_new_record_preserves_history(self):
        history.append_inspection(self.path, self.record())
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            history.append_inspection(self.path, {})
        self.assertEqual(self.path.read_bytes(), before)

    def test_write_and_replace_failure_preserve_history_cleanup(self):
        history.append_inspection(self.path, self.record())
        before = self.path.read_bytes()
        for target in ("json.dump", "os.fsync", "os.replace"):
            with self.subTest(target=target):
                with patch("inspection_history." + target, side_effect=OSError("disk failure")):
                    with self.assertRaises(OSError):
                        history.append_inspection(self.path, self.record())
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_atomic_replacement_uses_same_directory(self):
        real_replace = history.os.replace
        with patch.object(history.os, "replace", wraps=real_replace) as replace:
            history.append_inspection(self.path, self.record())
            source, destination = replace.call_args.args
            self.assertEqual(source.parent, self.path.parent)
            self.assertEqual(destination, self.path)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_first_save_failure_leaves_no_history_or_temporary_file(self):
        with patch.object(history.os, "replace", side_effect=OSError("write blocked")):
            with self.assertRaises(OSError):
                history.append_inspection(self.path, self.record())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_findings_validation_and_independence(self):
        defaults = history.create_default_findings()
        for condition in history.CONDITIONS:
            for assessment in history.ASSESSMENTS:
                defaults["oil"] = {"condition": condition, "comment": "Unicode ✓\nsecond line"}
                record = history.create_inspection_record(
                    "Unit", "0", "", self.data, findings=defaults, assessment=assessment)
                self.assertEqual(record["findings"], defaults)
                self.assertEqual(record["assessment"], assessment)
                self.assertEqual(record["progress"]["completed"], 0)
                record["findings"]["oil"]["comment"] = "changed"
                self.assertNotEqual(record["findings"], defaults)
        fresh = history.create_default_findings()
        self.assertEqual(fresh["oil"], {"condition": "Not inspected", "comment": ""})
        copied = history.validate_findings(fresh)
        copied["oil"]["comment"] = "independent"
        self.assertEqual(fresh["oil"]["comment"], "")
        for assessment in ("", None, True, "unknown", " Ready for service "):
            with self.subTest(assessment=assessment), self.assertRaises(ValueError):
                history.validate_assessment(assessment)
        for mutation in (
            lambda f: f.pop("oil"), lambda f: f.update(extra={}),
            lambda f: f["oil"].update(condition="unknown"),
            lambda f: f["oil"].update(condition=None),
            lambda f: f["oil"].update(comment=None),
            lambda f: f["oil"].pop("comment"),
            lambda f: f["oil"].update(extra="unexpected"),
        ):
            findings = history.create_default_findings()
            mutation(findings)
            with self.assertRaises(ValueError):
                history.validate_findings(findings)
        with self.assertRaises(TypeError):
            history.create_inspection_record("Unit", "0", "", self.data)

    def test_legacy_read_upgrade_and_failed_upgrade(self):
        legacy = self.record()
        legacy.pop("findings")
        legacy.pop("assessment")
        original = {"schema_version": 1, "inspections": [legacy, deepcopy(legacy)]}
        self.path.write_text(json.dumps(original, indent=4))
        before = self.path.read_bytes()
        self.assertEqual(history.load_history(self.path), original)
        self.assertEqual(self.path.read_bytes(), before)
        for target in ("json.dump", "os.fsync", "os.replace"):
            with patch("inspection_history." + target, side_effect=OSError("failure")):
                with self.assertRaises(OSError):
                    history.append_inspection(self.path, self.record())
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(list(self.directory.iterdir()), [self.path])
        with self.assertRaises(ValueError):
            history.append_inspection(self.path, legacy)
        self.assertEqual(self.path.read_bytes(), before)
        new = self.record()
        history.append_inspection(self.path, new)
        history.append_inspection(self.path, new)
        new["findings"]["oil"]["comment"] = "changed after save"
        loaded = history.load_history(self.path)
        self.assertEqual(loaded["schema_version"], 2)
        self.assertEqual(loaded["inspections"][:2], original["inspections"])
        self.assertEqual(loaded["inspections"][2:], [self.record(), self.record()])

    def test_partial_findings_and_schema_mismatch_never_overwritten(self):
        for version in (1, 2):
            for mutation in (
                lambda r: r.pop("assessment"), lambda r: r.pop("findings"),
                lambda r: r["findings"].pop("oil"),
                lambda r: r.update(assessment=""),
                lambda r: r["findings"]["oil"].update(comment=1),
            ):
                record = self.record()
                mutation(record)
                self.path.write_text(json.dumps({"schema_version": version, "inspections": [record]}))
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    history.append_inspection(self.path, self.record())
                self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text(json.dumps({"schema_version": 1, "inspections": [self.record()]}))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            history.append_inspection(self.path, self.record())
        self.assertEqual(self.path.read_bytes(), before)

    def test_profile_snapshots_upgrade_and_independence(self):
        from vehicle_profiles import create_profile, update_profile, delete_profile
        profiles_path = self.directory / "vehicle_profiles.json"
        profile = create_profile(profiles_path, "Unit", "2020", "Ford", "Transit", "42")
        legacy = self.record()
        legacy.pop("findings")
        legacy.pop("assessment")
        for version, records in ((1, [legacy]), (2, [legacy, self.record()])):
            original = {"schema_version": version, "inspections": records}
            self.path.write_text(json.dumps(original, indent=4))
            before = self.path.read_bytes()
            self.assertEqual(history.load_history(self.path), original)
            self.assertEqual(self.path.read_bytes(), before)
            record = history.create_inspection_record(
                "Actual inspection", "99", "notes", self.data,
                findings=history.create_default_findings(), assessment="Ready for service", profile=profile)
            for target in ("json.dump", "os.fsync", "os.replace"):
                with patch("inspection_history." + target, side_effect=OSError("failure")):
                    with self.assertRaises(OSError):
                        history.append_inspection(self.path, record)
                self.assertEqual(self.path.read_bytes(), before)
            history.append_inspection(self.path, record)
            loaded = history.load_history(self.path)
            self.assertEqual(loaded["schema_version"], 3)
            self.assertEqual(loaded["inspections"][:-1], records)
            self.assertEqual(record["profile_id"], profile["profile_id"])
            self.assertEqual(record["profile_snapshot"]["mileage"], 42)
            self.assertEqual((record["vehicle_name"], record["mileage"]), ("Actual inspection", 99))
            saved = self.path.read_bytes()
            update_profile(profiles_path, profile["profile_id"], "Renamed", "", "", "", "100")
            self.assertEqual(self.path.read_bytes(), saved)
            history.append_inspection(self.path, self.record())
            self.assertEqual(history.load_history(self.path)["schema_version"], 3)
        saved = self.path.read_bytes()
        delete_profile(profiles_path, profile["profile_id"])
        self.assertEqual(self.path.read_bytes(), saved)
        profile["make"] = "caller mutation"
        self.assertEqual(record["profile_snapshot"]["make"], "Ford")
        record["profile_snapshot"]["model"] = "caller mutation"
        self.assertEqual(history.load_history(self.path)["inspections"][-2]["profile_snapshot"]["model"], "Transit")

    def test_profile_metadata_rejection_preserves_history(self):
        from vehicle_profiles import create_profile
        profile = create_profile(self.directory / "vehicle_profiles.json", "Unit", "", "", "", "0")
        record = history.create_inspection_record("Unit", "0", "", self.data,
            findings=history.create_default_findings(), assessment="Ready for service", profile=profile)
        for version in (1, 2):
            self.path.write_text(json.dumps({"schema_version": version, "inspections": [record]}))
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                history.append_inspection(self.path, record)
            self.assertEqual(self.path.read_bytes(), before)
        for mutation in (lambda r: r.pop("profile_id"), lambda r: r.pop("profile_snapshot"),
                         lambda r: r.pop("assessment"), lambda r: r.update(profile_id="bad"),
                         lambda r: r.update(profile_snapshot=None),
                         lambda r: r["profile_snapshot"].update(profile_id=profile["profile_id"]),
                         lambda r: r["profile_snapshot"].update(year=True),
                         lambda r: r["profile_snapshot"].pop("make")):
            malformed = deepcopy(record)
            mutation(malformed)
            self.path.write_text(json.dumps({"schema_version": 3, "inspections": [self.record(), malformed]}))
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                history.load_history(self.path)
            with self.assertRaises(ValueError):
                history.append_inspection(self.path, record)
            self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
