"""Fake Tk components and fake controllers only; never construct a real GUI."""
from copy import deepcopy
from datetime import date
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dashboard_gui as dg

DAY = date(2026, 10, 31)
PID, OTHER, ORPHAN = 'stable-a', 'stable-b', 'historical-id'


def presentation():
    return dict(as_of=DAY.isoformat(), scope_profile_id=None,
        evaluation=dict(selected_profile_id=None, explicit_current_mileage=None,
                        input_error=None, export_available=True),
        vehicles=[dict(profile_id=PID, vehicle_name='Same name', profile_missing=False,
                       profile={'mileage': 987654}),
                  dict(profile_id=OTHER, vehicle_name='Same name', profile_missing=False),
                  dict(profile_id=ORPHAN, vehicle_name='Old vehicle', profile_missing=True)],
        shared_inspection_progress=dict(availability='Available', scope='shared', completed=0,
                                        total=4, percentage=0),
        maintenance=dict(status_totals={'Upcoming': 2, 'Overdue': 1, 'Current': 3, 'Archived': 4},
                         tasks=[dict(profile_id=PID, status='Upcoming', task={'title': 'Oil'},
                                     next_due_mileage=1000, next_due_date='2027-01-01')]),
        mileage_forecasts=dict(tasks=[dict(profile_id=PID, next_due_mileage=1000,
            axes=[dict(forecast_axis='mileage', availability='Available',
                       reference_kind='Last recorded observation', reference_mileage=100,
                       estimated_due_date='2027-03-04', estimated_days=120),
                  dict(forecast_axis='date', availability='Unavailable',
                       unavailable_reason='no_dated_history', estimated_due_date=None)])],
            rates={PID: dict(availability='Unavailable', unavailable_reason='insufficient_observations')}),
        service_cost_summary=dict(availability='Available', cost_start_date=None, cost_end_date=None,
            service_count=3, priced_service_count=1, unpriced_service_count=2,
            legacy_service_count=1, total_cost_cents=0, average_cost_cents=0,
            min_cost_cents=0, max_cost_cents=0),
        recent_services=[dict(profile_id=ORPHAN, task_id='oil-id', task_title='Historical oil',
            service=dict(service_id='service-1', completed_date='2026-10-01', mileage=100,
                         notes='Retained history'))],
        data_quality=dict(warnings=[dict(severity='warning', code='missing_live_profile',
            profile_id=ORPHAN, message='Historical identity remains separate.')], sources=[], assumptions=[]),
        backup_health=dict(availability='Unavailable', unavailable_reason='backup_inspection_not_implemented'))


class FakeController:
    """Expose shared mappings deliberately, to detect view-side mutations."""
    def __init__(self, ready=False):
        self.data = dict(as_of=DAY, cost_start_date=None, cost_end_date=None,
            selected_profile_id=None, explicit_current_mileage=None, status='empty',
            revision=0, publication_generation=0, presentation_generation=None,
            last_successful_generation=None, loading=False, dirty=False, stale=False,
            error=None, action_ready=False, action_in_progress=False,
            presentation=None, last_successful_presentation=None)
        self.template = presentation()
        self.calls = []
        self.refresh_error = self.action_error = None
        self.refresh_hook = self.revalidation_hook = None
        if ready:
            self.refresh()
            self.calls.clear()

    @property
    def state(self):
        return self.data

    def _dirty(self):
        self.data.update(revision=self.data['revision'] + 1, status='dirty', dirty=True,
            presentation=None, presentation_generation=None, action_ready=False,
            stale=self.data['last_successful_presentation'] is not None, error=None)

    def select_scope(self, pid):
        self.calls.append(('scope', pid))
        if pid is not None and (type(pid) is not str or not pid):
            raise ValueError('Invalid stable ID')
        if pid != self.data['selected_profile_id']:
            self.data.update(selected_profile_id=pid, explicit_current_mileage=None)
            self._dirty()

    def set_evaluation_date(self, value):
        self.calls.append(('as_of', value))
        if value != self.data['as_of']:
            self.data['as_of'] = value
            self._dirty()

    def set_cost_bounds(self, start, end):
        self.calls.append(('bounds', start, end))
        if (start, end) != (self.data['cost_start_date'], self.data['cost_end_date']):
            self.data.update(cost_start_date=start, cost_end_date=end)
            self._dirty()

    def set_explicit_mileage(self, value):
        self.calls.append(('mileage', value))
        if value != self.data['explicit_current_mileage']:
            self.data['explicit_current_mileage'] = value
            self._dirty()

    def invalidate(self):
        self.data['explicit_current_mileage'] = None
        self._dirty()

    def _fail(self, message):
        self._dirty()
        self.data.update(status='error', error=message)

    def refresh(self):
        self.calls.append(('refresh',))
        if self.refresh_hook:
            self.refresh_hook()
        pid = self.data['selected_profile_id']
        if self.refresh_error or pid not in (None, PID, OTHER):
            message = self.refresh_error or 'Selected profile ID unavailable'
            self._fail(message)
            raise ValueError(message)
        output = deepcopy(self.template)
        output.update(as_of=str(self.data['as_of']), scope_profile_id=pid)
        output['evaluation'].update(selected_profile_id=pid,
            explicit_current_mileage=self.data['explicit_current_mileage'])
        output['service_cost_summary'].update(cost_start_date=self.data['cost_start_date'],
                                              cost_end_date=self.data['cost_end_date'])
        generation = self.data['publication_generation'] + 1
        self.data.update(status='ready', revision=self.data['revision'] + 1,
            publication_generation=generation, presentation_generation=generation,
            last_successful_generation=generation, presentation=output,
            last_successful_presentation=deepcopy(output), action_ready=True,
            dirty=False, stale=False, error=None)
        return self.data

    def perform_action(self, callback):
        self.calls.append(('revalidate',))
        if self.revalidation_hook:
            self.revalidation_hook()
        if self.action_error:
            self._fail(self.action_error)
            raise ValueError(self.action_error)
        if not self.data['action_ready']:
            raise ValueError('Action requires current sources')
        self.data['action_in_progress'] = True
        try:
            return callback(deepcopy(self.data['presentation']))
        finally:
            self.data['action_in_progress'] = False


class Variable:
    def __init__(self, master=None, value=''):
        self.master, self.value, self.traces = master, value, []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.traces:
            callback('', '', 'write')

    def trace_add(self, mode, callback):
        self.traces.append(callback)


class Widget:
    def __init__(self, kind, parent=None, **options):
        self.kind, self.parent, self.options = kind, parent, dict(options)
        self.bindings, self.protocols, self.tabs = {}, {}, []
        self.index, self.content, self.destroyed = -1, '', False
        self.grid_options = self.minimum = self.size = self.resize = None
        self.rows, self.columns = {}, {}
        self.idle_hook = None
        self.focused = False

    def configure(self, **options):
        self.options.update(options)

    def grid(self, **options):
        self.grid_options = options

    def rowconfigure(self, index, **options):
        self.rows[index] = options

    def columnconfigure(self, index, **options):
        self.columns[index] = options

    def bind(self, event, callback):
        self.bindings[event] = callback

    def current(self, index=None):
        if index is not None:
            self.index = index
        return self.index

    def title(self, text):
        self.title_text = text

    def geometry(self, size):
        self.size = size

    def minsize(self, *dimensions):
        self.minimum = dimensions

    def resizable(self, *values):
        self.resize = values

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def add(self, page, **options):
        self.tabs.append((page, options))

    def delete(self, *args):
        self.content = ''

    def insert(self, index, text):
        self.content += text

    def xview(self, *args):
        pass

    def yview(self, *args):
        pass

    def set(self, *args):
        pass

    def update_idletasks(self):
        if self.idle_hook:
            self.idle_hook()

    def destroy(self):
        self.destroyed = True

    def invoke(self):
        if self.options.get('state') != 'disabled':
            return self.options['command']()

    def tk_focusNext(self):
        return self

    def tk_focusPrev(self):
        return self

    def focus_set(self):
        self.focused = True


class TestDashboardGUI(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.parent = Widget('existing parent')
        for namespace, names in ((dg.tk, ('Toplevel', 'Text')),
                                 (dg.ttk, ('Frame', 'Label', 'Entry', 'Combobox', 'Button', 'Notebook', 'Scrollbar'))):
            for name in names:
                def create(parent=None, _kind=name, **options):
                    widget = Widget(_kind, parent, **options)
                    self.created.append(widget)
                    return widget
                patcher = patch.object(namespace, name, side_effect=create)
                patcher.start()
                self.addCleanup(patcher.stop)
        patcher = patch.object(dg.tk, 'StringVar', Variable)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('Tk', 'mainloop'):
            patcher = patch.object(dg.tk, name, side_effect=AssertionError('Root/event loop forbidden'))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = FakeController()
        self.callbacks = {key: Mock() for key, label in dg.ACTIONS}
        self.window = self.new_window()

    def new_window(self, controller=None, **callbacks):
        defaults = dict(open_inspection=self.callbacks['inspection'],
            open_maintenance=self.callbacks['maintenance'], open_maintenance_insights=self.callbacks['insights'],
            open_history=self.callbacks['history'])
        defaults.update(callbacks)
        return dg.DashboardWindow(self.parent, controller or self.controller, **defaults)

    def ready(self):
        self.controller.refresh()
        self.window.render()
        return self.window

    def choose(self, pid):
        self.window.selector.current(self.window._choice_ids.index(pid))
        self.window.selector.bindings['<<ComboboxSelected>>'](SimpleNamespace(widget=self.window.selector))

    def assert_disabled(self):
        self.assertTrue(all(button.options['state'] == 'disabled' for button in self.window.buttons.values()))

    def test_import_has_no_startup_effects(self):
        with patch.object(dg.tk, 'Toplevel', side_effect=AssertionError('No import window')), \
             patch.object(dg.ttk, 'Frame', side_effect=AssertionError('No import widgets')):
            importlib.reload(dg)
        self.assertEqual(self.controller.calls, [])

    def test_constructor_requires_existing_parent(self):
        with self.assertRaises(ValueError):
            dg.DashboardWindow(None, self.controller)

    def test_initial_empty_state_does_not_refresh_or_infer_mileage(self):
        self.assertEqual(self.controller.calls, [])
        self.assertIn('Unavailable', self.window.notice.get())
        self.assertIn('no usable presentation', self.window.content_notice.get())
        self.assertEqual(self.window.variables['as_of'].get(), '2026-10-31')
        self.assertEqual(self.window.variables['explicit_current_mileage'].get(), '')
        self.assertEqual(self.window.selector.options['values'], ['All vehicles'])
        self.assert_disabled()

    def test_existing_ready_controller_is_rendered_without_capture(self):
        controller = FakeController(ready=True)
        window = self.new_window(controller)
        self.assertEqual(controller.calls, [])
        self.assertIn('Current presentation', window.content_notice.get())
        self.assertTrue(all(button.options['state'] == 'normal' for button in window.buttons.values()))

    def test_no_second_root_or_mainloop_and_close_only_destroys_toplevel(self):
        self.assertEqual([w.kind for w in self.created].count('Toplevel'), 1)
        self.assertIs(self.window.root.parent, self.parent)
        self.window.root.protocols['WM_DELETE_WINDOW']()
        self.window.close()
        self.assertTrue(self.window.root.destroyed)
        self.assertFalse(self.parent.destroyed)
        self.assertEqual(self.controller.calls, [])
        self.window.refresh()
        self.window.navigate('history')
        self.assertEqual(self.controller.calls, [])

    def test_resizable_native_layout_and_scrollable_keyboard_regions(self):
        self.assertEqual(self.window.root.minimum, (680, 480))
        self.assertEqual(self.window.root.resize, (True, True))
        self.assertEqual(len(self.window.notebook.tabs), 7)
        self.assertEqual(len([w for w in self.created if w.kind == 'Scrollbar']), 14)
        for widget in self.created:
            self.assertFalse(set(widget.options).intersection({'style', 'font', 'foreground', 'background'}))
        for text in self.window.panels.values():
            self.assertEqual(text.options['state'], 'disabled')
            self.assertTrue(text.options['takefocus'])
            self.assertIn('xscrollcommand', text.options)
            self.assertIn('yscrollcommand', text.options)
            for event in ('<Tab>', '<Shift-Tab>', '<ISO_Left_Tab>'):
                self.assertEqual(text.bindings[event](SimpleNamespace(widget=text)), 'break')
                self.assertTrue(text.focused)

    def test_successful_refresh_renders_every_panel(self):
        self.window.refresh()
        self.assertIn('Current presentation', self.window.content_notice.get())
        self.assertIn('Ready', self.window.notice.get())
        self.assertIn('completed: 0', self.window.panels['inspection'].content)
        for status, value in self.controller.template['maintenance']['status_totals'].items():
            self.assertIn(status + ': ' + str(value), self.window.panels['maintenance'].content)
        self.assertIn('Recorded next due mileage: 1000', self.window.panels['maintenance'].content)
        self.assertIn('Historical oil', self.window.panels['history'].content)
        self.assertIn('missing_live_profile', self.window.panels['quality'].content)
        self.assertIn('Unavailable — not implemented', self.window.panels['backup'].content)

    def test_duplicate_display_names_have_distinct_stable_id_choices(self):
        self.ready()
        labels = self.window.selector.options['values']
        self.assertIn('Same name [stable-a]', labels)
        self.assertIn('Same name [stable-b]', labels)
        for pid in (PID, OTHER):
            self.choose(pid)
            self.assertEqual(self.controller.calls[-1], ('scope', pid))
            self.assertEqual(self.controller.state['selected_profile_id'], pid)

    def test_all_vehicle_scope_is_none_and_never_a_display_name(self):
        self.ready()
        self.choose(PID)
        self.choose(None)
        self.assertEqual(self.controller.calls[-1], ('scope', None))
        self.window.refresh()
        self.assertIsNone(self.controller.state['presentation']['scope_profile_id'])
        self.assertNotIn(('scope', 'All vehicles'), self.controller.calls)

    def test_unknown_selected_id_remains_explicit_without_fallback(self):
        self.ready()
        self.controller.select_scope('unknown-id')
        self.window.render()
        self.assertEqual(self.window._selected_id(), 'unknown-id')
        self.assertIn('Unavailable profile [unknown-id]', self.window.selector.options['values'])
        self.window.refresh()
        self.assertEqual(self.controller.state['selected_profile_id'], 'unknown-id')
        self.assertIn('Selected profile ID unavailable', self.window.notice.get())
        self.assert_disabled()

    def test_invalid_selector_event_does_not_fall_back_to_all(self):
        self.ready()
        self.choose(PID)
        for index in (-1, 999, True):
            self.window.selector.current(index)
            self.window.select_scope()
            self.assertEqual(self.controller.state['selected_profile_id'], PID)
            self.assertIn('Select a listed vehicle scope', self.window.notice.get())
            self.assert_disabled()

    def test_scope_change_clears_displayed_explicit_mileage(self):
        self.ready()
        self.choose(PID)
        self.window.variables['explicit_current_mileage'].set('123')
        self.window.refresh()
        self.assertEqual(self.controller.state['explicit_current_mileage'], 123)
        self.choose(OTHER)
        self.assertEqual(self.window.variables['explicit_current_mileage'].get(), '')
        self.assertIsNone(self.controller.state['explicit_current_mileage'])
        self.assertIn('STALE', self.window.content_notice.get())
        self.assert_disabled()

    def test_scope_change_does_not_change_shared_inspection_facts(self):
        self.ready()
        progress = self.window.panels['inspection'].content
        self.choose(PID)
        self.window.refresh()
        self.choose(OTHER)
        self.window.refresh()
        self.assertEqual(self.window.panels['inspection'].content, progress)

    def test_exact_input_forwarding_and_blank_optional_bounds(self):
        self.ready()
        self.choose(PID)
        for key, value in dict(as_of='2026-10-30', explicit_current_mileage='000123',
                               cost_start_date='2026-01-01', cost_end_date='2026-10-29').items():
            self.window.variables[key].set(value)
        self.controller.calls.clear()
        self.window.refresh()
        self.assertEqual(self.controller.calls, [('scope', PID), ('as_of', date(2026, 10, 30)),
            ('bounds', '2026-01-01', '2026-10-29'), ('mileage', 123), ('refresh',)])
        self.window.variables['cost_start_date'].set('')
        self.window.variables['cost_end_date'].set('')
        self.window.variables['explicit_current_mileage'].set('')
        self.window.refresh()
        self.assertIn(('bounds', None, None), self.controller.calls)
        self.assertEqual(self.controller.state['explicit_current_mileage'], None)

    def test_invalid_dates_bounds_and_mileage_remain_visible_without_mutating_controller(self):
        self.ready()
        self.choose(PID)
        self.window.refresh()
        good = {key: var.get() for key, var in self.window.variables.items()}
        cases = [('as_of', ''), ('as_of', '2026-02-30'), ('as_of', '20261031'),
                 ('cost_start_date', 'bad'), ('cost_end_date', 'bad'),
                 ('explicit_current_mileage', '-1'), ('explicit_current_mileage', '1.2'),
                 ('explicit_current_mileage', 'true'), ('explicit_current_mileage', '１２３')]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                for field, text in good.items():
                    self.window.variables[field].set(text)
                before = deepcopy(self.controller.data)
                self.controller.calls.clear()
                self.window.variables[key].set(value)
                self.window.refresh()
                self.assertEqual(self.window.variables[key].get(), value)
                self.assertIn('Error:', self.window.notice.get())
                self.assertEqual(self.controller.data, before)
                self.assertEqual(self.controller.calls, [])
                self.assert_disabled()
        self.window.variables['as_of'].set('2026-10-31')
        self.window.variables['explicit_current_mileage'].set('')
        self.window.variables['cost_start_date'].set('2026-10-31')
        self.window.variables['cost_end_date'].set('2026-01-01')
        self.window.refresh()
        self.assertIn('must not exceed', self.window.notice.get())

    def test_explicit_mileage_rejected_for_all_vehicle_scope(self):
        self.ready()
        self.window.variables['explicit_current_mileage'].set('1')
        self.controller.calls.clear()
        self.window.refresh()
        self.assertIn('requires one vehicle', self.window.notice.get())
        self.assertEqual(self.controller.calls, [])
        self.assert_disabled()

    def test_profile_mileage_is_never_used_as_current_input(self):
        self.ready()
        self.choose(PID)
        self.window.refresh()
        self.assertIn(('mileage', None), self.controller.calls)
        self.assertEqual(self.window.variables['explicit_current_mileage'].get(), '')
        self.assertNotIn('987654', self.window.panels['history'].content)

    def test_unsubmitted_edit_marks_visible_content_stale_and_blocks_actions(self):
        self.ready()
        self.window.variables['as_of'].set('2026-10-30')
        self.assertIn('STALE retained content', self.window.content_notice.get())
        self.assertIn('Inputs changed', self.window.notice.get())
        self.assert_disabled()
        self.window.navigate('inspection')
        self.callbacks['inspection'].assert_not_called()

    def test_failed_refresh_labels_retained_success_as_stale(self):
        self.ready()
        old = self.window.panels['maintenance'].content
        self.controller.refresh_error = 'corrupt analytical store'
        self.window.refresh()
        self.assertIn('STALE retained content', self.window.content_notice.get())
        self.assertIn('corrupt analytical store', self.window.notice.get())
        self.assertEqual(self.window.panels['maintenance'].content, old)
        self.assert_disabled()
        self.controller.refresh_error = None
        self.window.refresh()
        self.assertIn('Current presentation', self.window.content_notice.get())
        self.assertTrue(all(b.options['state'] == 'normal' for b in self.window.buttons.values()))

    def test_missing_corrupt_and_unavailable_presentations_clear_content_safely(self):
        self.ready()
        for value in (None, {}, [], 'bad', {'vehicles': []}):
            with self.subTest(value=value):
                self.controller.data.update(presentation=value, last_successful_presentation=None)
                self.window.render()
                self.assertIn('Unavailable', self.window.content_notice.get())
                self.assert_disabled()
                self.assertTrue(all('no usable presentation' in panel.content for panel in self.window.panels.values()))
        self.controller.data['presentation'] = presentation()
        self.controller.data['presentation']['maintenance'] = None
        self.window.render()
        self.assertIn('malformed', self.window.notice.get())
        self.assert_disabled()

    def test_unavailable_or_malformed_controller_state_is_visible(self):
        self.controller.data = None
        self.window.render()
        self.assertIn('Controller state is unavailable', self.window.notice.get())
        self.assert_disabled()
        self.window.refresh()
        self.assertIn('Controller state is unavailable', self.window.notice.get())

    def test_loading_and_failed_flags_disable_actions_even_with_old_presentation(self):
        for flags in (dict(status='loading', loading=True), dict(status='error', error='failed'),
                      dict(status='dirty', dirty=True), dict(stale=True), dict(action_in_progress=True),
                      dict(action_ready=False), dict(presentation_generation=999),
                      dict(publication_generation=0, presentation_generation=0),
                      dict(publication_generation=True, presentation_generation=True)):
            with self.subTest(flags=flags):
                self.controller = FakeController(ready=True)
                self.window.controller = self.controller
                self.controller.data.update(flags)
                self.window.render()
                self.assert_disabled()
                self.window.navigate('history')
                self.callbacks['history'].assert_not_called()
        self.controller.data.update(status='loading', loading=True)
        self.window.render()
        self.assertIn('Loading', self.window.notice.get())
        self.assertEqual(self.window.refresh_button.options['state'], 'disabled')

    def test_loading_is_painted_and_nested_refresh_or_actions_are_blocked(self):
        def during_refresh():
            self.assertIn('Loading', self.window.notice.get())
            self.assert_disabled()
            self.window.refresh()
            self.window.navigate('history')
        self.controller.refresh_hook = during_refresh
        self.window.refresh()
        self.assertEqual(self.controller.calls.count(('refresh',)), 1)
        self.callbacks['history'].assert_not_called()

    def test_optional_callbacks_are_disabled(self):
        self.ready()
        window = self.new_window(open_inspection=None, open_history=None)
        self.assertEqual(window.buttons['inspection'].options['state'], 'disabled')
        self.assertEqual(window.buttons['history'].options['state'], 'disabled')
        self.assertEqual(window.buttons['maintenance'].options['state'], 'normal')
        window.navigate('inspection')
        window.navigate('unknown')
        self.callbacks['inspection'].assert_not_called()

    def test_every_navigation_callback_passes_through_immediate_revalidation(self):
        self.ready()
        for name, callback in self.callbacks.items():
            self.controller.calls.clear()
            def consume(payload):
                self.assertEqual(self.controller.calls, [('revalidate',)])
                self.assertTrue(self.controller.state['action_in_progress'])
                payload['maintenance']['status_totals'].clear()
            callback.side_effect = consume
            self.window.buttons[name].invoke()
            callback.assert_called_once()
            self.assertEqual(self.controller.data['presentation']['maintenance']['status_totals']['Current'], 3)

    def test_source_revalidation_rejection_never_invokes_callback(self):
        self.ready()
        self.controller.action_error = 'sources replaced'
        self.window.navigate('history')
        self.callbacks['history'].assert_not_called()
        self.assertIn(('revalidate',), self.controller.calls)
        self.assertIn('sources replaced', self.window.notice.get())
        self.assertIn('STALE', self.window.content_notice.get())
        self.assert_disabled()

    def test_changed_display_generation_requires_refresh_before_action(self):
        self.ready()
        self.controller.refresh()
        self.window.navigate('history')
        self.callbacks['history'].assert_not_called()
        self.assertIn('Dashboard changed', self.window.notice.get())
        self.assert_disabled()

    def test_generation_change_during_idle_paint_cannot_navigate_unreviewed_output(self):
        self.ready()
        self.window.root.idle_hook = self.controller.refresh
        self.window.navigate('history')
        self.callbacks['history'].assert_not_called()
        self.assertIn('Dashboard changed', self.window.notice.get())
        self.assert_disabled()

    def test_callback_exception_is_visible_and_callback_invalidation_survives(self):
        self.ready()
        def callback(payload):
            self.controller.invalidate()
            raise OSError('navigation failed')
        self.callbacks['history'].side_effect = callback
        self.window.navigate('history')
        self.assertIn('navigation failed', self.window.notice.get())
        self.assertIn('STALE', self.window.content_notice.get())
        self.assertFalse(self.window._busy)
        self.assert_disabled()

    def test_external_invalidation_clears_displayed_mileage_on_render(self):
        self.ready()
        self.choose(PID)
        self.window.variables['explicit_current_mileage'].set('123')
        self.window.refresh()
        self.controller.invalidate()
        self.window.render()
        self.assertEqual(self.window.variables['explicit_current_mileage'].get(), '')
        self.assertIn('STALE', self.window.content_notice.get())
        self.assert_disabled()

    def test_zero_unpriced_legacy_and_unavailable_costs_are_distinct(self):
        self.ready()
        text = self.window.panels['costs'].content
        self.assertIn('total cost cents: 0', text)
        self.assertIn('unpriced service count: 2', text)
        self.assertIn('legacy service count: 1', text)
        self.controller.data['presentation']['service_cost_summary']['total_cost_cents'] = None
        self.window.render()
        self.assertIn('total cost cents: Unavailable / not supplied', self.window.panels['costs'].content)
        self.controller.data['presentation']['service_cost_summary']['availability'] = 'Unavailable'
        self.window.render()
        self.assertIn('availability: Unavailable', self.window.panels['costs'].content)
        self.assert_disabled()

    def test_available_unavailable_forecasts_preserve_recorded_estimated_labels(self):
        self.ready()
        text = self.window.panels['forecasts'].content
        for phrase in ('Recorded next due mileage: 1000', 'estimated due date: 2027-03-04',
                       'availability: Available', 'availability: Unavailable', 'no_dated_history',
                       'Last recorded observation'):
            self.assertIn(phrase, text)
        self.assertNotIn('987654', text)

    def test_orphan_history_keeps_stable_id_and_missing_profile_label(self):
        self.ready()
        text = self.window.panels['history'].content
        self.assertIn(ORPHAN, text)
        self.assertIn('Missing profile; historical identity retained', text)
        self.assertIn('Historical oil', text)
        self.assertIn('service-1', text)
        self.assertIn('Old vehicle [historical-id] (missing profile)', self.window.selector.options['values'])
        self.choose(ORPHAN)
        self.window.refresh()
        self.assertEqual(self.controller.state['selected_profile_id'], ORPHAN)
        self.assertIn('unavailable', self.window.notice.get())
        self.assert_disabled()

    def test_long_warning_and_history_text_is_not_truncated_or_used_as_widget_ids(self):
        self.ready()
        long_text = 'details / [stable-a] <unsafe-looking>\n' * 1000
        model = self.controller.data['presentation']
        model['data_quality']['warnings'][0]['message'] = long_text
        model['recent_services'][0]['service']['notes'] = long_text
        self.window.render()
        self.assertIn(long_text, self.window.panels['quality'].content)
        self.assertIn(long_text, self.window.panels['history'].content)
        self.assertNotIn(long_text, self.window.selector.options['values'])

    def test_empty_collections_render_without_invented_totals(self):
        self.ready()
        model = self.controller.data['presentation']
        model['vehicles'] = []
        model['recent_services'] = []
        model['maintenance'] = dict(status_totals={}, tasks=[])
        model['data_quality']['warnings'] = []
        self.window.render()
        self.assertIn('No entries.', self.window.panels['maintenance'].content)
        self.assertIn('No entries.', self.window.panels['history'].content)
        self.assertNotIn('Upcoming: 0', self.window.panels['maintenance'].content)

    def test_widget_render_and_edit_events_do_not_mutate_supplied_mappings(self):
        self.ready()
        before = deepcopy(self.controller.data)
        self.window.render()
        self.window.variables['as_of'].set('invalid draft')
        self.window.render()
        self.window.navigate('history')
        self.assertEqual(self.controller.data, before)
        self.assertEqual(self.controller.template, presentation())

    def test_invalid_evaluation_presentation_disables_actions(self):
        self.ready()
        self.controller.data['presentation']['evaluation']['input_error'] = 'below history'
        self.window.render()
        self.assert_disabled()
        self.assertIn('below history', self.window.notice.get())
        self.window.navigate('history')
        self.callbacks['history'].assert_not_called()

    def test_enter_key_refresh_and_focus_are_keyboard_reachable(self):
        entry = self.window.entries['as_of']
        self.assertTrue(entry.options['takefocus'])
        entry.bindings['<Return>'](SimpleNamespace(widget=entry))
        self.assertIn(('refresh',), self.controller.calls)
        self.assertTrue(self.window.selector.options['takefocus'])
        self.assertTrue(self.window.refresh_button.options['takefocus'])


    def test_rejected_scope_setter_cannot_refresh_a_different_vehicle(self):
        self.ready()
        self.window.selector.current(self.window._choice_ids.index(PID))
        self.controller.select_scope = Mock(side_effect=ValueError('scope rejected'))
        self.controller.calls.clear()
        self.window.refresh()
        self.assertIsNone(self.controller.state['selected_profile_id'])
        self.assertNotIn(('refresh',), self.controller.calls)
        self.assertIn('Controller rejected inputs', self.window.notice.get())
        self.assert_disabled()

    def test_corrected_form_can_recover_an_existing_invalid_controller(self):
        class InvalidController(FakeController):
            def select_scope(self, pid):
                super().select_scope(pid)
                if type(self.data['as_of']) is not date:
                    raise ValueError('Existing evaluation date is invalid')
        controller = InvalidController()
        controller.data.update(as_of='bad', status='error', error='Existing evaluation date is invalid')
        window = self.new_window(controller)
        window.variables['as_of'].set(DAY.isoformat())
        window.refresh()
        self.assertEqual(controller.state['status'], 'ready')
        self.assertIn('Current presentation', window.content_notice.get())

    def test_external_restore_clears_unsubmitted_mileage_draft(self):
        self.ready()
        self.choose(PID)
        self.window.refresh()
        self.window.variables['explicit_current_mileage'].set('123')
        self.controller.invalidate()
        self.window.render()
        self.assertEqual(self.window.variables['explicit_current_mileage'].get(), '')
        self.assertIn('STALE', self.window.content_notice.get())
        self.assert_disabled()

    def test_refresh_cancellation_restores_view_guard_and_renders_controller_state(self):
        def cancel():
            self.controller._fail('cancelled capture')
            raise KeyboardInterrupt('cancel refresh')
        self.controller.refresh_hook = cancel
        with self.assertRaises(KeyboardInterrupt):
            self.window.refresh()
        self.assertFalse(self.window._busy)
        self.assertIn('cancelled capture', self.window.notice.get())
        self.assert_disabled()
        self.controller.refresh_hook = None
        self.window.refresh()
        self.assertIn('Ready', self.window.notice.get())

    def test_form_edit_during_idle_paint_rejects_action_even_if_sources_are_current(self):
        self.ready()
        self.window.root.idle_hook = lambda: self.window.variables['as_of'].set('invalid pending edit')
        self.window.navigate('history')
        self.callbacks['history'].assert_not_called()
        self.assertIn('Dashboard changed', self.window.notice.get())
        self.assert_disabled()

    def test_mismatched_scope_date_or_cost_bounds_are_nonactionable(self):
        for changes in (dict(scope_profile_id=PID), dict(as_of='2020-01-01'),
                        dict(scope_profile_id='unknown')):
            with self.subTest(changes=changes):
                self.controller = FakeController(ready=True)
                self.window.controller = self.controller
                self.controller.data['presentation'].update(changes)
                self.window.render()
                self.assert_disabled()
                self.assertIn('STALE', self.window.content_notice.get())
        self.controller = FakeController(ready=True)
        self.window.controller = self.controller
        self.controller.data['presentation']['service_cost_summary']['cost_start_date'] = '2020-01-01'
        self.window.render()
        self.assert_disabled()

    def test_duplicate_stable_ids_or_invalid_backup_contract_are_malformed(self):
        self.ready()
        model = self.controller.data['presentation']
        model['vehicles'].append(deepcopy(model['vehicles'][0]))
        self.window.render()
        self.assertIn('unique stable IDs', self.window.notice.get())
        self.assert_disabled()
        model['vehicles'].pop()
        model['backup_health']['availability'] = 'Available'
        self.window.render()
        self.assertIn('Backup health must remain unavailable', self.window.notice.get())
        self.assert_disabled()

if __name__ == '__main__':
    unittest.main()
