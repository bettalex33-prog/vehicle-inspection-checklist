"""Headless-testable, session-only Maintenance Insights window."""
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from fleet_dashboard import parse_date
from maintenance_schedule import format_money
from maintenance_insights_sources import capture_insights
from maintenance_insights_exports import export_insights_csv, export_insights_html, output_fingerprint


class MaintenanceInsightsWindow:
    def __init__(self, parent, schedule_path, profiles_path, history_path, *, runtime_paths=(), open_task=None):
        try:
            self._initialize(parent, schedule_path, profiles_path, history_path,
                             runtime_paths=runtime_paths, open_task=open_task)
        except Exception as error:
            messagebox.showerror('Maintenance Insights', 'Unable to initialize insights: ' + str(error),
                                 parent=getattr(self, 'root', parent))
            if hasattr(self, 'root'):
                self.root.destroy()
            raise

    def _initialize(self, parent, schedule_path, profiles_path, history_path, *, runtime_paths=(), open_task=None):
        self.schedule_path, self.profiles_path, self.history_path = map(Path, (schedule_path, profiles_path, history_path))
        self.runtime_paths, self.open_task_callback = tuple(runtime_paths), open_task
        self.snapshot = self.model = None
        self.ready = False
        self.profile_ids, self.live_ids = [], set()
        self.active_task_id = None
        self.root = tk.Toplevel(parent)
        self.root.title('Maintenance Insights and Forecasting')
        # Reserve non-scrolling rows for inputs and actions. Only the notebook
        # grows; table requested widths must never set the toplevel width.
        width = min(1100, max(1, self.root.winfo_screenwidth() - 80))
        height = min(740, max(1, self.root.winfo_screenheight() - 100))
        self.root.geometry(f'{width}x{height}')
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.controls = tk.Frame(self.root)
        self.controls.grid(row=0, column=0, sticky='ew', padx=12, pady=6)
        self.controls.columnconfigure(1, weight=1)
        self.profile_selector = ttk.Combobox(self.controls, state='readonly', width=30)
        tk.Label(self.controls, text='Profile (stable ID)').grid(row=0, column=0, sticky='w')
        self.profile_selector.grid(row=0, column=1, sticky='ew')
        self.profile_selector.bind('<<ComboboxSelected>>', self.select_profile)
        self.evaluation_date = tk.StringVar(master=self.root, value=date.today().isoformat())
        self.mileage = tk.StringVar(master=self.root, value='')
        self.start = tk.StringVar(master=self.root, value='')
        self.end = tk.StringVar(master=self.root, value='')
        for row, (label, variable) in enumerate((
                ('Evaluation YYYY-MM-DD', self.evaluation_date),
                ('Explicit current mileage (never saved)', self.mileage),
                ('Cost start (inclusive)', self.start), ('Cost end (inclusive)', self.end)), 1):
            tk.Label(self.controls, text=label).grid(row=row, column=0, sticky='w')
            tk.Entry(self.controls, textvariable=variable, width=18).grid(row=row, column=1, sticky='w')
        tk.Button(self.controls, text='Evaluate / Refresh', command=self.refresh).grid(
            row=5, column=0, columnspan=2, sticky='w', pady=4)
        self.notice = tk.StringVar(master=self.root, value='Evaluation required')
        tk.Label(self.controls, textvariable=self.notice, wraplength=650, anchor='w',
                 justify='left').grid(row=6, column=0, columnspan=2, sticky='ew')
        self.totals = tk.StringVar(master=self.root, value='Unavailable')
        tk.Label(self.root, textvariable=self.totals, wraplength=650).grid(
            row=1, column=0, sticky='ew', padx=12)
        self.notebook = ttk.Notebook(self.root, width=1, height=1)
        self.notebook.grid(row=2, column=0, sticky='nsew', padx=12)
        self.pages = pages = {}
        for name in ('Forecasts', 'Costs and Months', 'Observations', 'Data Quality'):
            page = pages[name] = tk.Frame(self.notebook, width=1, height=1)
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            self.notebook.add(page, text=name)
        self.forecasts = self._tree(pages['Forecasts'],
            ('vehicle', 'profile_id', 'task', 'status', 'date_threshold', 'days', 'date_availability',
             'mileage_threshold', 'miles', 'mileage_availability', 'estimated_date', 'reason'))
        self.forecasts.bind('<<TreeviewSelect>>', self.select_task)
        self.forecasts.bind('<Double-1>', self.navigate)
        self.forecast_details = self._text_region(pages['Forecasts'], row=1, height=5)
        self.costs = self._tree(pages['Costs and Months'],
            ('scope', 'profile_id', 'services', 'priced', 'unpriced', 'legacy', 'total', 'average', 'minimum', 'maximum'))
        tk.Label(pages['Costs and Months'], text='Recently serviced: preceding 30 days inclusive; cost filters ignored',
                 wraplength=650).grid(row=1, column=0, sticky='ew')
        self.recent = self._tree(pages['Costs and Months'], ('date', 'profile_id', 'task', 'mileage', 'cost'), row=2)
        self.observations = self._tree(pages['Observations'], ('profile_id', 'date', 'mileage', 'accepted', 'provenance'))
        tk.Label(pages['Observations'], text='Observed endpoint rate (miles/day); exact miles / days shown separately',
                 wraplength=650).grid(row=1, column=0, sticky='ew')
        self.rates = self._tree(pages['Observations'], ('profile_id', 'availability', 'start', 'end', 'miles', 'days', 'rate', 'reason'), row=2)
        self.quality = self._text_region(pages['Data Quality'], row=0, height=8)
        self.actions = actions = tk.Frame(self.root)
        actions.grid(row=3, column=0, sticky='ew', padx=12, pady=6)
        self.buttons = {}
        for label, callback in (('Open Task / Full History', self.navigate), ('Export CSV', self.export_csv), ('Export HTML', self.export_html)):
            button = tk.Button(actions, text=label, command=callback)
            button.pack(side='left')
            self.buttons[label] = button
        tk.Button(actions, text='Close', command=self.root.destroy).pack(side='right')
        for variable in (self.mileage, self.start, self.end):
            variable.trace_add('write', self.dirty)
        self.evaluation_date.trace_add('write', self.date_changed)
        self.root.bind('<FocusIn>', self.focus_return)
        self.refresh()

    def _region(self, page, row):
        page.rowconfigure(row, weight=1)
        region = tk.Frame(page, width=1, height=1)
        region.grid(row=row, column=0, sticky='nsew')
        region.rowconfigure(0, weight=1)
        region.columnconfigure(0, weight=1)
        return region

    def _text_region(self, page, *, row, height):
        region = self._region(page, row)
        widget = tk.Text(region, width=1, height=height, wrap='word')
        widget.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(region, orient='vertical', command=widget.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        widget.configure(yscrollcommand=scrollbar.set)
        return widget

    def _tree(self, parent, columns, *, row=0):
        region = self._region(parent, row)
        tree = ttk.Treeview(region, columns=columns, show='headings', selectmode='browse', height=4)
        for column in columns:
            tree.heading(column, text=column.replace('_', ' ').title())
            tree.column(column, width=125, minwidth=65, stretch=False)
        tree.grid(row=0, column=0, sticky='nsew')
        horizontal = ttk.Scrollbar(region, orient='horizontal', command=tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        vertical = ttk.Scrollbar(region, orient='vertical', command=tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        tree.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        return tree

    def selected_profile_id(self):
        index = self.profile_selector.current() - 1
        return self.profile_ids[index] if 0 <= index < len(self.profile_ids) else None

    def _buttons(self):
        for label, button in self.buttons.items():
            enabled = self.ready and self.model is not None and self.model['export_available']
            if label == 'Open Task / Full History':
                enabled = enabled and self.active_task_id is not None and self.open_task_callback is not None
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def dirty(self, *args):
        self.ready = False
        self.notice.set('Inputs changed — evaluate required. Navigation and exports are disabled.')
        self._buttons()

    def date_changed(self, *args):
        self.mileage.set('')
        self.dirty()

    def select_profile(self, event=None):
        self.mileage.set('')
        self.dirty()

    def _clear(self):
        self.ready = False
        self.model = self.snapshot = None
        self.active_task_id = None
        for tree in (self.forecasts, self.costs, self.recent, self.observations, self.rates):
            for child in tree.get_children():
                tree.delete(child)
        for widget in (self.quality, self.forecast_details):
            widget.configure(state=tk.NORMAL)
            widget.delete('1.0', tk.END)
            widget.configure(state=tk.DISABLED)
        self.totals.set('Unavailable — refresh required')
        self._buttons()

    def _check_sources(self):
        if self.snapshot is None:
            return False
        try:
            self.snapshot.revalidate()
            return True
        except (ValueError, OSError):
            self.mileage.set('')
            self._clear()
            self.notice.set('Source data changed or became unsafe — refresh required.')
            return False

    def focus_return(self, event=None):
        if event is None or event.widget is self.root:
            self._check_sources()

    def refresh(self, *, reset=False):
        try:
            self._refresh(reset=reset)
        except Exception as error:
            self._clear()
            self.notice.set('Insights unavailable — evaluation failed. Refresh required: ' + str(error))
            messagebox.showerror('Maintenance Insights', str(error), parent=self.root)

    def _refresh(self, *, reset=False):
        selected = self.selected_profile_id()
        if self.snapshot is not None:
            self._check_sources()
        if reset:
            selected = None
            self.evaluation_date.set(date.today().isoformat())
            for variable in (self.mileage, self.start, self.end):
                variable.set('')
        try:
            as_of = parse_date(self.evaluation_date.get())
            if as_of is None:
                raise ValueError('An evaluation date is required.')
            base = capture_insights(self.schedule_path, self.profiles_path, self.history_path, as_of=as_of)
            vehicles = base.model['vehicles']
            ids = [v['profile_id'] for v in vehicles]
            live_ids = {v['profile_id'] for v in vehicles if not v['profile_missing']}
            if selected not in ids or (selected in self.live_ids and selected not in live_ids):
                selected = None
                self.mileage.set('')
            text = self.mileage.get().strip()
            mileage = int(text) if text and all(c in '0123456789' for c in text) else text if text else None
            snapshot = capture_insights(self.schedule_path, self.profiles_path, self.history_path,
                as_of=as_of, selected_profile_id=selected if selected in live_ids else None,
                explicit_current_mileage=mileage, cost_start_date=self.start.get(), cost_end_date=self.end.get())
            model = snapshot.model
            base.revalidate()
        except (ValueError, OSError, OverflowError) as error:
            self.mileage.set('')
            self._clear()
            self.notice.set('Insights unavailable. Correct inputs/data or restore a valid backup, then refresh.')
            messagebox.showerror('Maintenance Insights', str(error), parent=self.root)
            return
        self.profile_ids, self.live_ids = ids, live_ids
        self.profile_selector.configure(values=['All profiles'] + [
            f"{v['vehicle_name']} [{v['profile_id']}]" + (' (missing profile)' if v['profile_missing'] else '') for v in vehicles])
        self.profile_selector.current(ids.index(selected) + 1 if selected else 0)
        self.snapshot, self.model, self.ready = snapshot, model, True
        self.active_task_id = None
        self.notice.set(model['input_error'] or 'Evaluated on ' + model['as_of'] + '. Estimates are separate from recorded thresholds. Mileage is never inferred or saved.')
        self.render()

    def render(self):
        model = self.model
        selected = self.selected_profile_id()
        def visible(pid):
            return selected is None or selected == pid
        self.totals.set(' | '.join(f'{s}: {n}' for s, n in model['status_totals'].items()))
        for tree in (self.forecasts, self.costs, self.recent, self.observations, self.rates):
            for child in tree.get_children():
                tree.delete(child)
        for row in model['tasks']:
            if not visible(row['profile_id']):
                continue
            task, (day, miles) = row['task'], row['axes']
            self.forecasts.insert('', 'end', iid=task['task_id'], values=(row['vehicle_name'], row['profile_id'],
                task['title'], row['status'], row['next_due_date'] or 'Not applicable', row['remaining_days'], day['availability'],
                row['next_due_mileage'], row['remaining_miles'], miles['availability'], miles['estimated_due_date'] or '—',
                '; '.join(a['unavailable_reason'] or '' for a in (day, miles))))
        details = ['Already due / overdue (separate from approaching):']
        details += [f"{r['profile_id']} | {r['task_id']} | {r['status']}" for r in model['already_due'] if visible(r['profile_id'])]
        details.append('Approaching within 0–30 days (cost filters ignored):')
        details += [f"{r['profile_id']} | {r['task_title']} | {r['forecast_axis']} | " +
                    str(r['next_due_date'] if r['forecast_axis'] == 'date' else r['estimated_due_date']) for r in model['approaching'] if visible(r['profile_id'])]
        self._text(self.forecast_details, '\n'.join(details))
        def cost_row(label, pid, costs):
            self.costs.insert('', 'end', values=(label, pid, costs['service_count'], costs['priced_service_count'],
                costs['unpriced_service_count'], costs['legacy_service_count'], *(format_money(costs[k]) for k in
                    ('total_cost_cents', 'average_cost_cents', 'min_cost_cents', 'max_cost_cents'))))
        cost_row('Overall (all profiles)', '', model['costs'])
        for vehicle in model['vehicles']:
            if visible(vehicle['profile_id']):
                cost_row(vehicle['vehicle_name'], vehicle['profile_id'], vehicle['costs'])
        for month in model['months']:
            cost_row('Month ' + month['month'] + ' (all profiles)', '', month)
        for wrapper in model['recent_services']:
            if visible(wrapper['profile_id']):
                s = wrapper['service']
                self.recent.insert('', 'end', values=(s['completed_date'], wrapper['profile_id'], wrapper['task_title'], s['mileage'], format_money(s.get('total_cost_cents'))))
        for point in model['observations']:
            if visible(point['profile_id']):
                provenance = '; '.join(f"{p['source_kind']} {p['source_record_ref']} SHA-256 {p['source_digest']}" for p in point['provenance'])
                self.observations.insert('', 'end', values=(point['profile_id'], point['observation_date'], point['observation_mileage'],
                                                          'Accepted' if point['observation_accepted'] else 'Excluded: future', provenance))
        for pid, rate in model['rates'].items():
            if visible(pid):
                self.rates.insert('', 'end', values=(pid, rate['availability'], rate['rate_start_date'], rate['rate_end_date'],
                    rate['rate_delta_miles'], rate['rate_delta_days'], rate['rate_miles_per_day'], rate['unavailable_reason']))
        quality = ['Observed endpoint rates; facts and estimates remain separate.']
        quality += [a['message'] for a in model['assumptions']]
        quality += [f"Source {s['source_kind']}: {s['availability']} {s['source_digest'] or ''}" for s in model['sources']]
        quality += [f"{w['severity']}: {w['code']} | {w.get('profile_id', '')} {w.get('task_id', '')} {w.get('service_id', '')} {w.get('source_record_ref', '')} | {w['message']}" for w in model['warnings']]
        self._text(self.quality, '\n'.join(quality))
        self._buttons()

    @staticmethod
    def _text(widget, value):
        widget.configure(state=tk.NORMAL)
        widget.delete('1.0', tk.END)
        widget.insert('1.0', value)
        widget.configure(state=tk.DISABLED)

    def select_task(self, event=None):
        selection = self.forecasts.selection()
        self.active_task_id = selection[0] if selection else None
        if self.active_task_id and self.model:
            row = next((r for r in self.model['tasks'] if r['task']['task_id'] == self.active_task_id), None)
            if row:
                self._text(self.forecast_details, '\n'.join([
                    'Recorded thresholds and independent estimates:',
                    *[f"{axis['forecast_axis']}: {axis['availability']}; {axis['unavailable_reason'] or ''}; "
                      f"reference: {axis['reference_kind'] or 'Not applicable'} {axis['reference_date'] or ''} "
                      f"{axis['reference_mileage'] if axis['reference_mileage'] is not None else ''}; "
                      f"estimated days: {axis['estimated_days']}; estimated date: {axis['estimated_due_date']}" for axis in row['axes']]]))
        self._buttons()

    def navigate(self, event=None):
        if not self.ready or not self.model or not self.model['export_available'] or not self.active_task_id or not self.open_task_callback:
            return
        if not self._check_sources():
            return
        try:
            self.open_task_callback(self.active_task_id)
        except (ValueError, OSError) as error:
            self._clear()
            self.notice.set('Navigation unavailable — refresh required.')
            messagebox.showerror('Maintenance Insights', str(error), parent=self.root)

    def export_csv(self):
        self._export('.csv', export_insights_csv)

    def export_html(self):
        self._export('.html', export_insights_html)

    def _export(self, extension, exporter):
        if not self.ready or not self.model or not self.model['export_available'] or not self._check_sources():
            return
        output = filedialog.asksaveasfilename(parent=self.root, title='Export Maintenance Insights',
            initialdir=str(self.schedule_path.parent / 'exports'), initialfile='maintenance-insights' + extension,
            defaultextension=extension, filetypes=[('Insights report', '*' + extension)], confirmoverwrite=False)
        if not output:
            return
        try:
            previous = output_fingerprint(output)
            overwrite = previous is not None
            if overwrite and not messagebox.askyesno('Overwrite Insights export', f'Replace {output}?', parent=self.root):
                return
            exporter(self.snapshot, output, overwrite=overwrite, expected_output=previous, runtime_paths=self.runtime_paths)
        except (ValueError, OSError) as error:
            self._check_sources()
            self.notice.set('Export unavailable: ' + str(error))
            messagebox.showerror('Insights export', str(error), parent=self.root)
            return
        messagebox.showinfo('Insights export', f'Report saved: {output}', parent=self.root)
