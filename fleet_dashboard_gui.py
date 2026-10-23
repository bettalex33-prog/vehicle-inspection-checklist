"""Headless-testable fleet dashboard with session-only evaluation inputs."""
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from fleet_dashboard import STATUSES, BUCKETS
from fleet_dashboard_exports import (capture_dashboard, export_dashboard_csv, export_dashboard_html,
                                     output_fingerprint)
from maintenance_schedule import format_money


class FleetDashboardWindow:
    def __init__(self, parent, schedule_path, profiles_path, *, runtime_paths=(), open_task=None):
        self.schedule_path, self.profiles_path = Path(schedule_path), Path(profiles_path)
        self.runtime_paths, self.open_task_callback = tuple(runtime_paths), open_task
        self.snapshot = self.model = None
        self.profile_ids = []
        self.ready = False
        self.active_task_id = None
        self.root = tk.Toplevel(parent)
        self.root.title('Fleet Dashboard and Maintenance Alerts')
        self.notice = tk.StringVar(master=self.root, value='Refresh required')
        tk.Label(self.root, textvariable=self.notice).pack(fill='x', padx=12, pady=6)
        tk.Label(self.root, text='Fleet totals; explicit mileage applies only to the selected profile. Never inferred or saved.').pack()
        inputs = tk.Frame(self.root)
        inputs.pack(fill='x', padx=12)
        self.profile_selector = ttk.Combobox(inputs, state='readonly', width=48)
        self.profile_selector.pack(side='left')
        self.profile_selector.bind('<<ComboboxSelected>>', self.select_profile)
        self.mileage = tk.StringVar(master=self.root, value='')
        tk.Label(inputs, text='Explicit current mileage').pack(side='left')
        tk.Entry(inputs, textvariable=self.mileage, width=12).pack(side='left')
        self.start = tk.StringVar(master=self.root, value='')
        self.end = tk.StringVar(master=self.root, value='')
        dates = tk.Frame(self.root)
        dates.pack(fill='x', padx=12)
        for label, variable in (('Cost start YYYY-MM-DD (inclusive)', self.start),
                                ('Cost end YYYY-MM-DD (inclusive)', self.end)):
            tk.Label(dates, text=label).pack(side='left')
            tk.Entry(dates, textvariable=variable, width=12).pack(side='left')
        tk.Button(dates, text='Evaluate / Refresh', command=self.refresh).pack(side='left')
        self.totals = tk.StringVar(master=self.root, value='Unavailable')
        self.costs = tk.StringVar(master=self.root, value='Unavailable')
        tk.Label(self.root, textvariable=self.totals, wraplength=1100).pack(fill='x', padx=12)
        tk.Label(self.root, textvariable=self.costs, wraplength=1100).pack(fill='x', padx=12)
        self.status = tk.StringVar(master=self.root, value='All statuses')
        status_selector = ttk.Combobox(self.root, textvariable=self.status,
                                      values=('All statuses', *STATUSES), state='readonly')
        status_selector.pack(anchor='w', padx=12)
        status_selector.bind('<<ComboboxSelected>>', self.render)
        self.vehicles = self._tree('Per-vehicle fleet summaries (all vehicles)',
            ('vehicle', 'id', *STATUSES, 'services', 'unpriced', 'legacy', 'cost'))
        self.tasks = self._tree('Tasks (selected profile and status filters)',
            ('vehicle', 'task', 'status', 'date', 'mileage', 'unknown', 'appointment',
             'services', 'priced', 'unpriced', 'legacy', 'cost'))
        self.appointments = self._tree('Appointments (selected profile; enabled, nonarchived tasks)',
            ('bucket', 'date', 'vehicle', 'task'))
        self.tasks.bind('<<TreeviewSelect>>', self.select_task)
        self.appointments.bind('<<TreeviewSelect>>', self.select_appointment)
        self.tasks.bind('<Double-1>', self.navigate)
        self.appointments.bind('<Double-1>', self.navigate)
        actions = tk.Frame(self.root)
        actions.pack(fill='x', padx=12, pady=6)
        self.buttons = {}
        for label, callback in (('Open Task / Full History', self.navigate),
                                ('Export CSV', self.export_csv), ('Export HTML', self.export_html)):
            button = tk.Button(actions, text=label, command=callback)
            button.pack(side='left', padx=3)
            self.buttons[label] = button
        tk.Button(actions, text='Close', command=self.root.destroy).pack(side='right')
        for variable in (self.mileage, self.start, self.end):
            variable.trace_add('write', self.dirty)
        self.refresh()

    def _tree(self, label, columns):
        tk.Label(self.root, text=label).pack(anchor='w', padx=12)
        tree = ttk.Treeview(self.root, columns=columns, show='headings', selectmode='browse', height=5)
        for column in columns:
            tree.heading(column, text=column.replace('_', ' ').title())
            tree.column(column, width=100, minwidth=55)
        tree.pack(fill='both', expand=True, padx=12)
        scrollbar = ttk.Scrollbar(self.root, orient='horizontal', command=tree.xview)
        scrollbar.pack(fill='x', padx=12)
        tree.configure(xscrollcommand=scrollbar.set)
        return tree

    def selected_profile_id(self):
        index = self.profile_selector.current() - 1
        return self.profile_ids[index] if 0 <= index < len(self.profile_ids) else None

    def dirty(self, *args):
        self.ready = False
        self.notice.set('Inputs changed; results need refresh. Export and navigation disabled.')
        self._buttons()

    def _buttons(self):
        for label, button in self.buttons.items():
            enabled = self.ready and (label != 'Open Task / Full History' or
                                     (self.active_task_id is not None and self.open_task_callback is not None))
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def select_profile(self, event=None):
        self.mileage.set('')
        self.refresh()

    def refresh(self, *, reset=False):
        selected = self.selected_profile_id()
        if reset:
            self.mileage.set('')
        try:
            # Load once without mileage first so deleted profiles can be cleared safely.
            base = capture_dashboard(self.schedule_path, self.profiles_path, today=date.today())
            live = [v for v in base.model['vehicles'] if not v['profile_missing']]
            ids = [v['profile_id'] for v in live]
            if selected is not None and selected not in ids:
                selected = None
                self.mileage.set('')
            text = self.mileage.get().strip()
            if text and (selected is None or any(c not in '0123456789' for c in text)):
                raise ValueError('Select a profile and enter current mileage using digits only.')
            snapshot = capture_dashboard(self.schedule_path, self.profiles_path, today=date.today(),
                         selected_profile_id=selected, current_mileage=int(text) if text else None,
                         cost_start_date=self.start.get(), cost_end_date=self.end.get())
            # Both captures must describe the same profile list.
            base.revalidate()
            model = snapshot.model
        except (ValueError, OSError, OverflowError) as error:
            self.ready = False
            self.snapshot = self.model = None
            self.active_task_id = None
            for tree in (self.vehicles, self.tasks, self.appointments):
                for child in tree.get_children():
                    tree.delete(child)
            self.totals.set('Unavailable — refresh required')
            self.costs.set('Unavailable — refresh required')
            self.notice.set('Dashboard unavailable. Correct inputs/data or restore a valid backup, then refresh.')
            self._buttons()
            messagebox.showerror('Fleet dashboard', str(error), parent=self.root)
            return
        self.profile_ids = ids
        self.profile_selector.configure(values=['All profiles (dates only)'] +
            [f"{v['vehicle_name']} [{v['profile_id']}]" for v in live])
        self.profile_selector.current(ids.index(selected) + 1 if selected else 0)
        self.snapshot, self.model, self.ready = snapshot, model, True
        self.active_task_id = None
        self.notice.set('Evaluated on ' + model['evaluated_on'] + '. Fleet totals; cost range applies only to costs.')
        self.render()

    def render(self, event=None):
        if self.model is None:
            return
        model = self.model
        self.totals.set(' | '.join(f'{s}: {model["status_totals"][s]}' for s in STATUSES))
        cost = model['costs']
        self.costs.set(f"Recorded cost: {format_money(cost['total_cost_cents'])} | Services: {cost['service_count']} | "
                       f"Priced: {cost['priced_service_count']} | Unpriced: {cost['unpriced_service_count']} | Legacy: {cost['legacy_service_count']}")
        for tree in (self.vehicles, self.tasks, self.appointments):
            for child in tree.get_children():
                tree.delete(child)
        self.active_task_id = None
        for vehicle in model['vehicles']:
            cost = vehicle['costs']
            self.vehicles.insert('', 'end', iid=vehicle['profile_id'], values=(
                vehicle['vehicle_name'] + (' (missing profile)' if vehicle['profile_missing'] else ''),
                vehicle['profile_id'], *(vehicle['status_totals'][s] for s in STATUSES),
                cost['service_count'], cost['unpriced_service_count'], cost['legacy_service_count'],
                format_money(cost['total_cost_cents'])))
        selected = self.selected_profile_id()
        for row in model['tasks']:
            if selected and row['profile_id'] != selected:
                continue
            if self.status.get() not in ('All statuses', row['status']):
                continue
            task = row['task']
            self.tasks.insert('', 'end', iid=task['task_id'], values=(row['vehicle_name'], task['title'],
                row['status'], row['next_due_date'] or '—', row['next_due_mileage'] if row['next_due_mileage'] is not None else '—',
                'Mileage needed' if row['mileage_unknown'] else '', task['scheduled_date'] or '—',
                row['costs']['service_count'], row['costs']['priced_service_count'],
                row['costs']['unpriced_service_count'], row['costs']['legacy_service_count'],
                format_money(row['costs']['total_cost_cents'])))
        for bucket in BUCKETS:
            for row in model['appointments'][bucket]:
                if selected and row['profile_id'] != selected:
                    continue
                self.appointments.insert('', 'end', iid=row['task']['task_id'], values=(
                    bucket, row['task']['scheduled_date'], row['vehicle_name'], row['task']['title']))
        self._buttons()

    def select_task(self, event=None):
        selected = self.tasks.selection()
        self.active_task_id = selected[0] if selected else None
        self._buttons()

    def select_appointment(self, event=None):
        selected = self.appointments.selection()
        self.active_task_id = selected[0] if selected else None
        self._buttons()

    def navigate(self, event=None):
        if not self.ready or self.active_task_id is None or self.open_task_callback is None:
            return
        try:
            self.snapshot.revalidate()
            self.open_task_callback(self.active_task_id)
        except (ValueError, OSError) as error:
            self.dirty()
            messagebox.showerror('Fleet dashboard', str(error), parent=self.root)

    def export_csv(self):
        self._export('.csv', export_dashboard_csv)

    def export_html(self):
        self._export('.html', export_dashboard_html)

    def _export(self, extension, exporter):
        if not self.ready:
            return
        output = filedialog.asksaveasfilename(parent=self.root, title='Export fleet dashboard',
            initialdir=str(self.schedule_path.parent / 'exports'), initialfile='fleet-dashboard' + extension,
            defaultextension=extension, filetypes=[('Dashboard report', '*' + extension)], confirmoverwrite=False)
        if not output:
            return
        try:
            previous_output = output_fingerprint(output)
            overwrite = previous_output is not None
            if overwrite and not messagebox.askyesno('Overwrite dashboard export', f'Replace {output}?', parent=self.root):
                return
            exporter(self.snapshot, Path(output), overwrite=overwrite, runtime_paths=self.runtime_paths,
                     expected_output=previous_output)
        except (ValueError, OSError) as error:
            self.dirty()
            messagebox.showerror('Dashboard export', str(error), parent=self.root)
            return
        messagebox.showinfo('Dashboard export', f'Report saved: {output}', parent=self.root)
