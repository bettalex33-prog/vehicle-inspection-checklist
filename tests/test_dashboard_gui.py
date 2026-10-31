"""Fake Tk only; controller integration uses injected in-memory captures."""
import builtins
from copy import deepcopy
from datetime import date
import importlib
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dashboard_gui as dg
from dashboard_controller import DashboardController

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

    def real_controller_window(self):
        """Exercise the public controller contract without sources or storage."""
        template = presentation()
        captures, supplied, events = [], [], []

        def capture(**inputs):
            snapshot = SimpleNamespace(stale=False)
            snapshot.model = dict(
                stores=dict(
                    checklist=dict(availability='Available', data={'oil': {'complete': False}}),
                    profiles=dict(availability='Available', data={'profiles': [
                        {'profile_id': PID}, {'profile_id': OTHER}]}),
                    history=dict(availability='Available', data={'inspections': []}),
                    maintenance=dict(availability='Available', data={'tasks': []})),
                insights=dict(availability='Available', data=dict(
                    inputs, input_error=None, export_available=True)))

            def revalidate():
                events.append('revalidate')
                if snapshot.stale:
                    raise ValueError('Synthetic sources changed')

            snapshot.revalidate = Mock(side_effect=revalidate)
            captures.append((snapshot, deepcopy(snapshot.model)))
            events.append('capture')
            return snapshot

        def project(facts, checklist, *, scope_profile_id):
            self.assertEqual(checklist, {'oil': {'complete': False}})
            output = deepcopy(template)
            output.update(as_of=facts['as_of'].isoformat(), scope_profile_id=scope_profile_id)
            output['evaluation'].update(selected_profile_id=facts['selected_profile_id'],
                explicit_current_mileage=facts['explicit_current_mileage'])
            for key in ('cost_start_date', 'cost_end_date'):
                output['service_cost_summary'][key] = facts[key]
            supplied.append((output, deepcopy(output)))
            events.append('project')
            return output

        factory, projector = Mock(side_effect=capture), Mock(side_effect=project)
        self.controller = DashboardController(factory, as_of=DAY, projector=projector)
        # Fail closed if construction or any later view event attempts IO,
        # JSON, path discovery, or real source discovery. Tk/root/event-loop
        # constructors remain forbidden by setUp's fake-widget patches.
        for namespace, name in ((builtins, 'open'), (io, 'open'), (os, 'open')):
            patcher = patch.object(namespace, name, side_effect=AssertionError('Filesystem forbidden'))
            patcher.start()
            self.addCleanup(patcher.stop)
        original_import = builtins.__import__

        def isolated_import(name, *args, **kwargs):
            if name.split('.')[0] in ('application_paths', 'data_backup', 'dashboard_sources', 'json'):
                raise AssertionError('Live dependency forbidden: ' + name)
            return original_import(name, *args, **kwargs)

        patcher = patch.object(builtins, '__import__', side_effect=isolated_import)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = self.new_window(self.controller)
        return SimpleNamespace(factory=factory, projector=projector, captures=captures,
            supplied=supplied, events=events, template=template)

    def assert_supplied_data_unchanged(self, fixture):
        self.assertEqual(fixture.template, presentation())
        for output, before in fixture.supplied:
            self.assertEqual(output, before)
        for snapshot, before in fixture.captures:
            self.assertEqual(snapshot.model, before)

    def test_layout_top_level_regions_are_distinct_and_only_content_expands(self):
        # Structural regression only; native Tk 9 smoke is the painting evidence.
        root = self.window.root
        children = [w for w in self.created if w.parent is root]
        notices = {w.options.get('textvariable'): w for w in children if w.kind == 'Label'}
        action_bar = self.window.buttons['history'].parent
        regions = (self.window.controls, notices[self.window.notice],
                   notices[self.window.content_notice], self.window.notebook, action_bar)
        self.assertEqual(set(children), set(regions))
        occupied = set()
        for row, widget in enumerate(regions):
            self.assertIs(widget.parent, root)
            grid = widget.grid_options
            self.assertEqual((grid['row'], grid['column']), (row, 0))
            cells = {(r, c) for r in range(row, row + grid.get('rowspan', 1))
                     for c in range(grid['column'], grid['column'] + grid.get('columnspan', 1))}
            self.assertFalse(occupied & cells)
            occupied.update(cells)
        self.assertEqual(root.rows, {3: {'weight': 1}})
        self.assertEqual(root.columns, {0: {'weight': 1}})
        self.assertEqual(self.window.controls.columns, {1: {'weight': 1}})
        self.assertEqual(self.window.controls.rows, {})
        self.assertEqual(action_bar.rows, {})
        self.assertEqual(self.window.notebook.grid_options['sticky'], 'nsew')

    def test_layout_each_panel_owns_its_text_and_both_scrollbars(self):
        pages = [page for page, options in self.window.notebook.tabs]
        self.assertEqual(len(set(pages)), len(dg.PANELS))
        for (key, title, field), page in zip(dg.PANELS, pages):
            with self.subTest(panel=key):
                self.assertIs(page.parent, self.window.notebook)
                self.assertEqual(page.rows, {0: {'weight': 1}})
                self.assertEqual(page.columns, {0: {'weight': 1}})
                text = self.window.panels[key]
                self.assertIs(text.parent, page)
                children = [w for w in self.created if w.parent is page]
                self.assertEqual(len(children), 3)
                scrollbars = {w.options['orient']: w for w in children if w.kind == 'Scrollbar'}
                self.assertEqual(set(scrollbars), {'vertical', 'horizontal'})
                for widget, cell in ((text, (0, 0)), (scrollbars['vertical'], (0, 1)),
                                     (scrollbars['horizontal'], (1, 0))):
                    self.assertEqual((widget.grid_options['row'], widget.grid_options['column']), cell)
                    self.assertEqual(widget.grid_options.get('rowspan', 1), 1)
                    self.assertEqual(widget.grid_options.get('columnspan', 1), 1)
                self.assertEqual(scrollbars['vertical'].options['command'], text.yview)
                self.assertEqual(scrollbars['horizontal'].options['command'], text.xview)
                self.assertEqual(text.options['yscrollcommand'], scrollbars['vertical'].set)
                self.assertEqual(text.options['xscrollcommand'], scrollbars['horizontal'].set)
        self.assertEqual({w.parent for w in self.created if w.kind in ('Text', 'Scrollbar')}, set(pages))

    def test_layout_actions_and_close_are_separate_from_notebook_content(self):
        action_bar = self.window.buttons['history'].parent
        self.assertIs(action_bar.parent, self.window.root)
        self.assertEqual(action_bar.grid_options['row'], 4)
        self.assertEqual(action_bar.grid_options.get('rowspan', 1), 1)
        self.assertEqual(self.window.notebook.grid_options['row'], 3)
        buttons = [w for w in self.created if w.parent is action_bar]
        self.assertEqual(len(buttons), len(dg.ACTIONS) + 1)
        cells = []
        for index, (key, label) in enumerate(dg.ACTIONS):
            button = self.window.buttons[key]
            self.assertIs(button.parent, action_bar)
            self.assertEqual((button.grid_options['row'], button.grid_options['column']),
                             (index // 2, index % 2))
            cells.append((button.grid_options['row'], button.grid_options['column']))
        close, = [w for w in buttons if w.options['text'] == 'Close']
        self.assertEqual((close.grid_options['row'], close.grid_options['column']), (2, 1))
        self.assertNotIn((2, 1), cells)
        self.assertEqual(close.options['command'], self.window.close)
        for button in buttons:
            self.assertEqual(button.grid_options.get('rowspan', 1), 1)
            self.assertEqual(button.grid_options.get('columnspan', 1), 1)

    def test_real_controller_refresh_scope_and_explicit_mileage_forwarding(self):
        fixture = self.real_controller_window()
        fixture.factory.assert_not_called()
        fixture.projector.assert_not_called()
        self.assertEqual(self.controller.state['status'], 'empty')
        self.window.refresh_button.invoke()
        self.assertEqual(self.controller.state['status'], 'ready')
        self.assertIn('Current presentation', self.window.content_notice.get())
        fixture.factory.assert_called_once_with(as_of=DAY, cost_start_date=None, cost_end_date=None,
            selected_profile_id=None, explicit_current_mileage=None)
        self.assertEqual(fixture.events, ['capture', 'project', 'revalidate'])
        with patch.object(self.controller, 'select_scope', wraps=self.controller.select_scope) as scope, \
             patch.object(self.controller, 'set_explicit_mileage', wraps=self.controller.set_explicit_mileage) as mileage:
            self.choose(PID)
            scope.assert_called_once_with(PID)
            self.assertEqual(self.controller.state['selected_profile_id'], PID)
            before = self.controller.state
            self.window.variables['explicit_current_mileage'].set('000123')
            self.assertEqual(self.controller.state, before)
            self.window.refresh_button.invoke()
            mileage.assert_called_once_with(123)
        fixture.factory.assert_called_with(as_of=DAY, cost_start_date=None, cost_end_date=None,
            selected_profile_id=PID, explicit_current_mileage=123)
        self.assertEqual(self.controller.state['presentation']['scope_profile_id'], PID)
        self.assertEqual(self.controller.state['explicit_current_mileage'], 123)
        self.assertEqual(self.window.notice.get(), 'Ready.')
        self.assert_supplied_data_unchanged(fixture)

    def test_real_controller_navigation_revalidates_before_owned_callback(self):
        fixture = self.real_controller_window()
        self.window.refresh_button.invoke()
        snapshot = fixture.captures[-1][0]
        before = self.controller.state

        def consume(payload):
            self.assertEqual(fixture.events, ['capture', 'project', 'revalidate', 'revalidate'])
            self.assertTrue(self.controller.state['action_in_progress'])
            self.assertEqual(payload, before['presentation'])
            payload['maintenance']['status_totals'].clear()

        self.callbacks['history'].side_effect = consume
        with patch.object(self.controller, 'perform_action', wraps=self.controller.perform_action) as action:
            self.window.buttons['history'].invoke()
            action.assert_called_once()
            # Assertion failures inside the callback are caught by the view;
            # require successful UI recovery before editing can clear its error.
            self.assertEqual(self.window.notice.get(), 'Ready.')
            self.assertEqual(self.controller.state, before)
            self.window.variables['as_of'].set('invalid pending draft')
            draft_state = self.controller.state
            self.window.render()
            self.window.navigate('history')
            self.assertEqual(action.call_count, 1)
            self.assertEqual(self.controller.state, draft_state)
        self.callbacks['history'].assert_called_once()
        self.assertEqual(snapshot.revalidate.call_count, 2)
        self.assertEqual(fixture.factory.call_count, 1)
        self.assertEqual(self.controller.state, before)
        self.assert_supplied_data_unchanged(fixture)

    def test_real_controller_stale_capture_rejects_action_and_retains_output(self):
        fixture = self.real_controller_window()
        self.window.refresh_button.invoke()
        before = self.controller.state['presentation']
        snapshot = fixture.captures[-1][0]
        snapshot.stale = True  # External source change, unseen by the view.
        self.assertEqual(self.window.buttons['history'].options['state'], 'normal')
        with patch.object(self.controller, 'perform_action', wraps=self.controller.perform_action) as action:
            self.window.buttons['history'].invoke()
            action.assert_called_once()
        self.callbacks['history'].assert_not_called()
        self.assertEqual(snapshot.revalidate.call_count, 2)
        self.assertEqual(self.controller.state['status'], 'error')
        self.assertIsNone(self.controller.state['presentation'])
        self.assertEqual(self.controller.state['last_successful_presentation'], before)
        self.assertIn('Synthetic sources changed', self.window.notice.get())
        self.assertIn('STALE retained content', self.window.content_notice.get())
        self.assert_disabled()
        self.assert_supplied_data_unchanged(fixture)

    def test_real_controller_invalidation_keeps_stale_content_without_view_mutation(self):
        fixture = self.real_controller_window()
        self.window.refresh_button.invoke()
        self.choose(PID)
        self.window.variables['explicit_current_mileage'].set('123')
        self.window.refresh_button.invoke()
        panels = {key: text.content for key, text in self.window.panels.items()}
        published = self.controller.state['presentation']
        self.controller.invalidate()
        before = self.controller.state
        self.window.render()
        self.window.navigate('history')
        self.assertEqual(self.controller.state, before)
        self.assertIsNone(before['presentation'])
        self.assertEqual(before['last_successful_presentation'], published)
        self.assertEqual({key: text.content for key, text in self.window.panels.items()}, panels)
        self.assertEqual(self.window.variables['explicit_current_mileage'].get(), '')
        self.assertIn('STALE retained content', self.window.content_notice.get())
        self.assert_disabled()
        self.callbacks['history'].assert_not_called()
        self.assertEqual(fixture.factory.call_count, 2)
        for snapshot, model in fixture.captures:
            snapshot.revalidate.assert_called_once_with()
        self.assert_supplied_data_unchanged(fixture)

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

    def test_optional_backward_alias_errors_do_not_prevent_construction(self):
        original_bind = Widget.bind
        for rejected in (('<ISO_Left_Tab>',), ('<Shift-Tab>',),
                         ('<Shift-Tab>', '<ISO_Left_Tab>')):
            with self.subTest(rejected=rejected):
                attempts = []

                def bind(widget, sequence, callback):
                    attempts.append((widget, sequence))
                    if sequence in rejected:
                        keysym = sequence[1:-1]
                        raise dg.tk.TclError('bad event type or keysym "' + keysym + '"')
                    original_bind(widget, sequence, callback)

                with patch.object(Widget, 'bind', bind):
                    window = self.new_window()
                self.assertEqual(len(window.panels), len(dg.PANELS))
                self.assertEqual(self.controller.calls, [])
                for text in window.panels.values():
                    self.assertIn('<Tab>', text.bindings)
                    for sequence in ('<Shift-Tab>', '<ISO_Left_Tab>'):
                        self.assertIn((text, sequence), attempts)
                        if sequence in rejected:
                            self.assertNotIn(sequence, text.bindings)
                        else:
                            target = Mock()
                            with patch.object(text, 'tk_focusPrev', return_value=target) as previous:
                                self.assertEqual(text.bindings[sequence](SimpleNamespace(widget=text)), 'break')
                            previous.assert_called_once_with()
                            target.focus_set.assert_called_once_with()

    def test_both_backward_aliases_traverse_once_and_stop_default_handling(self):
        for text in self.window.panels.values():
            self.assertIs(text.bindings['<Shift-Tab>'], text.bindings['<ISO_Left_Tab>'])
            # Tk selects one matching binding per bindtag, even when both
            # aliases are registered. Either selected alias must stop dispatch.
            for sequence in ('<Shift-Tab>', '<ISO_Left_Tab>'):
                with self.subTest(panel=text, sequence=sequence):
                    target = Mock()
                    default_traversal = Mock()
                    with patch.object(text, 'tk_focusPrev', return_value=target) as previous, \
                         patch.object(text, 'tk_focusNext') as following:
                        result = text.bindings[sequence](SimpleNamespace(widget=text))
                        if result != 'break':
                            default_traversal()
                    self.assertEqual(result, 'break')
                    previous.assert_called_once_with()
                    following.assert_not_called()
                    target.focus_set.assert_called_once_with()
                    default_traversal.assert_not_called()

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
