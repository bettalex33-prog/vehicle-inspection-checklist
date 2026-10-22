"""Exercise GUI callbacks and shared persistence without creating a window."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import gui
from checklist import create_default_data, load_data, set_complete
from inspection_history import load_history, create_inspection_record, append_inspection, create_default_findings
from datetime import datetime, timezone


class TestChecklistGUI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_path = Path(temporary.name) / "checklist_data.json"
        self.history_path = Path(temporary.name) / "inspection_history.json"
        self.widgets = {}
        for name in ("Frame", "Checkbutton", "Button", "Label",
                     "BooleanVar", "StringVar", "Entry", "Text", "Toplevel"):
            patcher = patch.object(gui.tk, name, side_effect=lambda *a, **k: MagicMock())
            self.widgets[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.root = MagicMock()
        tree_patcher = patch.object(gui.ttk, "Treeview", side_effect=lambda *a, **k: MagicMock())
        self.tree_widget = tree_patcher.start()
        self.addCleanup(tree_patcher.stop)
        for name in ("Combobox", "Scrollbar"):
            patcher = patch.object(gui.ttk, name, side_effect=lambda *a, **k: MagicMock())
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = gui.ChecklistApp(self.root, self.data_path, self.history_path)
        self.app.assessment.get.return_value = "Ready for service"
        self.app.vehicle_name.get.return_value = " Unit 12 "
        self.app.mileage.get.return_value = "42"
        self.app.notes.get.return_value = "Notes\nsecond line"

    def test_seven_items_and_buttons_are_connected(self):
        calls = self.widgets["Checkbutton"].call_args_list
        self.assertEqual([call.kwargs["text"] for call in calls],
                         [item["name"] for item in create_default_data().values()])
        for item_id, call in zip(create_default_data(), calls):
            with patch.object(self.app, "complete_item") as complete:
                call.kwargs["command"]()
                complete.assert_called_once_with(item_id)
        buttons = {call.kwargs["text"]: call.kwargs["command"]
                   for call in self.widgets["Button"].call_args_list}
        self.assertEqual(set(buttons), {"Refresh", "Reset All", "Close", "Save Inspection", "History", "Edit Findings…", "New Profile…", "Edit Profile…", "Delete Profile…", "Reload Profiles"})
        self.assertEqual(buttons["Save Inspection"], self.app.save_inspection)
        self.assertEqual(buttons["History"], self.app.open_history)
        self.assertEqual(buttons["Refresh"], self.app.refresh)
        self.assertEqual(buttons["Reset All"], self.app.reset_all)
        buttons["Close"]()
        self.root.destroy.assert_called_once_with()

    def test_completion_persists_and_stays_checked(self):
        with patch.object(gui, "set_complete", wraps=set_complete) as complete:
            self.app.complete_item("oil")
            complete.assert_called_once_with(self.data_path, "oil")
        self.assertTrue(load_data(self.data_path)["oil"]["complete"])
        self.app.variables["oil"].set.assert_called_with(True)
        self.app.checkboxes["oil"].configure.assert_called_with(state=gui.tk.DISABLED)
        self.app.complete_item("oil")
        self.assertTrue(load_data(self.data_path)["oil"]["complete"])

    def test_refresh_reads_external_changes_and_formats_progress(self):
        set_complete(self.data_path, "oil")
        set_complete(self.data_path, "coolant")
        self.app.refresh()
        self.app.progress.set.assert_called_with("Progress: [██░░░░░] 2/7 (29%)")
        self.app.variables["coolant"].set.assert_called_with(True)
        self.app.checkboxes["tires"].configure.assert_called_with(state=gui.tk.NORMAL)

    def test_reset_cancel_preserves_saved_completion(self):
        set_complete(self.data_path, "oil")
        with patch.object(gui.messagebox, "askyesno", return_value=False) as confirm:
            with patch.object(gui, "reset_items") as reset:
                self.app.reset_all()
                reset.assert_not_called()
            confirm.assert_called_once_with(
                "Reset All", "Clear all completed inspection items?", parent=self.root
            )
        self.assertTrue(load_data(self.data_path)["oil"]["complete"])

    def test_confirmed_reset_clears_and_enables_all_items(self):
        set_complete(self.data_path, "oil")
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            with patch.object(gui, "reset_items", wraps=gui.reset_items) as reset:
                self.app.reset_all()
                reset.assert_called_once_with(self.data_path)
        self.assertTrue(all(not item["complete"]
                            for item in load_data(self.data_path).values()))
        for item_id in create_default_data():
            self.app.variables[item_id].set.assert_called_with(False)
            self.app.checkboxes[item_id].configure.assert_called_with(state=gui.tk.NORMAL)
        self.app.progress.set.assert_called_with("Progress: [░░░░░░░] 0/7 (0%)")

    def test_save_retains_checklist_and_form_at_any_progress(self):
        for count in (0, 2, 7):
            for item_id in list(create_default_data())[:count]:
                set_complete(self.data_path, item_id)
            before = self.data_path.read_bytes()
            with patch.object(gui.messagebox, "showinfo") as info:
                self.app.save_inspection()
                info.assert_called_once()
            self.assertEqual(self.data_path.read_bytes(), before)
            record = load_history(self.history_path)["inspections"][-1]
            self.assertEqual(record["progress"]["completed"], count)
        self.app.vehicle_name.set.assert_not_called()
        self.app.mileage.set.assert_not_called()
        self.app.notes.delete.assert_not_called()

    def test_invalid_input_and_corrupt_history_report_without_changes(self):
        self.history_path.write_text("corrupt")
        before = self.data_path.read_bytes()
        for vehicle, mileage in (("", "42"), ("Unit", "-1"), ("Unit", "42")):
            self.app.vehicle_name.get.return_value = vehicle
            self.app.mileage.get.return_value = mileage
            with patch.object(gui.messagebox, "showerror") as error:
                self.app.save_inspection()
                error.assert_called_once()
            self.assertEqual(self.history_path.read_text(), "corrupt")
            self.assertEqual(self.data_path.read_bytes(), before)

    def test_save_io_failure_reports_error(self):
        with patch.object(gui, "append_inspection", side_effect=OSError("disk full")):
            with patch.object(gui.messagebox, "showerror") as error:
                self.app.save_inspection()
                error.assert_called_once()
        self.assertFalse(self.history_path.exists())

    def test_history_path_defaults_beside_injected_data(self):
        app = gui.ChecklistApp(self.root, self.data_path)
        self.assertEqual(app.history_path, self.history_path)

    def test_open_history_uses_injected_path(self):
        with patch.object(gui, "HistoryWindow") as window:
            self.app.open_history()
            window.assert_called_once_with(self.root, self.history_path)

    def test_history_empty_sorted_details_and_refresh(self):
        window = gui.HistoryWindow(self.root, self.history_path)
        window.details.insert.assert_called_with("1.0", "No saved inspections.")
        for year in (2025, 2026):
            record = create_inspection_record("Unit", "0", "Multiline\nnotes",
                create_default_data(), findings=create_default_findings(), assessment="Ready for service", timestamp=datetime(year, 1, 1, tzinfo=timezone.utc))
            append_inspection(self.history_path, record)
        window.refresh()
        self.assertTrue(window.records[0]["timestamp"].startswith("2026"))
        self.assertEqual(window.tree.insert.call_count, 2)
        window.tree.selection.return_value = ("0",)
        window.show_selected_inspection()
        detail = window.details.insert.call_args.args[1]
        self.assertIn("Multiline\nnotes", detail)
        for item in create_default_data().values():
            self.assertIn("□ " + item["name"], detail)
        window.details.configure.assert_called_with(state=gui.tk.DISABLED)
        self.history_path.write_text("broken")
        with patch.object(gui.messagebox, "showerror") as error:
            window.refresh()
            error.assert_called_once()
        self.assertEqual(len(window.records), 2)

    def test_required_assessment_and_retained_draft(self):
        self.app.assessment.get.return_value = ""
        before = self.data_path.read_bytes()
        with patch.object(gui.messagebox, "showerror") as error:
            self.app.save_inspection()
            error.assert_called_once()
        self.assertFalse(self.history_path.exists())
        self.app.findings["oil"] = {"condition": "Needs attention", "comment": "Review ✓\nsoon"}
        draft = create_default_findings()
        draft["oil"] = self.app.findings["oil"].copy()
        self.app.assessment.get.return_value = "Do not operate"
        self.app.refresh()
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            self.app.reset_all()
        before = self.data_path.read_bytes()
        with patch.object(gui.messagebox, "showinfo"):
            self.app.save_inspection()
        record = load_history(self.history_path)["inspections"][0]
        self.assertEqual(record["findings"], draft)
        self.assertEqual(record["assessment"], "Do not operate")
        self.assertEqual(self.app.findings, draft)
        self.app.assessment.set.assert_not_called()
        self.assertEqual(self.data_path.read_bytes(), before)

    def test_findings_editor_switch_apply_cancel_and_completed_item(self):
        self.app.complete_item("oil")
        before = self.data_path.read_bytes()
        with patch.object(gui, "FindingsWindow") as editor:
            self.app.open_findings()
            editor.assert_called_once_with(self.root, self.app.findings, self.app._apply_findings)
        window = gui.FindingsWindow(self.root, self.app.findings, self.app._apply_findings)
        window.condition.get.return_value = "Needs attention"
        window.comment.get.return_value = "Oil comment\nsecond line"
        window.items.selection.return_value = ("tires",)
        window.select_item()
        self.assertEqual(window.findings["oil"]["condition"], "Needs attention")
        self.assertEqual(self.app.findings["oil"]["condition"], "Not inspected")
        window.condition.get.return_value = "OK"
        window.comment.get.return_value = "Tires comment"
        window.apply()
        self.assertEqual(self.app.findings["oil"]["comment"], "Oil comment\nsecond line")
        self.assertEqual(self.app.findings["tires"]["condition"], "OK")
        window.root.destroy.assert_called_once()
        self.assertEqual(self.data_path.read_bytes(), before)
        cancelled = gui.FindingsWindow(self.root, self.app.findings, self.app._apply_findings)
        cancelled.findings["oil"]["comment"] = "discarded"
        buttons = {call.kwargs["text"]: call.kwargs["command"]
                   for call in self.widgets["Button"].call_args_list}
        buttons["Cancel"]()
        self.assertEqual(self.app.findings["oil"]["comment"], "Oil comment\nsecond line")
        cancelled.root.protocol.assert_called_once_with("WM_DELETE_WINDOW", cancelled.root.destroy)
        cancelled.condition.get.return_value = "invalid"
        cancelled.comment.get.return_value = ""
        with patch.object(gui.messagebox, "showerror") as error:
            cancelled.apply()
            error.assert_called_once()

    def test_history_legacy_and_findings_details(self):
        record = create_inspection_record("Unit", "0", "", create_default_data(),
            findings=create_default_findings(), assessment="Service recommended")
        record["items"]["oil"]["complete"] = True
        record["progress"] = {"completed": 1, "total": 7, "percentage": 1 / 7 * 100}
        record["findings"]["oil"] = {"condition": "Needs attention", "comment": "Check ✓\nagain"}
        import json
        legacy = {key: value for key, value in record.items() if key not in ("findings", "assessment")}
        self.history_path.write_text(json.dumps({"schema_version": 1, "inspections": [legacy]}))
        before = self.history_path.read_bytes()
        window = gui.HistoryWindow(self.root, self.history_path)
        window.tree.selection.return_value = ("0",)
        window.show_selected_inspection()
        self.assertIn("Not recorded (legacy inspection)", window.details.insert.call_args.args[1])
        self.assertEqual(self.history_path.read_bytes(), before)
        append_inspection(self.history_path, record)
        window.refresh()
        window.records = [record]
        window.show_selected_inspection()
        text = window.details.insert.call_args.args[1]
        for expected in ("Service recommended", "Needs attention", "Check ✓\nagain",
                         "Completion: Complete", "Completion: Incomplete", "Not inspected"):
            self.assertIn(expected, text)
        for item in record["items"].values():
            self.assertIn(item["name"], text)

    def make_profile(self):
        from vehicle_profiles import create_profile
        return create_profile(self.app.profiles_path, "Unit", "2020", "Ford", "Transit", "42")

    def test_profile_selection_manual_entry_and_saved_snapshot(self):
        profile = self.make_profile()
        self.assertEqual(self.app.profiles_path, self.data_path.with_name("vehicle_profiles.json"))
        self.assertTrue(self.app.reload_profiles())
        self.app.profile_selector.current.return_value = 1
        self.app.select_profile()
        self.app.vehicle_name.set.assert_called_with("Unit")
        self.app.mileage.set.assert_called_with("42")
        self.app.vehicle_name.get.return_value = "Inspection name"
        self.app.mileage.get.return_value = "99"
        before = self.app.profiles_path.read_bytes()
        with patch.object(gui.messagebox, "showinfo"):
            self.app.save_inspection()
        record = load_history(self.history_path)["inspections"][0]
        self.assertEqual(record["profile_id"], profile["profile_id"])
        self.assertEqual(record["profile_snapshot"]["mileage"], 42)
        self.assertEqual(record["mileage"], 99)
        self.assertEqual(self.app.profiles_path.read_bytes(), before)
        window = gui.HistoryWindow(self.root, self.history_path)
        window.tree.selection.return_value = ("0",)
        window.show_selected_inspection()
        detail = window.details.insert.call_args.args[1]
        for value in (profile["profile_id"], "Ford", "Transit", "2020", "99", "42"):
            self.assertIn(value, detail)
        self.app.vehicle_name.set.reset_mock()
        self.app.mileage.set.reset_mock()
        self.app.profile_selector.current.return_value = 0
        self.app.select_profile()
        self.assertIsNone(self.app.active_profile)
        self.app.vehicle_name.set.assert_not_called()
        self.app.mileage.set.assert_not_called()
        with patch.object(gui.messagebox, "showinfo"):
            self.app.save_inspection()
        self.assertNotIn("profile_id", load_history(self.history_path)["inspections"][-1])

    def test_profile_deletion_confirmation_failure_and_draft_retention(self):
        from vehicle_profiles import load_profiles
        profile = self.make_profile()
        self.app.reload_profiles()
        self.app._apply_profile(profile)
        self.app.vehicle_name.set.reset_mock()
        self.app.mileage.set.reset_mock()
        before = self.app.profiles_path.read_bytes()
        with patch.object(gui.messagebox, "askyesno", return_value=False):
            self.app.delete_profile()
        self.assertEqual(self.app.profiles_path.read_bytes(), before)
        self.assertEqual(self.app.active_profile, profile)
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            with patch.object(gui, "delete_profile", side_effect=OSError("failure")):
                with patch.object(gui.messagebox, "showerror") as error:
                    self.app.delete_profile()
                    error.assert_called_once()
            self.assertEqual(self.app.active_profile, profile)
            self.app.delete_profile()
        self.assertIsNone(self.app.active_profile)
        self.assertEqual(load_profiles(self.app.profiles_path)["profiles"], [])
        self.app.vehicle_name.set.assert_not_called()
        self.app.mileage.set.assert_not_called()
        self.app.notes.delete.assert_not_called()

    def test_profile_editor_create_edit_cancel_errors_and_connections(self):
        from vehicle_profiles import load_profiles
        with patch.object(gui, "ProfileEditor") as editor:
            self.app.new_profile()
            editor.assert_called_once_with(self.root, self.app.profiles_path, self.app._profile_saved)
        window = gui.ProfileEditor(self.root, self.app.profiles_path, self.app._profile_saved)
        values = dict(vehicle_name="Unit", year="", make="", model="", mileage="0")
        for key, value in values.items():
            window.fields[key].get.return_value = value
        window.fields["mileage"].get.return_value = "-1"
        with patch.object(gui.messagebox, "showerror"):
            window.save()
        self.assertFalse(self.app.profiles_path.exists())
        window.root.destroy.assert_not_called()
        window.fields["mileage"].get.return_value = "0"
        window.save()
        profile = self.app.active_profile.copy()
        window.root.destroy.assert_called_once()
        with patch.object(gui, "ProfileEditor") as editor:
            self.app.edit_profile()
            editor.assert_called_once_with(self.root, self.app.profiles_path,
                                          self.app._profile_saved, profile=profile)
        edited = gui.ProfileEditor(self.root, self.app.profiles_path, self.app._profile_saved, profile)
        for key, value in values.items():
            edited.fields[key].get.return_value = value
        edited.fields["vehicle_name"].get.return_value = "Renamed"
        edited.save()
        self.assertEqual(self.app.active_profile["profile_id"], profile["profile_id"])
        self.assertEqual(self.app.active_profile["vehicle_name"], "Renamed")
        before = self.app.profiles_path.read_bytes()
        cancelled = gui.ProfileEditor(self.root, self.app.profiles_path, self.app._profile_saved, profile)
        buttons = {c.kwargs["text"]: c.kwargs["command"] for c in self.widgets["Button"].call_args_list}
        buttons["Cancel"]()
        self.assertEqual(self.app.profiles_path.read_bytes(), before)
        cancelled.root.protocol.assert_called_once_with("WM_DELETE_WINDOW", cancelled.root.destroy)
        self.assertEqual(len(load_profiles(self.app.profiles_path)["profiles"]), 1)

    def test_corrupt_profiles_report_allow_manual_inspection_and_recover(self):
        self.app.profiles_path.write_text("broken")
        with patch.object(gui.messagebox, "showerror") as error:
            self.assertFalse(self.app.reload_profiles())
            error.assert_called_once()
        self.assertFalse(self.app.profiles_available)
        with patch.object(gui, "ProfileEditor") as editor:
            self.app.new_profile()
            self.app.edit_profile()
            editor.assert_not_called()
        with patch.object(gui.messagebox, "showinfo"):
            self.app.save_inspection()
        self.assertNotIn("profile_id", load_history(self.history_path)["inspections"][0])
        self.assertEqual(self.app.profiles_path.read_text(), "broken")
        self.app.profiles_path.write_text('{"schema_version":1,"profiles":[]}')
        self.assertTrue(self.app.reload_profiles())
        self.app.profile_selector.configure.assert_called_with(state="readonly", values=["Manual entry"])

    def test_profile_reload_retains_snapshot_until_reselection_and_clears_removed(self):
        from vehicle_profiles import update_profile, delete_profile
        profile = self.make_profile()
        self.app.reload_profiles()
        self.app._apply_profile(profile)
        self.app.vehicle_name.set.reset_mock()
        self.app.mileage.set.reset_mock()
        update_profile(self.app.profiles_path, profile["profile_id"], "Changed", "", "", "", "100")
        self.app.refresh()
        self.app.reload_profiles()
        self.assertEqual(self.app.active_profile, profile)
        self.app.vehicle_name.set.assert_not_called()
        self.app.mileage.set.assert_not_called()
        self.app.profile_selector.current.return_value = 1
        self.app.select_profile()
        self.assertEqual(self.app.active_profile["mileage"], 100)
        delete_profile(self.app.profiles_path, profile["profile_id"])
        self.app.vehicle_name.set.reset_mock()
        self.app.mileage.set.reset_mock()
        self.app.reload_profiles()
        self.assertIsNone(self.app.active_profile)
        self.app.vehicle_name.set.assert_not_called()
        self.app.mileage.set.assert_not_called()

    def test_explicit_profiles_path_and_editor_io_failure(self):
        path = self.data_path.with_name("injected_profiles.json")
        app = gui.ChecklistApp(self.root, self.data_path, self.history_path, path)
        self.assertEqual(app.profiles_path, path)
        callback = MagicMock()
        window = gui.ProfileEditor(self.root, path, callback)
        for key, value in dict(vehicle_name="Unit", year="", make="", model="", mileage="0").items():
            window.fields[key].get.return_value = value
        with patch.object(gui, "create_profile", side_effect=OSError("disk full")):
            with patch.object(gui.messagebox, "showerror") as error:
                window.save()
                error.assert_called_once()
        callback.assert_not_called()
        window.root.destroy.assert_not_called()
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
