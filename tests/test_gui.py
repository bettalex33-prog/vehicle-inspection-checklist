"""Exercise GUI callbacks and shared persistence without creating a window."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import gui
from checklist import create_default_data, load_data, set_complete


class TestChecklistGUI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_path = Path(temporary.name) / "checklist_data.json"
        self.widgets = {}
        for name in ("Frame", "Checkbutton", "Button", "Label",
                     "BooleanVar", "StringVar"):
            patcher = patch.object(gui.tk, name, side_effect=lambda *a, **k: MagicMock())
            self.widgets[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.root = MagicMock()
        self.app = gui.ChecklistApp(self.root, self.data_path)

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
        self.assertEqual(set(buttons), {"Refresh", "Reset All", "Close"})
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


if __name__ == "__main__":
    unittest.main()
