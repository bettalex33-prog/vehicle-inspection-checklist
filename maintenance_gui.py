"""Headless-testable Tk maintenance views; all writes delegate to the store."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from vehicle_profiles import load_profiles
from maintenance_schedule import (load_schedule, validate_task_details, validate_service_details,
    create_task, update_task, delete_task, archive_task, record_service, calculate_due, format_money, COST_KEYS, NOT_RECORDED, task_cost_summary)
from maintenance_exports import export_maintenance_csv, export_maintenance_html


class MaintenanceWindow:
    def __init__(self, parent, schedule_path, profiles_path):
        self.schedule_path = Path(schedule_path)
        self.profiles_path = Path(profiles_path)
        self.tasks = []
        self.profiles = []
        self.available = False
        self.root = tk.Toplevel(parent)
        self.root.title('Maintenance Due and Service Scheduling')
        filters = tk.Frame(self.root)
        filters.pack(fill='x', padx=12, pady=8)
        tk.Label(filters, text='View').pack(side='left')
        self.view = tk.StringVar(master=self.root, value='Active')
        self.view_selector = ttk.Combobox(filters, textvariable=self.view,
            values=('Active', 'Archived'), state='readonly', width=12)
        self.view_selector.pack(side='left')
        self.view_selector.bind('<<ComboboxSelected>>', self.render)
        self.profile_selector = ttk.Combobox(filters, state='readonly', width=55)
        self.profile_selector.pack(side='left')
        self.profile_selector.bind('<<ComboboxSelected>>', self.select_profile)
        tk.Label(self.root, text='Current mileage (enter explicitly; evaluation only)').pack(anchor='w', padx=12)
        self.mileage = tk.StringVar(master=self.root, value='')
        tk.Entry(self.root, textvariable=self.mileage).pack(fill='x', padx=12)
        tk.Button(self.root, text='Evaluate / Refresh', command=self.refresh).pack(anchor='w', padx=12)
        tk.Label(self.root, text='All-profile view evaluates dates; select a profile to evaluate mileage.').pack(anchor='w', padx=12)
        self.tree = ttk.Treeview(self.root, columns=('vehicle', 'task', 'status', 'date', 'mileage', 'appointment'),
                                 show='headings', selectmode='browse')
        for column, label in (('vehicle', 'Vehicle'), ('task', 'Task'), ('status', 'Status'),
                              ('date', 'Next date'), ('mileage', 'Next mileage'), ('appointment', 'Appointment')):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=140)
        self.tree.pack(fill='both', expand=True, padx=12)
        self.tree.bind('<<TreeviewSelect>>', self.show_details)
        self.details = tk.Text(self.root, height=14, width=85, state=tk.DISABLED, wrap='word')
        self.details.pack(fill='both', expand=True, padx=12)
        actions = tk.Frame(self.root)
        actions.pack(fill='x', padx=12, pady=8)
        self.buttons = {}
        for label, callback in (('New Task…', self.new_task), ('Edit Task…', self.edit_task),
                                ('Record Service…', self.record_service), ('Delete / Archive…', self.remove_task)):
            button = tk.Button(actions, text=label, command=callback)
            button.pack(side='left', padx=3)
            self.buttons[label] = button
        tk.Button(self.root, text='Export All History CSV', command=self.export_csv).pack()
        tk.Button(self.root, text='Export All History HTML', command=self.export_html).pack()
        tk.Button(actions, text='Close', command=self.root.destroy).pack(side='right')
        self.refresh(initial=True)

    def selected_profile(self):
        index = self.profile_selector.current() - 2
        return self.profiles[index] if 0 <= index < len(self.profiles) else None

    def focus_task(self, task_id):
        """Reload and navigate by stable ID; always show complete, unfiltered history."""
        self.mileage.set('')
        self.refresh()
        if not self.available:
            raise ValueError('Maintenance unavailable; refresh after correcting the data.')
        task = next((task for task in self.tasks if task['task_id'] == task_id), None)
        if task is None:
            raise ValueError('Task no longer exists. Refresh the dashboard.')
        ids = [profile['profile_id'] for profile in self.profiles]
        self.view.set('Archived' if task['archived'] else 'Active')
        self.profile_selector.current(ids.index(task['profile_id']) + 2 if task['profile_id'] in ids else 1)
        self.render()
        self.tree.selection_set(task_id)
        self.tree.focus(task_id)
        self.tree.see(task_id)
        self.show_details()

    def selected_task(self):
        selection = self.tree.selection()
        return next((task for task in self.tasks if selection and task['task_id'] == selection[0]), None)

    def _details(self, text):
        self.details.configure(state=tk.NORMAL)
        self.details.delete('1.0', tk.END)
        self.details.insert('1.0', text)
        self.details.configure(state=tk.DISABLED)

    def refresh(self, *, initial=False):
        old_profile = None if initial else self.selected_profile()
        old_index = 0 if initial else self.profile_selector.current()
        try:
            tasks = load_schedule(self.schedule_path)['tasks']
            profiles = load_profiles(self.profiles_path)['profiles']
        except (ValueError, OSError) as error:
            self.available = False
            self.tasks = []
            for child in self.tree.get_children():
                self.tree.delete(child)
            for button in self.buttons.values():
                button.configure(state=tk.DISABLED)
            self._details('Maintenance unavailable. Correct the data or restore a valid backup, then refresh.')
            messagebox.showerror('Maintenance', str(error), parent=self.root)
            return
        self.tasks, self.profiles = tasks, profiles
        self.available = True
        self.profile_selector.configure(values=['All profiles', 'Missing profiles'] + [
            f"{p['vehicle_name']} [{p['profile_id']}]" for p in profiles])
        ids = [p['profile_id'] for p in profiles]
        index = ids.index(old_profile['profile_id']) + 2 if old_profile and old_profile['profile_id'] in ids else (1 if old_index == 1 else 0)
        self.profile_selector.current(index)
        if old_profile and (index < 2 or self.profiles[index - 2]['profile_id'] != old_profile['profile_id']):
            self.mileage.set('')
        self.render()

    def select_profile(self, event=None):
        self.mileage.set('')
        self.render()

    def render(self, event=None):
        for child in self.tree.get_children():
            self.tree.delete(child)
        self._details('Select a task to view its snapshot, due details, and service history.')
        if not self.available:
            return
        profile = self.selected_profile()
        mileage = None
        try:
            text = self.mileage.get().strip()
            if profile and text:
                if any(c not in '0123456789' for c in text):
                    raise ValueError('Current mileage must contain digits only.')
                mileage = int(text)
        except ValueError as error:
            messagebox.showerror('Maintenance', str(error), parent=self.root)
            self.update_buttons()
            return
        ids = {p['profile_id'] for p in self.profiles}
        for task in self.tasks:
            if task['archived'] != (self.view.get() == 'Archived'):
                continue
            if profile and task['profile_id'] != profile['profile_id']:
                continue
            if self.profile_selector.current() == 1 and task['profile_id'] in ids:
                continue
            try:
                due = calculate_due(task, today=date.today(), current_mileage=mileage)
                status = due['status']
            except ValueError as error:
                due = calculate_due(task, today=date.today(), current_mileage=None)
                status = str(error)
            vehicle = task['profile_snapshot']['vehicle_name']
            if task['profile_id'] not in ids:
                vehicle += ' (missing profile)'
            self.tree.insert('', 'end', iid=task['task_id'], values=(vehicle, task['title'], status,
                due['next_due_date'] or '—', due['next_due_mileage'] if due['next_due_mileage'] is not None else '—',
                (task['scheduled_date'] or '—') + (' (passed)' if due['appointment_passed'] else '')))
        self.update_buttons()

    def update_buttons(self):
        task = self.selected_task()
        active = task is not None and not task['archived']
        ids = {p['profile_id'] for p in self.profiles}
        states = {'New Task…': self.available and self.selected_profile() is not None and self.view.get() != 'Archived',
                  'Edit Task…': self.available and active,
                  'Record Service…': self.available and active and task['enabled'] and task['profile_id'] in ids,
                  'Delete / Archive…': self.available and task is not None and (not task['archived'] or not task['services'])}
        for label, enabled in states.items():
            self.buttons[label].configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def show_details(self, event=None):
        self.update_buttons()
        task = self.selected_task()
        if task is None:
            return
        profile = self.selected_profile()
        try:
            text = self.mileage.get().strip()
            mileage = int(text) if profile and text and all(c in '0123456789' for c in text) else None
            due = calculate_due(task, today=date.today(), current_mileage=mileage)
            evaluation = '\n'.join(f'{key}: {value}' for key, value in due.items())
        except ValueError as error:
            evaluation = str(error)
        lines = [task['title'], f"Task ID: {task['task_id']}", f"Profile ID: {task['profile_id']}",
                 f"Enabled: {task['enabled']} | Archived: {task['archived']}",
                 'Profile snapshot (captured at task creation):']
        lines.extend(f'  {key}: {value}' for key, value in task['profile_snapshot'].items())
        lines.extend(f'{key}: {task[key]}' for key in ('interval_days', 'interval_miles', 'warning_days',
                     'warning_miles', 'baseline_date', 'baseline_mileage', 'scheduled_date', 'notes'))
        summary = task_cost_summary(task)
        lines += [evaluation, f"Recorded task cost: {format_money(summary['total_cost_cents'])}",
                  f"Services: {summary['service_count']} | Unpriced: {summary['unpriced_service_count']}", 'Service history:']
        for service in task['services']:
            lines.append(f"{service['completed_date']} | {service['mileage']} miles | {service['notes']} [{service['service_id']}]")
            lines.extend(f"  {key.replace('_', ' ').title()}: {service.get(key) or NOT_RECORDED}"
                         for key in ('provider', 'invoice_reference'))
            lines.extend(f"  {key.replace('_cents', '').replace('_', ' ').title()}: {format_money(service.get(key))}"
                         for key in (*COST_KEYS, 'total_cost_cents'))
        self._details('\n'.join(lines))

    def export_csv(self):
        self._export('.csv', export_maintenance_csv)

    def export_html(self):
        self._export('.html', export_maintenance_html)

    def _export(self, extension, exporter):
        selected = filedialog.asksaveasfilename(parent=self.root, title='Export all maintenance history',
                    initialdir=str(self.schedule_path.parent / 'exports'),
                    initialfile='maintenance-history' + extension, defaultextension=extension,
                    confirmoverwrite=False, filetypes=[('Maintenance report', '*' + extension)])
        if not selected:
            return
        try:
            from data_backup import reject_output_alias, NAMES
            protected = [self.schedule_path, self.profiles_path,
                         *(self.schedule_path.parent / name for name in NAMES)]
            reject_output_alias(selected, protected)
            overwrite = Path(selected).exists()
            if overwrite and not messagebox.askyesno('Overwrite maintenance export', f'Replace {selected}?', parent=self.root):
                return
            mileages = {}
            profile = self.selected_profile()
            text = self.mileage.get().strip()
            if profile and text:
                if any(c not in '0123456789' for c in text):
                    raise ValueError('Current mileage must contain digits only.')
                mileages[profile['profile_id']] = int(text)
            exporter(self.schedule_path, Path(selected), today=date.today(), current_mileages=mileages,
                     overwrite=overwrite, runtime_paths=protected)
        except (ValueError, OSError) as error:
            messagebox.showerror('Maintenance export', str(error), parent=self.root)
            return
        messagebox.showinfo('Maintenance export', f'Report saved: {selected}', parent=self.root)

    def new_task(self):
        profile = self.selected_profile()
        if self.available and profile and self.view.get() != 'Archived':
            MaintenanceTaskEditor(self.root, self.schedule_path, self.profiles_path, profile['profile_id'], self.refresh)

    def edit_task(self):
        task = self.selected_task()
        if self.available and task and not task['archived']:
            MaintenanceTaskEditor(self.root, self.schedule_path, self.profiles_path, task['profile_id'], self.refresh, task=task)

    def record_service(self):
        task = self.selected_task()
        if self.available and task and task['enabled'] and not task['archived']:
            RecordServiceDialog(self.root, self.schedule_path, self.profiles_path, task, self.refresh)

    def remove_task(self):
        task = self.selected_task()
        if not self.available or task is None:
            return
        archive = bool(task['services'])
        if archive and task['archived']:
            return
        question = ('Archive and disable this task? Its entire service history will be retained.' if archive
                    else 'Permanently delete this task? It has no recorded services.')
        if not messagebox.askyesno('Maintenance', question, parent=self.root):
            return
        try:
            action = archive_task if archive else delete_task
            action(self.schedule_path, task['task_id'], expected_task=task)
        except (ValueError, OSError) as error:
            messagebox.showerror('Maintenance', str(error), parent=self.root)
            return
        self.refresh()


class MaintenanceTaskEditor:
    def __init__(self, parent, schedule_path, profiles_path, profile_id, on_saved, task=None):
        self.schedule_path, self.profiles_path = schedule_path, profiles_path
        self.profile_id, self.on_saved, self.task = profile_id, on_saved, deepcopy(task)
        self.root = tk.Toplevel(parent)
        self.root.title('Edit Maintenance Task' if task else 'New Maintenance Task')
        self.root.transient(parent); self.root.grab_set()
        self.fields = {}
        defaults = {'title': '', 'notes': '', 'interval_days': '180', 'interval_miles': '',
                    'warning_days': '30', 'warning_miles': '', 'baseline_date': date.today().isoformat(),
                    'baseline_mileage': '', 'scheduled_date': ''}
        tk.Label(self.root, text='Use days and/or miles. Blank unused intervals and warnings. Dates: YYYY-MM-DD.').pack()
        for key, default in defaults.items():
            value = task[key] if task else default
            variable = tk.StringVar(master=self.root, value='' if value is None else str(value))
            self.fields[key] = variable
            tk.Label(self.root, text=key.replace('_', ' ').title()).pack(anchor='w', padx=12)
            entry = tk.Entry(self.root, textvariable=variable, width=65)
            entry.pack(fill='x', padx=12)
            if task and task['services'] and key in ('baseline_date', 'baseline_mileage'):
                entry.configure(state='disabled')
        self.enabled = tk.BooleanVar(master=self.root, value=task['enabled'] if task else True)
        tk.Checkbutton(self.root, text='Enabled', variable=self.enabled).pack(anchor='w', padx=12)
        tk.Button(self.root, text='Save Task', command=self.save).pack(side='left', padx=12, pady=8)
        tk.Button(self.root, text='Cancel', command=self.root.destroy).pack(side='right', padx=12, pady=8)

    def save(self):
        try:
            details = validate_task_details(today=date.today(), enabled=self.enabled.get(),
                                           **{key: var.get() for key, var in self.fields.items()})
            if self.task is None:
                create_task(self.schedule_path, self.profile_id, details, profiles_path=self.profiles_path, today=date.today())
            else:
                update_task(self.schedule_path, self.task['task_id'], details, expected_task=self.task, today=date.today())
        except (ValueError, OSError) as error:
            messagebox.showerror('Maintenance Task', str(error), parent=self.root)
            return
        self.on_saved(); self.root.destroy()


class RecordServiceDialog:
    def __init__(self, parent, schedule_path, profiles_path, task, on_saved):
        self.schedule_path, self.profiles_path = schedule_path, profiles_path
        self.task, self.on_saved = deepcopy(task), on_saved
        self.root = tk.Toplevel(parent)
        self.root.title('Record Service')
        self.root.transient(parent); self.root.grab_set()
        self.fields = {}
        for key, default in (('completed_date', date.today().isoformat()), ('mileage', ''), ('notes', ''),
                             ('provider', ''), ('invoice_reference', ''), ('labor_cost', ''),
                             ('parts_cost', ''), ('tax', '')):
            tk.Label(self.root, text=key.replace('_', ' ').title()).pack(anchor='w', padx=12)
            self.fields[key] = tk.StringVar(master=self.root, value=default)
            tk.Entry(self.root, textvariable=self.fields[key], width=65).pack(fill='x', padx=12)
        tk.Button(self.root, text='Record Service', command=self.save).pack(side='left', padx=12, pady=8)
        tk.Button(self.root, text='Cancel', command=self.root.destroy).pack(side='right', padx=12, pady=8)

    def save(self):
        try:
            details = validate_service_details(**{key: var.get() for key, var in self.fields.items()}, today=date.today())
            if not messagebox.askyesno('Record Service',
                    f"Record completed service? Total: {format_money(details['total_cost_cents'])}.\n"
                    'Unrecorded cost components remain Not recorded. Both applicable due thresholds will advance and the appointment will be cleared.', parent=self.root):
                return
            record_service(self.schedule_path, self.task['task_id'], details, profiles_path=self.profiles_path,
                           expected_task=self.task, today=date.today())
        except (ValueError, OSError) as error:
            messagebox.showerror('Record Service', str(error), parent=self.root)
            return
        self.on_saved(); self.root.destroy()
