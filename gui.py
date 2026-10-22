#!/usr/bin/env python3
"""Tkinter interface for the shared vehicle inspection checklist."""

from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from checklist import (
    create_default_data,
    load_data,
    set_complete,
    reset_items,
    calculate_progress,
)


DATA_PATH = Path(__file__).resolve().parent / "checklist_data.json"


class ChecklistApp:
    """Display checklist state and delegate changes to the checklist module."""

    def __init__(self, root, data_path=DATA_PATH):
        self.root = root
        self.data_path = data_path
        root.title("Vehicle Inspection Checklist")

        content = tk.Frame(root, padx=20, pady=20)
        content.pack(fill="both", expand=True)
        self.variables = {}
        self.checkboxes = {}
        for item_id, item in create_default_data().items():
            variable = tk.BooleanVar(master=root)
            checkbox = tk.Checkbutton(
                content,
                text=item["name"],
                variable=variable,
                command=lambda item_id=item_id: self.complete_item(item_id),
            )
            checkbox.pack(anchor="w", pady=3)
            self.variables[item_id] = variable
            self.checkboxes[item_id] = checkbox

        self.progress = tk.StringVar(master=root)
        tk.Label(content, textvariable=self.progress).pack(pady=(12, 8))
        buttons = tk.Frame(content)
        buttons.pack()
        for label, command in (
            ("Refresh", self.refresh),
            ("Reset All", self.reset_all),
            ("Close", root.destroy),
        ):
            tk.Button(buttons, text=label, command=command).pack(
                side="left", padx=4
            )
        self.refresh()

    def refresh(self):
        """Reload shared state, including changes made through the CLI."""
        data = load_data(self.data_path)
        for item_id, variable in self.variables.items():
            complete = data[item_id]["complete"]
            variable.set(complete)
            self.checkboxes[item_id].configure(
                state=tk.DISABLED if complete else tk.NORMAL
            )
        _, bar, progress_text = calculate_progress(data)
        self.progress.set(f"Progress: [{bar}] {progress_text}")

    def complete_item(self, item_id):
        """Persist completion and restore the checkbox from saved state."""
        set_complete(self.data_path, item_id)
        self.refresh()

    def reset_all(self):
        """Clear completion only after the user confirms."""
        if messagebox.askyesno(
            "Reset All", "Clear all completed inspection items?", parent=self.root
        ):
            reset_items(self.data_path)
            self.refresh()


def main():
    root = tk.Tk()
    ChecklistApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
