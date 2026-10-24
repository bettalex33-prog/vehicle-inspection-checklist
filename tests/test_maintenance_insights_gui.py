"""All windows, widgets, variables, dialogs, clocks and navigation are mocked."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import maintenance_insights_gui as ig
from test_maintenance_insights import AS_OF, PID, OTHER, profile
from test_maintenance_insights_sources import stores


class TestInsightsGUI(unittest.TestCase):
    def test_shared_gui_sources_do_not_mutate_native_theme(self):
        # Include import-time code: mocking constructors alone misses global changes.
        import ast
        import gui
        import maintenance_gui
        import fleet_dashboard_gui
        for module in (gui, maintenance_gui, fleet_dashboard_gui, ig):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
                    self.assertNotIn(name, ('Style', 'theme_use', 'theme_settings',
                        'theme_create', 'option_add', 'option_readfile', 'layout',
                        'pack_propagate', 'grid_propagate'), module.__name__)
                    self.assertFalse({'foreground', 'background', 'fg', 'bg', 'font',
                        'rowheight', 'style'}.intersection(k.arg for k in node.keywords),
                        (module.__name__, node.lineno))

    def test_opening_insights_preserves_existing_windows(self):
        import gui
        import maintenance_gui
        from checklist import create_default_data
        with patch.object(ig.tk, 'Tk', side_effect=AssertionError('Real Tk forbidden')), \
             patch.object(gui, 'load_data', return_value=create_default_data()), \
             patch.object(ig.tk, 'BooleanVar', return_value=MagicMock()), \
             patch.object(ig.tk, 'Checkbutton', return_value=MagicMock()), \
             patch.object(ig.ttk, 'Style', side_effect=AssertionError('Global style forbidden')):
            app = gui.ChecklistApp(MagicMock(), self.runtime[-1], self.paths[2],
                                   self.paths[1], self.paths[0])
            maintenance = maintenance_gui.MaintenanceWindow(app.root, *self.paths[:2])
            existing = list(self.created)
            before = [list(widget.mock_calls) for _, widget, _, _ in existing]
            app.open_insights()
            self.assertEqual(before, [list(widget.mock_calls) for _, widget, _, _ in existing])
            self.assertEqual(len(app.insights_windows), 1)
            self.assertTrue(maintenance.available)

    def test_controls_tables_tabs_and_text_keep_native_configuration(self):
        forbidden = {'foreground', 'background', 'fg', 'bg', 'font', 'style'}
        for kind, widget, _, options in self.created:
            self.assertFalse(forbidden.intersection(options), kind)
            for call in widget.configure.call_args_list:
                self.assertFalse(forbidden.intersection(call.kwargs), kind)
            if kind == 'Combobox':
                self.assertEqual(options['state'], 'readonly')
            if kind == 'Treeview':
                self.assertEqual(options['show'], 'headings')
                self.assertTrue(widget.heading.called)
        self.assertEqual(len(self.notebook.add.call_args_list), 4)
        self.assertIn('Evaluated on', self.window.notice.get())
        for widget in (self.window.quality, self.window.forecast_details):
            widget.insert.assert_called()
            widget.configure.assert_called_with(state=ig.tk.DISABLED)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = stores(self.root)
        self.runtime = (*self.paths, self.root / 'custom-checklist.json')
        self.runtime[-1].write_bytes(b'protected')
        self.before = {p: p.read_bytes() for p in self.runtime}
        self.widgets = {}
        self.created = []
        for namespace, names in ((ig.tk, ('Toplevel', 'Frame', 'Label', 'Entry', 'Button', 'Text')),
                                 (ig.ttk, ('Combobox', 'Treeview', 'Scrollbar', 'Notebook'))):
            for name in names:
                def factory(*args, _kind=name, **kwargs):
                    widget = MagicMock()
                    widget.winfo_screenwidth.return_value = 1280
                    widget.winfo_screenheight.return_value = 800
                    self.created.append((_kind, widget, args, kwargs))
                    return widget
                if name == 'Combobox':
                    def factory(*args, **kwargs):
                        widget = MagicMock(); index = [-1]
                        self.created.append(("Combobox", widget, args, kwargs))
                        def current(value=None):
                            if value is not None: index[0] = value
                            return index[0]
                        widget.current.side_effect = current
                        return widget
                elif name == 'Notebook':
                    def factory(*args, **kwargs):
                        self.notebook = MagicMock()
                        self.created.append(("Notebook", self.notebook, args, kwargs))
                        return self.notebook
                patcher = patch.object(namespace, name, side_effect=factory)
                self.widgets[name] = patcher.start(); self.addCleanup(patcher.stop)
        def variable(*args, **kwargs):
            value, callbacks = [kwargs.get('value', '')], []
            mock = MagicMock()
            mock.get.side_effect = lambda: value[0]
            def change(new):
                value[0] = new
                for callback in callbacks: callback('', '', 'write')
            mock.set.side_effect = change
            mock.trace_add.side_effect = lambda mode, callback: callbacks.append(callback)
            return mock
        patcher = patch.object(ig.tk, 'StringVar', side_effect=variable)
        patcher.start(); self.addCleanup(patcher.stop)
        for namespace, names in ((ig.messagebox, ('showerror', 'showinfo', 'askyesno')),
                                 (ig.filedialog, ('asksaveasfilename',))):
            for name in names:
                patcher = patch.object(namespace, name)
                setattr(self, name, patcher.start()); self.addCleanup(patcher.stop)
        patcher = patch.object(ig, 'date'); clock = patcher.start(); self.addCleanup(patcher.stop)
        clock.today.return_value = AS_OF
        self.open_task = MagicMock()
        self.window = self.new_window()

    def new_window(self):
        return ig.MaintenanceInsightsWindow(MagicMock(), *self.paths,
                                           runtime_paths=self.runtime, open_task=self.open_task)

    def select_live(self):
        self.window.profile_selector.current(self.window.profile_ids.index(PID)+1)
        self.window.select_profile()
        self.window.refresh()

    def test_construction_defaults_and_pages(self):
        self.assertTrue(self.window.ready)
        self.assertIsNone(self.window.selected_profile_id())
        self.assertEqual(self.window.evaluation_date.get(), '2026-10-21')
        self.assertEqual([v.get() for v in (self.window.mileage, self.window.start, self.window.end)], ['', '', ''])
        self.assertEqual(self.widgets['Notebook'].call_count, 1)
        self.assertEqual([c.kwargs['text'] for c in self.notebook.add.call_args_list],
                         ['Forecasts', 'Costs and Months', 'Observations', 'Data Quality'])
        self.assertEqual({p: p.read_bytes() for p in self.runtime}, self.before)
        buttons = {c.kwargs['text']: c.kwargs['command'] for c in self.widgets['Button'].call_args_list}
        self.assertEqual(buttons['Evaluate / Refresh'], self.window.refresh)

    def record(self, widget):
        return next(record for record in self.created if record[1] is widget)

    def test_geometry_hierarchy_and_scrollbar_ownership(self):
        window = self.window
        window.root.geometry.assert_called_once_with('1100x700')
        window.root.rowconfigure.assert_called_once_with(2, weight=1)
        window.root.columnconfigure.assert_called_once_with(0, weight=1)
        window.controls.grid.assert_called_once()
        window.notebook.grid.assert_called_once_with(row=2, column=0, sticky='nsew', padx=12)
        self.assertIs(self.record(window.notebook)[2][0], window.root)
        self.assertEqual(len(window.notebook.add.call_args_list), 4)
        for call, title in zip(window.notebook.add.call_args_list,
                               ('Forecasts', 'Costs and Months', 'Observations', 'Data Quality')):
            self.assertIs(call.args[0], window.pages[title])
            self.assertEqual(call.kwargs, {'text': title})
            self.assertIs(self.record(call.args[0])[2][0], window.notebook)
            call.args[0].columnconfigure.assert_called_with(0, weight=1)
        entries = [r for r in self.created if r[0] == 'Entry']
        self.assertEqual(len(entries), 4)
        for record, variable in zip(entries, (window.evaluation_date, window.mileage, window.start, window.end)):
            self.assertIs(record[2][0], window.controls)
            self.assertIs(record[3]['textvariable'], variable)
            record[1].grid.assert_called_once()
        window.profile_selector.grid.assert_called_once()
        for tree, title in ((window.forecasts, 'Forecasts'), (window.costs, 'Costs and Months'),
                            (window.recent, 'Costs and Months'), (window.observations, 'Observations'),
                            (window.rates, 'Observations')):
            region = self.record(tree)[2][0]
            self.assertIs(self.record(region)[2][0], window.pages[title])
            region.grid.assert_called_once()
            region.rowconfigure.assert_called_once_with(0, weight=1)
            region.columnconfigure.assert_called_once_with(0, weight=1)
            tree.grid.assert_called_once_with(row=0, column=0, sticky='nsew')
            bars = [r for r in self.created if r[0] == 'Scrollbar' and r[2][0] is region]
            self.assertEqual({r[3]['orient'] for r in bars}, {'horizontal', 'vertical'})
            for _, bar, _, options in bars:
                axis = 'x' if options['orient'] == 'horizontal' else 'y'
                self.assertEqual(options['command'], getattr(tree, axis + 'view'))
                self.assertTrue(any(c.kwargs.get(axis + 'scrollcommand') == bar.set
                                    for c in tree.configure.call_args_list))
                bar.grid.assert_called_once()
        self.assertIs(self.record(window.actions)[2][0], window.root)
        window.actions.grid.assert_called_once_with(row=3, column=0, sticky='ew', padx=12, pady=6)
        for kind, widget, args, options in self.created:
            if kind == 'Button':
                self.assertIs(args[0], window.controls if options['text'] == 'Evaluate / Refresh' else window.actions)
                self.assertTrue(widget.grid.called or widget.pack.called)
        # Every parent uses one geometry manager for its children.
        managers = {}
        for kind, widget, args, _ in self.created:
            if not args:
                continue
            used = {name for name in ('grid', 'pack') if getattr(widget, name).called}
            if used:
                managers.setdefault(id(args[0]), set()).update(used)
        self.assertTrue(all(len(used) == 1 for used in managers.values()))

    def test_evaluate_updates_visible_results(self):
        self.window.forecasts.insert.reset_mock()
        self.window.quality.insert.reset_mock()
        self.window.refresh()
        self.assertIn('Evaluated on', self.window.notice.get())
        self.assertNotIn('Unavailable', self.window.totals.get())
        self.window.forecasts.insert.assert_called()
        self.window.costs.insert.assert_called()
        self.window.quality.insert.assert_called()
        self.window.forecast_details.insert.assert_called()

    def test_initial_capture_failure_is_visible_and_recoverable(self):
        with patch.object(ig, 'capture_insights', side_effect=OSError('unreadable source')):
            window = self.new_window()
        self.assertFalse(window.ready)
        self.assertIn('unavailable', window.notice.get())
        self.showerror.assert_called()
        window.notebook.grid.assert_called_once()
        window.refresh()
        self.assertTrue(window.ready)

    def test_unexpected_evaluation_and_render_errors_are_visible(self):
        for target in ('capture_insights',):
            with patch.object(ig, target, side_effect=RuntimeError('evaluation failed')):
                self.window.refresh()
            self.assertFalse(self.window.ready)
            self.assertIn('evaluation failed', self.window.notice.get())
            self.showerror.assert_called()
        with patch.object(self.window, 'render', side_effect=RuntimeError('render failed')):
            self.window.refresh()
        self.assertFalse(self.window.ready)
        self.assertIn('render failed', self.window.notice.get())
        self.showerror.assert_called()

    def test_widget_initialization_error_is_reported(self):
        with patch.object(ig.ttk, 'Notebook', side_effect=RuntimeError('notebook failed')):
            with self.assertRaisesRegex(RuntimeError, 'notebook failed'):
                self.new_window()
        self.assertIn('Unable to initialize', self.showerror.call_args.args[1])
        self.assertIn('notebook failed', self.showerror.call_args.args[1])

    def test_switch_clears_explicit_mileage_and_requires_evaluation(self):
        self.select_live(); self.window.mileage.set('10600'); self.window.refresh()
        self.assertEqual(self.window.model['explicit_current_mileage'], 10600)
        self.window.profile_selector.current(0); self.window.select_profile()
        self.assertEqual(self.window.mileage.get(), '')
        self.assertFalse(self.window.ready)
        self.window.buttons['Export CSV'].configure.assert_called_with(state=ig.tk.DISABLED)

    def test_explicit_evaluation_does_not_persist_or_transfer(self):
        self.select_live(); self.window.mileage.set('10600')
        self.assertFalse(self.window.ready)
        self.window.refresh()
        self.assertTrue(self.window.ready)
        self.assertEqual(self.window.model['tasks'][0]['remaining_miles'], 401)
        self.assertEqual({p: p.read_bytes() for p in self.runtime}, self.before)

    def test_dirty_filters_block_actions_and_invalid_mileage_keeps_costs(self):
        self.select_live(); self.window.mileage.set('bad'); self.window.refresh()
        self.assertTrue(self.window.ready)
        self.assertFalse(self.window.model['export_available'])
        self.assertEqual(self.window.model['costs']['service_count'], 2)
        self.assertEqual(self.window.model['tasks'][0]['axes'][0]['availability'], 'Available')
        self.window.start.set('2026-10-01')
        self.assertFalse(self.window.ready)
        self.window.export_csv(); self.asksaveasfilename.assert_not_called()

    def test_missing_profiles_and_duplicate_names_use_stable_ids(self):
        self.paths[1].write_text(json.dumps({'schema_version': 1, 'profiles': [profile(OTHER, 'Unit')]}))
        self.window.refresh()
        self.assertEqual(set(self.window.profile_ids), {PID, OTHER})
        choices = self.window.profile_selector.configure.call_args.kwargs['values']
        self.assertTrue(any(PID in v and 'missing profile' in v for v in choices))
        self.window.profile_selector.current(self.window.profile_ids.index(PID)+1)
        self.window.select_profile(); self.window.mileage.set('10600'); self.window.refresh()
        self.assertFalse(self.window.model['export_available'])

    def test_focus_staleness_clears_successful_presentation(self):
        self.select_live(); self.window.mileage.set('10600'); self.window.refresh()
        self.paths[2].write_bytes(self.paths[2].read_bytes()+b'\n')
        self.window.focus_return()
        self.assertIsNone(self.window.model)
        self.assertIsNone(self.window.snapshot)
        self.assertEqual(self.window.mileage.get(), '')
        self.assertFalse(self.window.ready)
        self.assertIn('refresh required', self.window.notice.get())

    def test_refresh_reset_and_date_change_clear_session_inputs(self):
        self.select_live(); self.window.mileage.set('10600')
        self.window.evaluation_date.set('2026-10-22')
        self.assertEqual(self.window.mileage.get(), '')
        self.window.start.set('2026-10-01'); self.window.end.set('2026-10-11')
        self.window.refresh(reset=True)
        self.assertIsNone(self.window.selected_profile_id())
        self.assertEqual(self.window.evaluation_date.get(), '2026-10-21')
        self.assertEqual(self.window.start.get(), '')
        self.assertEqual(self.window.end.get(), '')

    def test_restart_does_not_restore_filters_or_mileage(self):
        self.select_live(); self.window.mileage.set('10600'); self.window.start.set('2026-10-11'); self.window.refresh()
        restarted = self.new_window()
        self.assertEqual(restarted.mileage.get(), '')
        self.assertEqual(restarted.start.get(), '')
        self.assertIsNone(restarted.model['explicit_current_mileage'])
        self.assertEqual({p: p.read_bytes() for p in self.runtime}, self.before)

    def test_navigation_passes_only_task_id_and_ignores_cost_filters(self):
        self.select_live(); self.window.mileage.set('10600'); self.window.start.set('2026-01-01'); self.window.end.set('2026-01-01'); self.window.refresh()
        tid = self.window.model['tasks'][0]['task']['task_id']
        self.window.forecasts.selection.return_value = [tid]
        self.window.select_task(); self.window.navigate()
        self.open_task.assert_called_once_with(tid)
        self.paths[0].write_bytes(self.paths[0].read_bytes()+b'\n')
        self.window.navigate()
        self.open_task.assert_called_once()
        self.assertFalse(self.window.ready)

    def test_export_cancel_decline_and_confirm(self):
        self.asksaveasfilename.return_value = ''
        self.window.export_csv(); self.showinfo.assert_not_called()
        output = self.root / 'report.csv'; output.write_bytes(b'old')
        self.asksaveasfilename.return_value = str(output)
        self.askyesno.return_value = False
        self.window.export_csv(); self.assertEqual(output.read_bytes(), b'old')
        self.askyesno.return_value = True
        self.window.export_csv()
        self.assertTrue(output.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.asksaveasfilename.return_value = str(self.root/'report.html')
        self.window.export_html(); self.assertTrue((self.root/'report.html').exists())

    def test_export_error_and_source_change_in_save_dialog(self):
        self.asksaveasfilename.return_value = str(self.paths[0])
        self.askyesno.return_value = True
        self.window.export_csv(); self.showerror.assert_called()
        self.assertEqual(self.paths[0].read_bytes(), self.before[self.paths[0]])
        def change(**kwargs):
            self.paths[0].write_bytes(self.paths[0].read_bytes()+b'\n')
            return str(self.root/'report.csv')
        self.asksaveasfilename.side_effect = change
        self.window.export_csv()
        self.assertFalse(self.window.ready)
        self.assertFalse((self.root/'report.csv').exists())

    def test_corrupt_sources_invalid_dates_and_recovery(self):
        self.window.evaluation_date.set('2026-1-01'); self.window.refresh()
        self.assertIsNone(self.window.model)
        self.window.evaluation_date.set('2026-10-21'); self.window.refresh()
        self.assertTrue(self.window.ready)
        self.paths[2].write_bytes(b'broken'); self.window.refresh()
        self.assertIsNone(self.window.model)
        self.assertEqual(self.window.totals.get(), 'Unavailable — refresh required')
        self.paths[2].write_bytes(self.before[self.paths[2]]); self.window.refresh()
        self.assertTrue(self.window.ready)
