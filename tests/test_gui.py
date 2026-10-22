"""Exercise GUI callbacks and shared persistence without creating a window."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import gui
from checklist import create_default_data, load_data, set_complete
from inspection_history import load_history, create_inspection_record, append_inspection
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
        self.app = gui.ChecklistApp(self.root, self.data_path, self.history_path)
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
        self.assertEqual(set(buttons), {"Refresh", "Reset All", "Close", "Save Inspection", "History"})
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
                create_default_data(), timestamp=datetime(year, 1, 1, tzinfo=timezone.utc))
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


if __name__ == "__main__":
    unittest.main()
