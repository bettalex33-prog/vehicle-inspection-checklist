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
                                                 self.data, timestamp=kwargs.get("timestamp", self.stamp))

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
        generated = history.create_inspection_record("Unit", "0", "", self.data)
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
            record = history.create_inspection_record("Unit", "0", "", data)
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
        record = history.create_inspection_record("Unit", "42", "", data)
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
                history.create_inspection_record("Unit", "0", "", data)

    def test_corrupt_malformed_unsupported_history_never_overwritten(self):
        valid = {"schema_version": 1, "inspections": [self.record()]}
        invalid = ["broken", "", "[]", '{"schema_version":2,"inspections":[]}',
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


if __name__ == "__main__":
    unittest.main()
