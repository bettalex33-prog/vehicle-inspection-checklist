"""Optional, passive Dashboard v2 window. Construct only with an existing parent.

The caller supplies a DashboardController and optional navigation callbacks. Each
callback accepts the owned presentation passed by controller.perform_action.
No capture, refresh, root window, clock, IO, or event loop starts on import or
construction. Call render() after external controller changes; Refresh is explicit.
"""
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
import tkinter as tk
from tkinter import ttk


PANELS = (
    ('inspection', 'Shared inspection progress', 'shared_inspection_progress'),
    ('maintenance', 'Maintenance status', 'maintenance'),
    ('forecasts', 'Mileage forecasts', 'mileage_forecasts'),
    ('costs', 'Service costs', 'service_cost_summary'),
    ('history', 'Recent service history', 'recent_services'),
    ('quality', 'Data quality', 'data_quality'),
    ('backup', 'Backup health', 'backup_health'),
)
ACTIONS = (
    ('inspection', 'Open inspection workflow'),
    ('maintenance', 'Open maintenance'),
    ('insights', 'Open maintenance insights'),
    ('history', 'Open history'),
)


def _text(value, indent=0):
    """Format supplied facts literally; None and zero remain distinct."""
    prefix = ' ' * indent
    if isinstance(value, Mapping):
        if not value:
            return prefix + 'No entries.'
        lines = []
        for key, item in value.items():
            label = str(key).replace('_', ' ')
            if key in ('next_due_date', 'next_due_mileage', 'remaining_days', 'remaining_miles'):
                label = 'Recorded ' + label
            if isinstance(item, (Mapping, list, tuple)):
                lines.extend((prefix + label + ':', _text(item, indent + 2)))
            else:
                lines.append(prefix + label + ': ' + _text(item))
        return '\n'.join(lines)
    if isinstance(value, (list, tuple)):
        return '\n\n'.join(_text(item, indent) for item in value) if value else prefix + 'No entries.'
    return prefix + ('Unavailable / not supplied' if value is None else str(value))


def _panel_text(presentation):
    """Check presentation structure, without validating or re-evaluating facts."""
    if not isinstance(presentation, Mapping):
        raise ValueError('Presentation is unavailable or malformed.')
    vehicles = presentation['vehicles']
    if not isinstance(vehicles, list):
        raise ValueError('Vehicle choices are malformed.')
    ids = []
    for vehicle in vehicles:
        pid = vehicle['profile_id']
        if type(pid) is not str or not pid or pid in ids or type(vehicle['vehicle_name']) is not str:
            raise ValueError('Vehicle choices require unique stable IDs and display names.')
        ids.append(pid)
    evaluation = presentation['evaluation']
    if not isinstance(evaluation, Mapping):
        raise ValueError('Evaluation presentation is malformed.')
    result = {}
    for key, title, field in PANELS:
        value = presentation[field]
        if not isinstance(value, list if key == 'history' else Mapping):
            raise ValueError(title + ' presentation is malformed.')
        result[key] = _text(value)
    result['inspection'] = 'Shared across all vehicle scopes.\n\n' + result['inspection']
    result['forecasts'] = ('Recorded thresholds and estimated forecasts are separate.\n'
        'Availability, reference kinds, dates, and reasons below are supplied facts.\n\n' + result['forecasts'])
    result['costs'] = ('Amounts are supplied in cents. Zero is distinct from unavailable amounts.\n'
        'Unpriced and legacy service counts are shown separately.\n\n' + result['costs'])
    identities = [dict(profile_id=v['profile_id'], vehicle_name=v['vehicle_name'],
                       profile_status='Missing profile; historical identity retained'
                       if v.get('profile_missing') else 'Live profile') for v in vehicles]
    result['history'] = 'Vehicle identities:\n' + _text(identities) + '\n\nRecent services:\n' + result['history']
    if presentation['backup_health'].get('availability') != 'Unavailable':
        raise ValueError('Backup health must remain unavailable; it is not implemented.')
    result['backup'] = 'Backup health: Unavailable — not implemented in this phase.\n\n' + result['backup']
    return result


class DashboardWindow:
    """Native ttk controls in a Toplevel; no default-app integration or storage."""
    def __init__(self, parent, controller, *, open_inspection=None,
                 open_maintenance=None, open_maintenance_insights=None, open_history=None):
        if parent is None:
            raise ValueError('An existing Tk parent is required; no root is created.')
        self.controller = controller
        self.callbacks = dict(inspection=open_inspection, maintenance=open_maintenance,
                              insights=open_maintenance_insights, history=open_history)
        self._closed = self._syncing = self._busy = self._form_dirty = False
        self._local_error = None
        self._shown_key = None
        self._seen_controller_revision = None
        self._choice_ids = [None]
        self.root = tk.Toplevel(parent)
        self.root.title('Dashboard v2')
        self.root.geometry('1100x740')
        self.root.minsize(680, 480)
        self.root.resizable(True, True)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)
        self.controls = ttk.Frame(self.root, padding=12)
        self.controls.grid(row=0, column=0, sticky='ew')
        self.controls.columnconfigure(1, weight=1)
        ttk.Label(self.controls, text='Vehicle scope').grid(row=0, column=0, sticky='w')
        self.selector = ttk.Combobox(self.controls, state='readonly', takefocus=True)
        self.selector.grid(row=0, column=1, sticky='ew', columnspan=3)
        self.selector.bind('<<ComboboxSelected>>', self.select_scope)
        self.variables, self.entries = {}, {}
        fields = (('as_of', 'Evaluation date (YYYY-MM-DD)'),
                  ('explicit_current_mileage', 'Explicit current mileage (session only)'),
                  ('cost_start_date', 'Cost start date (optional YYYY-MM-DD)'),
                  ('cost_end_date', 'Cost end date (optional YYYY-MM-DD)'))
        for row, (key, label) in enumerate(fields, 1):
            variable = tk.StringVar(master=self.root, value='')
            self.variables[key] = variable
            ttk.Label(self.controls, text=label).grid(row=row, column=0, sticky='w')
            entry = ttk.Entry(self.controls, textvariable=variable, takefocus=True)
            entry.grid(row=row, column=1, columnspan=3, sticky='ew')
            self.entries[key] = entry
            entry.bind('<Return>', self.refresh)
            variable.trace_add('write', self.inputs_changed)
        self.refresh_button = ttk.Button(self.controls, text='Refresh', command=self.refresh, takefocus=True)
        self.refresh_button.grid(row=5, column=1, sticky='w', pady=6)
        self.notice = tk.StringVar(master=self.root, value='')
        self.content_notice = tk.StringVar(master=self.root, value='')
        ttk.Label(self.root, textvariable=self.notice, wraplength=650).grid(row=1, column=0, sticky='ew', padx=12)
        ttk.Label(self.root, textvariable=self.content_notice, wraplength=650).grid(row=2, column=0, sticky='ew', padx=12)
        self.notebook = ttk.Notebook(self.root, takefocus=True)
        self.notebook.grid(row=3, column=0, sticky='nsew', padx=12, pady=6)
        self.panels = {}
        for key, title, field in PANELS:
            page = ttk.Frame(self.notebook)
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            self.notebook.add(page, text=title)
            text = tk.Text(page, wrap='none', width=1, height=6, takefocus=True)
            text.grid(row=0, column=0, sticky='nsew')
            vertical = ttk.Scrollbar(page, orient='vertical', command=text.yview)
            horizontal = ttk.Scrollbar(page, orient='horizontal', command=text.xview)
            vertical.grid(row=0, column=1, sticky='ns')
            horizontal.grid(row=1, column=0, sticky='ew')
            text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            text.bind('<Tab>', self._next_control)
            backward = lambda event: self._next_control(event, backward=True)
            # Tk keysyms vary by platform. Each alias is optional; Tk chooses
            # one matching binding per widget, and 'break' stops class traversal.
            for sequence in ('<Shift-Tab>', '<ISO_Left_Tab>'):
                try:
                    text.bind(sequence, backward)
                except tk.TclError:
                    pass
            self.panels[key] = text
        actions = ttk.Frame(self.root, padding=12)
        actions.grid(row=4, column=0, sticky='ew')
        self.buttons = {}
        for column, (key, label) in enumerate(ACTIONS):
            button = ttk.Button(actions, text=label, command=lambda name=key: self.navigate(name), takefocus=True)
            button.grid(row=column // 2, column=column % 2, sticky='w', padx=3, pady=3)
            self.buttons[key] = button
        ttk.Button(actions, text='Close', command=self.close, takefocus=True).grid(row=2, column=1, sticky='e')
        self.render(sync_inputs=True)

    @staticmethod
    def _next_control(event, backward=False):
        target = event.widget.tk_focusPrev() if backward else event.widget.tk_focusNext()
        if target is not None:
            target.focus_set()
        return 'break'

    def _state(self):
        state = deepcopy(self.controller.state)
        if not isinstance(state, Mapping):
            raise ValueError('Controller state is unavailable.')
        return state

    def _set_inputs(self, state):
        self._syncing = True
        try:
            for key, variable in self.variables.items():
                value = state.get(key)
                variable.set(value.isoformat() if type(value) is date else '' if value is None else str(value))
        finally:
            self._syncing = False

    def _choices(self, state, presentation):
        ids, labels = [None], ['All vehicles']
        if presentation is not None:
            for vehicle in presentation['vehicles']:
                ids.append(vehicle['profile_id'])
                labels.append(vehicle['vehicle_name'] + ' [' + vehicle['profile_id'] + ']' +
                              (' (missing profile)' if vehicle.get('profile_missing') else ''))
        selected = state.get('selected_profile_id')
        if selected not in ids:
            ids.append(selected)
            labels.append('Unavailable profile [' + str(selected) + ']')
        self._choice_ids = ids
        self.selector.configure(values=labels)
        self.selector.current(ids.index(selected))

    def _eligible(self, state, presentation):
        if presentation is None or self._busy or self._form_dirty or self._local_error:
            return False
        evaluation = presentation['evaluation']
        selected = state.get('selected_profile_id')
        if selected is not None and (type(selected) is not str or not any(
                v['profile_id'] == selected and not v.get('profile_missing')
                for v in presentation['vehicles'])):
            return False
        return (state.get('status') == 'ready' and state.get('action_ready') is True
            and not any(state.get(k) for k in ('loading', 'stale', 'dirty', 'error', 'action_in_progress'))
            and type(state.get('publication_generation')) is int
            and state['publication_generation'] > 0
            and type(state.get('presentation_generation')) is int
            and state.get('presentation_generation') == state.get('publication_generation')
            and evaluation.get('input_error') is None and evaluation.get('export_available') is True
            and presentation.get('scope_profile_id') == state.get('selected_profile_id')
            and presentation.get('as_of') == str(state.get('as_of'))
            and evaluation.get('explicit_current_mileage') == state.get('explicit_current_mileage')
            and all(presentation['service_cost_summary'].get(key) == state.get(key)
                    for key in ('cost_start_date', 'cost_end_date'))
            and presentation['service_cost_summary'].get('availability') == 'Available'
            and presentation['shared_inspection_progress'].get('availability') == 'Available')

    def render(self, *, sync_inputs=None):
        """Read-only controller access; retained output always has a stale label."""
        if self._closed:
            return
        try:
            state = self._state()
        except Exception as error:
            state = dict(status='error', error=str(error) or type(error).__name__)
        if sync_inputs is True or (sync_inputs is None and not self._form_dirty):
            self._set_inputs(state)
        elif (sync_inputs is None and self._seen_controller_revision is not None
                and state.get('revision') != self._seen_controller_revision
                and (state.get('dirty') or state.get('error'))
                and state.get('explicit_current_mileage') is None):
            # An external write/restore notification also clears an unsubmitted
            # mileage draft; it must not silently resurrect a cleared session input.
            self._syncing = True
            try:
                self.variables['explicit_current_mileage'].set('')
            finally:
                self._syncing = False
        raw = state.get('presentation')
        retained = raw is None
        if retained:
            raw = state.get('last_successful_presentation')
        presentation, panel_text, malformed = None, {}, None
        if raw is not None:
            try:
                panel_text = _panel_text(raw)
                presentation = raw
            except Exception as error:
                malformed = 'Presentation unavailable: ' + (str(error) or type(error).__name__)
        self._choices(state, presentation)
        enabled = self._eligible(state, presentation) and not retained and malformed is None
        evaluation_error = presentation['evaluation'].get('input_error') if presentation is not None else None
        stale = (not enabled or retained or self._form_dirty or self._local_error or self._busy
                 or state.get('loading') or state.get('stale') or state.get('dirty') or state.get('error'))
        if presentation is None:
            self.content_notice.set('Unavailable — no usable presentation. Refresh required.')
        else:
            context = 'Scope: ' + str(presentation.get('scope_profile_id') or 'All vehicles') + '; evaluated on ' + str(presentation.get('as_of', 'Unavailable'))
            self.content_notice.set(('STALE retained content — not current. ' if stale else 'Current presentation. ') + context)
        message = ('Loading / revalidating sources — actions disabled.' if self._busy or state.get('loading') else
                   'Inputs changed — refresh required.' if self._form_dirty else
                   'Ready.' if enabled else
                   'Unavailable — refresh required.' if state.get('status') == 'ready' else
                   'Unavailable — refresh required.' if state.get('status') == 'empty' else
                   str(state.get('status', 'Unavailable')).capitalize() + ' — refresh required.')
        errors = [str(value) for value in (state.get('error'), self._local_error, malformed, evaluation_error) if value]
        self.notice.set(message + (' Error: ' + ' | '.join(errors) if errors else ''))
        for key, button in self.buttons.items():
            button.configure(state='normal' if enabled and self.callbacks[key] is not None else 'disabled')
        busy = self._busy or state.get('loading') or state.get('action_in_progress')
        self.refresh_button.configure(state='disabled' if busy else 'normal')
        self.selector.configure(state='disabled' if busy else 'readonly')
        for entry in self.entries.values():
            entry.configure(state='disabled' if busy else 'normal')
        for key, widget in self.panels.items():
            widget.configure(state='normal')
            widget.delete('1.0', 'end')
            widget.insert('1.0', panel_text.get(key, 'Unavailable — no usable presentation.'))
            widget.configure(state='disabled')
        self._shown_key = (state.get('revision'), state.get('publication_generation'))
        self._seen_controller_revision = state.get('revision')

    def inputs_changed(self, *args):
        if self._syncing or self._closed:
            return
        self._form_dirty, self._local_error = True, None
        self.render()

    def _selected_id(self):
        index = self.selector.current()
        if type(index) is not int or not 0 <= index < len(self._choice_ids):
            raise ValueError('Select a listed vehicle scope.')
        return self._choice_ids[index]

    def select_scope(self, event=None):
        if self._closed or self._busy:
            return
        try:
            state = self._state()
            if state.get('loading') or state.get('action_in_progress'):
                return
            self.controller.select_scope(self._selected_id())
            state = self._state()
            self._syncing = True
            try:
                value = state.get('explicit_current_mileage')
                self.variables['explicit_current_mileage'].set('' if value is None else str(value))
            finally:
                self._syncing = False
            self._local_error = None
        except Exception as error:
            self._local_error = str(error) or type(error).__name__
        self.render()

    @staticmethod
    def _date_input(text, *, required=False):
        text = text.strip()
        if not text and not required:
            return None
        try:
            parsed = date.fromisoformat(text)
            if parsed.isoformat() == text:
                return parsed
        except ValueError:
            pass
        raise ValueError('Dates must use YYYY-MM-DD; an evaluation date is required.')

    def refresh(self, event=None):
        if self._closed or self._busy:
            return
        self._busy = True
        success = False
        try:
            state = self._state()
            if state.get('loading') or state.get('action_in_progress'):
                return
            pid = self._selected_id()
            as_of = self._date_input(self.variables['as_of'].get(), required=True)
            start = self._date_input(self.variables['cost_start_date'].get())
            end = self._date_input(self.variables['cost_end_date'].get())
            if start and end and start > end:
                raise ValueError('Cost start date must not exceed end date.')
            text = self.variables['explicit_current_mileage'].get().strip()
            if text and (pid is None or any(c not in '0123456789' for c in text)):
                raise ValueError('Explicit mileage requires one vehicle and nonnegative integer digits.')
            mileage = int(text) if text else None
            self.render()
            self.root.update_idletasks()  # Paint loading, without a nested event loop.
            # Setters retain invalid form values. Apply all corrections before
            # refresh validates their final combination (no analytical work here).
            for setter, args in ((self.controller.select_scope, (pid,)),
                                 (self.controller.set_evaluation_date, (as_of,)),
                                 (self.controller.set_cost_bounds, (start.isoformat() if start else None, end.isoformat() if end else None)),
                                 (self.controller.set_explicit_mileage, (mileage,))):
                try:
                    setter(*args)
                except ValueError:
                    pass
            expected = dict(selected_profile_id=pid, as_of=as_of,
                explicit_current_mileage=mileage, cost_start_date=start.isoformat() if start else None,
                cost_end_date=end.isoformat() if end else None)
            applied = self._state()
            if any(type(applied.get(key)) is not type(value) or applied.get(key) != value
                   for key, value in expected.items()):
                raise ValueError('Controller rejected inputs; refresh was not performed.')
            self.controller.refresh()
            success = True
            self._form_dirty, self._local_error = False, None
        except Exception as error:
            self._form_dirty = True
            self._local_error = str(error) or type(error).__name__
        finally:
            self._busy = False
            self.render(sync_inputs=success)

    def navigate(self, name):
        if self._closed or self._busy or name not in self.callbacks or self.callbacks[name] is None:
            return
        try:
            state = self._state()
            presentation = state.get('presentation')
            _panel_text(presentation)
            if not self._eligible(state, presentation):
                return
            if self._shown_key != (state.get('revision'), state.get('publication_generation')):
                raise ValueError('Dashboard changed; refresh and review before opening a workflow.')
            self._busy = True
            self.render()
            self.root.update_idletasks()
            expected = (state.get('revision'), state.get('publication_generation'))
            def approved(presentation):
                current = self._state()
                if (self._form_dirty or self._local_error or self._closed
                        or self._selected_id() != current.get('selected_profile_id')
                        or expected != (current.get('revision'), current.get('publication_generation'))):
                    raise ValueError('Dashboard changed; refresh and review before opening a workflow.')
                return self.callbacks[name](presentation)
            self.controller.perform_action(approved)
        except Exception as error:
            self._local_error = str(error) or type(error).__name__
        finally:
            self._busy = False
            self.render(sync_inputs=not self._form_dirty)

    def close(self):
        if not self._closed:
            self._closed = True
            self.root.destroy()
