#!/usr/bin/env python3
"""Tkinter interface for the shared vehicle inspection checklist."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from inspection_history import (create_inspection_record, append_inspection, load_history,
                                create_default_findings, validate_findings, CONDITIONS, ASSESSMENTS)

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

    def __init__(self, root, data_path=DATA_PATH, history_path=None):
        self.root = root
        self.data_path = data_path
        self.history_path = (Path(history_path) if history_path is not None else
                             Path(data_path).with_name("inspection_history.json"))
        root.title("Vehicle Inspection Checklist")

        content = tk.Frame(root, padx=20, pady=20)
        content.pack(fill="both", expand=True)
        self.vehicle_name = tk.StringVar(master=root)
        self.mileage = tk.StringVar(master=root)
        for label, variable in (("Vehicle name / unit number", self.vehicle_name),
                                ("Mileage", self.mileage)):
            tk.Label(content, text=label).pack(anchor="w")
            tk.Entry(content, textvariable=variable).pack(fill="x")
        tk.Label(content, text="Notes (optional)").pack(anchor="w")
        self.notes = tk.Text(content, height=3, width=45)
        self.notes.pack(fill="x")
        self.findings = create_default_findings()
        self.assessment = tk.StringVar(master=root, value="")
        tk.Label(content, text="Overall assessment (required; selected by you)").pack(anchor="w")
        ttk.Combobox(content, textvariable=self.assessment, values=ASSESSMENTS,
                     state="readonly", width=30).pack(fill="x")
        tk.Label(content, text="Form values are the current draft; saving retains them.").pack(anchor="w")
        tk.Button(content, text="Edit Findings…", command=self.open_findings).pack(anchor="w")
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
            ("Save Inspection", self.save_inspection),
            ("History", self.open_history),
            ("Refresh", self.refresh),
            ("Reset All", self.reset_all),
            ("Close", root.destroy),
        ):
            tk.Button(buttons, text=label, command=command).pack(
                side="left", padx=4
            )
        self.refresh()

    def save_inspection(self):
        """Save a snapshot without resetting checklist or form fields."""
        try:
            record = create_inspection_record(
                self.vehicle_name.get(), self.mileage.get(),
                self.notes.get("1.0", "end-1c"), load_data(self.data_path),
                findings=self.findings, assessment=self.assessment.get())
            append_inspection(self.history_path, record)
        except (ValueError, OSError) as error:
            messagebox.showerror("Save Inspection", str(error), parent=self.root)
            return
        messagebox.showinfo("Save Inspection", "Inspection saved.", parent=self.root)

    def open_findings(self):
        FindingsWindow(self.root, self.findings, self._apply_findings)

    def _apply_findings(self, findings):
        self.findings = validate_findings(findings)

    def open_history(self):
        HistoryWindow(self.root, self.history_path)

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


class FindingsWindow:
    """Edit a private copy of the draft without changing checkbox completion."""

    def __init__(self, parent, findings, on_apply):
        self.findings = deepcopy(findings)
        self.on_apply = on_apply
        self.current_item = None
        self.item_ids = list(create_default_data())
        self.root = tk.Toplevel(parent)
        self.root.title("Inspection Findings — Current Draft")
        self.root.transient(parent)
        self.root.grab_set()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.items = ttk.Treeview(self.root, show="tree", selectmode="browse", height=7)
        for item_id, item in create_default_data().items():
            self.items.insert("", "end", iid=item_id, text=item["name"])
        self.items.pack(fill="x", padx=12, pady=8)
        self.items.bind("<<TreeviewSelect>>", self.select_item)
        self.condition = tk.StringVar(master=self.root)
        tk.Label(self.root, text="Condition").pack(anchor="w", padx=12)
        ttk.Combobox(self.root, textvariable=self.condition, values=CONDITIONS,
                     state="readonly").pack(fill="x", padx=12)
        tk.Label(self.root, text="Comment (optional)").pack(anchor="w", padx=12)
        self.comment = tk.Text(self.root, height=4, width=45, wrap="word")
        self.comment.pack(fill="both", expand=True, padx=12)
        tk.Button(self.root, text="Apply", command=self.apply).pack(side="left", padx=12, pady=8)
        tk.Button(self.root, text="Cancel", command=self.root.destroy).pack(side="right", padx=12, pady=8)
        self.items.selection_set(self.item_ids[0])
        self._load_item(self.item_ids[0])

    def _load_item(self, item_id):
        self.current_item = item_id
        finding = self.findings[item_id]
        self.condition.set(finding["condition"])
        self.comment.delete("1.0", tk.END)
        self.comment.insert("1.0", finding["comment"])

    def _store_current_item(self):
        if self.current_item is not None:
            self.findings[self.current_item] = {
                "condition": self.condition.get(),
                "comment": self.comment.get("1.0", "end-1c"),
            }

    def select_item(self, event=None):
        selection = self.items.selection()
        if selection and selection[0] != self.current_item:
            self._store_current_item()
            self._load_item(selection[0])

    def apply(self):
        self._store_current_item()
        try:
            findings = validate_findings(self.findings)
        except ValueError as error:
            messagebox.showerror("Inspection Findings", str(error), parent=self.root)
            return
        self.on_apply(findings)
        self.root.destroy()


class HistoryWindow:
    """Read-only saved inspection list with snapshot details."""

    def __init__(self, parent, history_path):
        self.history_path = history_path
        self.root = tk.Toplevel(parent)
        self.root.title("Inspection History")
        self.records = []
        self.tree = ttk.Treeview(self.root, columns=("timestamp", "vehicle", "mileage", "progress"),
                                 show="headings", selectmode="browse")
        for column in self.tree["columns"]:
            self.tree.heading(column, text=column.title())
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_inspection)
        detail_frame = tk.Frame(self.root)
        detail_frame.pack(fill="both", expand=True)
        self.details = tk.Text(detail_frame, height=12, width=70, state=tk.DISABLED, wrap="word")
        scrollbar = ttk.Scrollbar(detail_frame, orient="vertical", command=self.details.yview)
        self.details.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.details.pack(side="left", fill="both", expand=True)
        tk.Button(self.root, text="Refresh", command=self.refresh).pack()
        tk.Button(self.root, text="Close", command=self.root.destroy).pack()
        self.refresh()

    def _set_details(self, text):
        self.details.configure(state=tk.NORMAL)
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state=tk.DISABLED)

    def refresh(self):
        try:
            records = load_history(self.history_path)["inspections"]
        except (ValueError, OSError) as error:
            messagebox.showerror("Inspection History", str(error), parent=self.root)
            return
        self.records = sorted(records, key=lambda record: datetime.fromisoformat(record["timestamp"]),
                              reverse=True)
        for child in self.tree.get_children():
            self.tree.delete(child)
        for index, record in enumerate(self.records):
            progress = record["progress"]
            self.tree.insert("", "end", iid=str(index), values=(
                record["timestamp"], record["vehicle_name"], record["mileage"],
                f"{progress['completed']}/{progress['total']} ({progress['percentage']:.0f}%)"))
        self._set_details("Select an inspection to view details." if self.records
                          else "No saved inspections.")

    def show_selected_inspection(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        record = self.records[int(selection[0])]
        lines = [f"Vehicle: {record['vehicle_name']}", f"Mileage: {record['mileage']}",
                 f"Timestamp: {record['timestamp']}", f"Notes: {record['notes']}", ""]
        legacy = "Not recorded (legacy inspection)"
        lines.append(f"Overall assessment (user-selected): {record.get('assessment', legacy)}")
        for item_id, item in record["items"].items():
            finding = record.get("findings", {}).get(item_id)
            lines.extend((
                f"{'✓' if item['complete'] else '□'} {item['name']}",
                f"Completion: {'Complete' if item['complete'] else 'Incomplete'}",
                f"Condition: {finding['condition'] if finding else legacy}",
                f"Comment: {finding['comment'] if finding else legacy}", ""))
        self._set_details("\n".join(lines))


def main():
    root = tk.Tk()
    ChecklistApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
