#!/usr/bin/env python3
"""Tkinter interface for the shared vehicle inspection checklist."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, filedialog
from tkinter import ttk

from inspection_history import (create_inspection_record, append_inspection, load_history,
                                create_default_findings, validate_findings, CONDITIONS, ASSESSMENTS)

from data_backup import (RuntimePaths, create_backup, validate_backup, restore_backup,
                         reject_output_alias)
from inspection_exports import export_inspection_html, export_history_csv
from maintenance_gui import MaintenanceWindow

from vehicle_profiles import load_profiles, create_profile, update_profile, delete_profile

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

    def __init__(self, root, data_path=DATA_PATH, history_path=None, profiles_path=None, maintenance_path=None):
        self.root = root
        self.data_path = data_path
        self.history_path = (Path(history_path) if history_path is not None else
                             Path(data_path).with_name("inspection_history.json"))
        self.profiles_path = (Path(profiles_path) if profiles_path is not None else
                              Path(data_path).with_name("vehicle_profiles.json"))
        self.maintenance_path = (Path(maintenance_path) if maintenance_path is not None else
                                 Path(data_path).with_name("maintenance_schedule.json"))
        self.maintenance_windows = []
        self.history_windows = []
        self.profiles = []
        self.active_profile = None
        self.profiles_available = False
        root.title("Vehicle Inspection Checklist")

        content = tk.Frame(root, padx=20, pady=20)
        content.pack(fill="both", expand=True)
        tk.Label(content, text="Vehicle profile (optional)").pack(anchor="w")
        self.profile_selector = ttk.Combobox(content, state="readonly", width=60)
        self.profile_selector.pack(fill="x")
        self.profile_selector.bind("<<ComboboxSelected>>", self.select_profile)
        profile_buttons = tk.Frame(content)
        profile_buttons.pack(fill="x")
        self.profile_buttons = []
        for label, command in (("New Profile…", self.new_profile),
                               ("Edit Profile…", self.edit_profile),
                               ("Delete Profile…", self.delete_profile)):
            button = tk.Button(profile_buttons, text=label, command=command)
            button.pack(side="left", padx=3)
            self.profile_buttons.append(button)
        tk.Button(profile_buttons, text="Reload Profiles", command=self.reload_profiles).pack(side="left")
        tk.Label(content, text="Name and mileage below are inspection values; profiles are not updated by saving.").pack(anchor="w")
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
            ("Maintenance…", self.open_maintenance),
            ("Refresh", self.refresh),
            ("Reset All", self.reset_all),
            ("Backup All Data", self.backup_all_data),
            ("Restore Backup", self.restore_backup),
            ("Close", root.destroy),
        ):
            tk.Button(buttons, text=label, command=command).pack(
                side="left", padx=4
            )
        self.refresh()
        self.reload_profiles()

    def save_inspection(self):
        """Save a snapshot without resetting checklist or form fields."""
        try:
            record = create_inspection_record(
                self.vehicle_name.get(), self.mileage.get(),
                self.notes.get("1.0", "end-1c"), load_data(self.data_path),
                findings=self.findings, assessment=self.assessment.get(),
                profile=self.active_profile)
            append_inspection(self.history_path, record)
        except (ValueError, OSError) as error:
            messagebox.showerror("Save Inspection", str(error), parent=self.root)
            return
        messagebox.showinfo("Save Inspection", "Inspection saved.", parent=self.root)

    def reload_profiles(self):
        """Reload choices without replacing the current inspection draft snapshot."""
        try:
            profiles = load_profiles(self.profiles_path)["profiles"]
        except (ValueError, OSError) as error:
            self.profiles_available = False
            self.profile_selector.configure(values=["Manual entry"], state="disabled")
            self.profile_selector.current(0)
            for button in self.profile_buttons:
                button.configure(state=tk.DISABLED)
            # Keep manual entry available even when the store cannot be read.
            self.active_profile = None
            messagebox.showerror("Vehicle Profiles", str(error), parent=self.root)
            return False
        self.profiles = profiles
        self.profiles_available = True
        self.profile_selector.configure(state="readonly", values=["Manual entry"] + [
            f"{p['vehicle_name']} [{p['profile_id']}]" for p in profiles])
        for button in self.profile_buttons:
            button.configure(state=tk.NORMAL)
        ids = [p["profile_id"] for p in profiles]
        if self.active_profile is not None and self.active_profile["profile_id"] in ids:
            self.profile_selector.current(ids.index(self.active_profile["profile_id"]) + 1)
        else:
            self.active_profile = None
            self.profile_selector.current(0)
        return True

    def select_profile(self, event=None):
        index = self.profile_selector.current()
        if index == 0:
            self.active_profile = None
        elif self.profiles_available and 0 < index <= len(self.profiles):
            self._apply_profile(self.profiles[index - 1])

    def _apply_profile(self, profile):
        self.active_profile = deepcopy(profile)
        self.vehicle_name.set(profile["vehicle_name"])
        self.mileage.set(str(profile["mileage"]))

    def _profile_saved(self, profile):
        self._apply_profile(profile)
        self.reload_profiles()

    def new_profile(self):
        if self.profiles_available:
            ProfileEditor(self.root, self.profiles_path, self._profile_saved)

    def edit_profile(self):
        if self.profiles_available and self.active_profile is not None:
            ProfileEditor(self.root, self.profiles_path, self._profile_saved,
                          profile=self.active_profile)

    def delete_profile(self):
        if not self.profiles_available or self.active_profile is None:
            return
        profile = self.active_profile
        if not messagebox.askyesno("Delete Vehicle Profile",
                f"Delete {profile['vehicle_name']}? Saved inspections will remain preserved.",
                parent=self.root):
            return
        try:
            delete_profile(self.profiles_path, profile["profile_id"])
        except (ValueError, OSError) as error:
            messagebox.showerror("Vehicle Profiles", str(error), parent=self.root)
            return
        self.active_profile = None
        self.reload_profiles()

    def open_findings(self):
        FindingsWindow(self.root, self.findings, self._apply_findings)

    def _apply_findings(self, findings):
        self.findings = validate_findings(findings)

    def open_history(self):
        window = HistoryWindow(self.root, self.history_path)
        window.runtime_paths = self.runtime_paths()
        self.history_windows.append(window)

    def open_maintenance(self):
        self.maintenance_windows.append(MaintenanceWindow(self.root, self.maintenance_path, self.profiles_path))

    def runtime_paths(self):
        return RuntimePaths(Path(self.data_path), self.history_path, self.profiles_path, self.maintenance_path)

    def backup_all_data(self):
        directory = filedialog.askdirectory(title="Backup All Data", parent=self.root,
                                            initialdir=str(Path(self.data_path).parent / "backups"))
        if not directory:
            return
        try:
            output = create_backup(self.runtime_paths(), Path(directory))
        except (ValueError, OSError) as error:
            messagebox.showerror("Backup All Data", str(error), parent=self.root)
            return
        messagebox.showinfo("Backup All Data", f"Backup saved: {output}", parent=self.root)

    def restore_backup(self):
        archive = filedialog.askopenfilename(title="Restore Backup", parent=self.root,
                                             filetypes=[("ZIP backup", "*.zip")])
        if not archive:
            return
        try:
            reject_output_alias(archive, self.runtime_paths().mapping().values())
            backup = validate_backup(Path(archive))
            included = [name for name, _ in backup.files]
            retained = [name for name in self.runtime_paths().mapping() if name not in included]
            warning = ("Replace/create: " + ", ".join(included) +
                       "\nLeave untouched: " + (", ".join(retained) or "none") +
                       "\nCurrent contents of included files will be replaced. Make a current backup first."
                       "\nPower loss or rollback failure can prevent complete recovery. Restore now?")
            if not messagebox.askyesno("Restore Backup", warning, parent=self.root):
                return
            restore_backup(backup, self.runtime_paths(), confirmed=True)
        except (ValueError, OSError) as error:
            messagebox.showerror("Restore Backup", str(error), parent=self.root)
            return
        try:
            self.refresh(create_missing=False)
            self.reload_profiles()
            for window in self.maintenance_windows:
                if window.root.winfo_exists():
                    window.refresh()
            for window in self.history_windows:
                if window.root.winfo_exists():
                    window.refresh()
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror("Restore Backup", f"Data restored, but view refresh failed: {error}", parent=self.root)
            return
        messagebox.showinfo("Restore Backup", "Restore completed. Omitted files were left untouched.", parent=self.root)

    def refresh(self, *, create_missing=True):
        """Reload shared state, including changes made through the CLI."""
        data = (load_data(self.data_path) if create_missing or Path(self.data_path).exists()
                else create_default_data())
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


class ProfileEditor:
    """A modal form; persistence and validation remain outside tkinter."""

    def __init__(self, parent, profiles_path, on_saved, profile=None):
        self.profiles_path = profiles_path
        self.on_saved = on_saved
        self.profile = deepcopy(profile)
        self.root = tk.Toplevel(parent)
        self.root.title("Edit Vehicle Profile" if profile else "New Vehicle Profile")
        self.root.transient(parent)
        self.root.grab_set()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.fields = {}
        for key, label in (("vehicle_name", "Vehicle name / unit number (required)"),
                           ("year", "Year (optional)"), ("make", "Make (optional)"),
                           ("model", "Model (optional)"), ("mileage", "Current mileage (required)")):
            value = profile[key] if profile else ""
            variable = tk.StringVar(master=self.root, value="" if value is None else str(value))
            self.fields[key] = variable
            tk.Label(self.root, text=label).pack(anchor="w", padx=12)
            tk.Entry(self.root, textvariable=variable).pack(fill="x", padx=12)
        tk.Button(self.root, text="Save Profile", command=self.save).pack(side="left", padx=12, pady=8)
        tk.Button(self.root, text="Cancel", command=self.root.destroy).pack(side="right", padx=12, pady=8)

    def save(self):
        values = {key: variable.get() for key, variable in self.fields.items()}
        try:
            if self.profile is None:
                profile = create_profile(self.profiles_path, **values)
            else:
                profile = update_profile(self.profiles_path, self.profile["profile_id"], **values)
        except (ValueError, OSError) as error:
            messagebox.showerror("Vehicle Profile", str(error), parent=self.root)
            return
        self.on_saved(profile)
        self.root.destroy()


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
        self.history_path = Path(history_path)
        self.runtime_paths = RuntimePaths(self.history_path.with_name("checklist_data.json"),
                                          self.history_path, self.history_path.with_name("vehicle_profiles.json"))
        self.schema_version = 1
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
        tk.Button(self.root, text="Export Selected Report", command=self.export_selected_report).pack()
        tk.Button(self.root, text="Export History CSV", command=self.export_history_csv).pack()
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
            history = load_history(self.history_path)
            self.schema_version = history["schema_version"]
            records = history["inspections"]
        except (ValueError, OSError) as error:
            messagebox.showerror("Inspection History", str(error), parent=self.root)
            return
        self.tree.selection_remove(*self.tree.selection())
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

    def _export_destination(self, extension):
        selected = filedialog.asksaveasfilename(parent=self.root, title="Export inspection data",
                    initialdir=str(self.history_path.parent / "exports"),
                    initialfile="inspection-report.html" if extension == ".html" else "inspection-history.csv",
                    defaultextension=extension, confirmoverwrite=False,
                    filetypes=[("Export", "*" + extension)])
        if not selected:
            return None
        path = Path(selected)
        reject_output_alias(path, self.runtime_paths.mapping().values())
        overwrite = path.exists()
        if overwrite and not messagebox.askyesno("Overwrite export", f"Replace {path}?", parent=self.root):
            return None
        return path, overwrite

    def export_selected_report(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Export Selected Report", "Select a saved inspection first.", parent=self.root)
            return
        try:
            destination = self._export_destination(".html")
            if destination is None:
                return
            path, overwrite = destination
            export_inspection_html(self.records[int(selection[0])], path,
                                   schema_version=self.schema_version, overwrite=overwrite,
                                   runtime_paths=tuple(self.runtime_paths.mapping().values()))
        except (ValueError, OSError, IndexError) as error:
            messagebox.showerror("Export Selected Report", str(error), parent=self.root)
            return
        messagebox.showinfo("Export Selected Report", f"Report saved: {path}", parent=self.root)

    def export_history_csv(self):
        try:
            destination = self._export_destination(".csv")
            if destination is None:
                return
            path, overwrite = destination
            export_history_csv(self.history_path, path, overwrite=overwrite,
                               runtime_paths=tuple(self.runtime_paths.mapping().values()))
        except (ValueError, OSError) as error:
            messagebox.showerror("Export History CSV", str(error), parent=self.root)
            return
        messagebox.showinfo("Export History CSV", f"CSV saved: {path}", parent=self.root)

    def show_selected_inspection(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        record = self.records[int(selection[0])]
        lines = [f"Vehicle: {record['vehicle_name']}", f"Mileage: {record['mileage']}",
                 f"Timestamp: {record['timestamp']}", f"Notes: {record['notes']}", ""]
        if "profile_snapshot" in record:
            lines.append(f"Profile ID: {record['profile_id']}")
            lines.append("Profile snapshot (at selection or profile edit):")
            for key, value in record["profile_snapshot"].items():
                lines.append(f"  {key.replace('_', ' ').title()}: {value if value is not None else 'Not specified'}")
            lines.append("")
        else:
            lines.append("Profile: Manual entry / no profile recorded")
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
