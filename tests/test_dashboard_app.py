"""Phase 6 uses fake Tk, explicit paths, and disposable source stores only."""
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dashboard_app as app
import dashboard_gui as view
import gui
from data_backup import RuntimePaths
from test_dashboard_gui import Variable, Widget
import test_dashboard_sources as source_fixtures
import test_gui as gui_fixtures


DAY = date(2026, 11, 11)


class ApplicationWidget(Widget):
    def __init__(self, kind, parent=None, **options):
        super().__init__(kind, parent, **options)
        self.lifts = self.deiconifications = 0

    def winfo_exists(self):
        return not self.destroyed

    def deiconify(self):
        self.deiconifications += 1

    def lift(self):
        self.lifts += 1


@contextmanager
def forbid_io():
    with ExitStack() as stack:
        for owner, names in ((io, ('open',)), (os, ('open', 'listdir', 'scandir')),
                             (Path, ('resolve', 'home', 'cwd', 'stat', 'lstat', 'exists'))):
            for name in names:
                stack.enter_context(patch.object(owner, name,
                    side_effect=AssertionError('Construction must not access the filesystem')))
        stack.enter_context(patch('builtins.open',
            side_effect=AssertionError('Construction must not open files')))
        yield


class TestDashboardApplication(unittest.TestCase):
    def setUp(self):
        self.parent = ApplicationWidget('existing root')
        self.paths = RuntimePaths(*(Path('/synthetic/runtime') / name for name in
            ('checklist.json', 'history.json', 'profiles.json', 'maintenance.json')))
        self.created = []
        for owner, names in ((view.tk, ('Toplevel', 'Text')),
                             (view.ttk, ('Frame', 'Label', 'Entry', 'Combobox',
                                         'Button', 'Notebook', 'Scrollbar'))):
            for name in names:
                def create(parent=None, _name=name, **options):
                    widget = ApplicationWidget(_name, parent, **options)
                    self.created.append(widget)
                    return widget
                patcher = patch.object(owner, name, side_effect=create)
                patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(view.tk, 'StringVar', Variable)
        patcher.start(); self.addCleanup(patcher.stop)
        for name in ('Tk', 'mainloop'):
            patcher = patch.object(view.tk, name,
                side_effect=AssertionError('A second root or loop is forbidden'))
            patcher.start(); self.addCleanup(patcher.stop)
        self.ask = Mock(return_value=DAY.isoformat())
        self.error = Mock()
        self.capture, self.backup = Mock(), Mock()
        self.chooser = Mock(return_value='synthetic-archive.zip')
        self.launcher = self.launcher_for()

    def launcher_for(self, **options):
        defaults = dict(capture_factory=self.capture, backup_factory=self.backup,
                        ask_date=self.ask, choose_archive=self.chooser, show_error=self.error)
        defaults.update(options)
        return app.DashboardLauncher(self.parent, self.paths, **defaults)

    def assert_main_survives(self):
        self.assertFalse(self.parent.destroyed)
        self.assertFalse(self.launcher._opening)

    def test_construction_and_open_are_passive_with_no_filesystem_or_clock(self):
        class NoClockDate:
            fromisoformat = staticmethod(date.fromisoformat)
            today = Mock(side_effect=AssertionError('Clock forbidden'))
        with forbid_io(), patch.object(app, 'date', NoClockDate):
            launcher = self.launcher_for()
            self.assertIsNone(launcher.window)
            self.ask.assert_not_called()
            self.assertEqual(self.created, [])
            window = launcher.open()
        self.assertEqual(window.controller.state['as_of'], DAY)
        self.assertEqual(window.controller.state['status'], 'empty')
        self.assertEqual(window.controller.backup_state['status'], 'no_selection')
        self.assertIsNone(window.controller.state['presentation'])
        self.assertIs(window.root.parent, self.parent)
        self.assertEqual(len([w for w in self.created if w.kind == 'Toplevel']), 1)
        self.capture.assert_not_called(); self.backup.assert_not_called()
        self.chooser.assert_not_called(); self.error.assert_not_called()
        NoClockDate.today.assert_not_called()
        self.assertTrue(all(button.options['state'] == 'disabled' for button in window.buttons.values()))

    def test_repeated_open_focuses_and_reuses_without_prompt_or_capture(self):
        first = self.launcher.open()
        for _ in range(3):
            self.assertIs(self.launcher.open(), first)
        self.assertEqual(first.root.lifts, 3)
        self.assertEqual(first.root.deiconifications, 3)
        self.assertTrue(first.root.focused)
        self.ask.assert_called_once_with()
        self.capture.assert_not_called(); self.backup.assert_not_called()
        self.assert_main_survives()

    def test_close_and_reopen_preserve_main_window_and_create_fresh_session(self):
        first = self.launcher.open()
        first.controller.select_backup_archive('synthetic.zip')
        first.root.protocols['WM_DELETE_WINDOW']()
        self.assertTrue(first.root.destroyed)
        second = self.launcher.open()
        self.assertIsNot(first, second)
        self.assertIsNot(first.controller, second.controller)
        self.assertFalse(second.controller.backup_state['selected'])
        self.assertEqual(second.controller.state['status'], 'empty')
        self.assert_main_survives()

    def test_external_window_destruction_does_not_block_reopening(self):
        first = self.launcher.open()
        first.root.destroy()
        second = self.launcher.open()
        self.assertIsNot(first, second)
        self.assert_main_survives()

    def test_cancelled_date_and_invalid_dates_create_no_window_and_can_retry(self):
        for text in (None, '', '2026-99-99', '20261111', '/private/secret'):
            with self.subTest(text=text):
                self.ask.return_value = text
                self.assertIsNone(self.launcher.open())
                self.assertEqual(self.created, [])
                self.assertIsNone(self.launcher.window)
                self.assert_main_survives()
        self.assertNotIn('/private', str(self.error.call_args_list))
        self.ask.return_value = DAY.isoformat()
        self.assertIsNotNone(self.launcher.open())

    def test_default_date_dialog_is_explicit_and_owned_by_main_root(self):
        launcher = self.launcher_for(ask_date=None)
        with patch.object(app.simpledialog, 'askstring', return_value=DAY.isoformat()) as dialog:
            self.assertIsNotNone(launcher.open())
        self.assertIs(dialog.call_args.kwargs['parent'], self.parent)
        self.capture.assert_not_called()

    def test_reentrant_open_during_date_prompt_safely_rejects_duplicate(self):
        def ask():
            self.assertIsNone(self.launcher.open())
            return DAY.isoformat()
        self.ask.side_effect = ask
        self.assertIsNotNone(self.launcher.open())
        self.assertEqual(len([w for w in self.created if w.kind == 'Toplevel']), 1)
        self.assert_main_survives()

    def test_controller_factory_failure_is_sanitized_and_reopening_works(self):
        factory = Mock(side_effect=OSError('/private/controller payload'))
        self.launcher = self.launcher_for(controller_factory=factory)
        self.assertIsNone(self.launcher.open())
        self.assertIsNone(self.launcher.window)
        self.assertEqual(self.created, [])
        self.error.assert_called_once_with('Open Dashboard', app.OPEN_ERROR, parent=self.parent)
        self.assert_main_survives()
        factory.side_effect = None
        factory.return_value = app.DashboardController(Mock(), as_of=DAY)
        self.assertIsNotNone(self.launcher.open())

    def test_injected_window_factory_failure_leaves_no_stale_reference(self):
        factory = Mock(side_effect=RuntimeError('/private/window payload'))
        self.launcher = self.launcher_for(window_factory=factory)
        self.assertIsNone(self.launcher.open())
        self.assertIsNone(self.launcher.window)
        self.assertEqual(self.error.call_args.args[1], app.OPEN_ERROR)
        self.assert_main_survives()
        factory.side_effect = None
        factory.return_value = SimpleNamespace(root=ApplicationWidget('Toplevel'), _closed=False)
        self.assertIs(self.launcher.open(), factory.return_value)

    def test_partial_widget_construction_failure_cleans_up_and_can_reopen(self):
        with patch.object(view.ttk, 'Notebook', side_effect=RuntimeError('/private/widget')):
            self.assertIsNone(self.launcher.open())
        roots = [w for w in self.created if w.kind == 'Toplevel']
        self.assertEqual(len(roots), 1)
        self.assertTrue(roots[0].destroyed)
        self.assertIsNone(self.launcher.window)
        self.error.assert_called_once_with('Open Dashboard', app.OPEN_ERROR, parent=self.parent)
        self.assert_main_survives()
        self.assertIsNotNone(self.launcher.open())

    def test_partial_construction_cancellation_cleans_up_without_swallowing(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error_type=error_type):
                failure = error_type('cancel')
                with patch.object(view.ttk, 'Frame', side_effect=failure):
                    with self.assertRaises(error_type) as caught:
                        self.launcher.open()
                self.assertIs(caught.exception, failure)
                self.assertTrue(self.created[-1].destroyed)
                self.assertIsNone(self.launcher.window)
                self.assert_main_survives()
        self.error.assert_not_called()
        self.assertIsNotNone(self.launcher.open())

    def test_focus_failure_keeps_existing_window_and_does_not_duplicate(self):
        first = self.launcher.open()
        with patch.object(first.root, 'lift', side_effect=RuntimeError('/private/focus')):
            self.assertIsNone(self.launcher.open())
        self.assertIs(self.launcher.window, first)
        self.assertEqual(self.error.call_args.args[1], app.OPEN_ERROR)
        self.assertIs(self.launcher.open(), first)
        self.assertEqual(len([w for w in self.created if w.kind == 'Toplevel']), 1)
        self.assert_main_survives()

    def test_explicit_paths_required_without_mapping_fallback(self):
        for parent, paths in ((None, self.paths), (self.parent, None),
                (self.parent, RuntimePaths(self.paths.checklist, self.paths.history, self.paths.profiles))):
            with self.assertRaises(ValueError), forbid_io():
                app.DashboardLauncher(parent, paths)

    def test_explicit_choose_binds_backup_factory_to_exact_runtime_paths(self):
        from test_dashboard_controller import BackupEvidence
        self.backup.return_value = BackupEvidence()
        window = self.launcher.open()
        window.backup_buttons['choose'].invoke()
        self.backup.assert_not_called()
        self.assertTrue(window.controller.backup_state['selected'])
        window.backup_buttons['assess'].invoke()
        self.backup.assert_called_once_with('synthetic-archive.zip', paths=self.paths)
        self.assertTrue(window.controller.backup_state['healthy'])
        self.capture.assert_not_called()

    def test_default_archive_dialog_is_owned_by_dashboard_and_never_scans(self):
        from tkinter import filedialog
        launcher = self.launcher_for(choose_archive=None)
        window = launcher.open()
        with patch.object(filedialog, 'askopenfilename', return_value='') as dialog:
            dialog.assert_not_called()
            window.backup_buttons['choose'].invoke()
        dialog.assert_called_once_with(parent=window.root, title='Choose archive to assess',
                                       filetypes=[('ZIP backup', '*.zip')])
        self.backup.assert_not_called()
        self.assertFalse(window.controller.backup_state['selected'])

    def test_capture_model_and_revalidation_errors_are_sanitized(self):
        for stage in ('capture', 'model', 'revalidate'):
            with self.subTest(stage=stage):
                failure = OSError('/private/source payload')
                if stage == 'capture':
                    with patch.object(self.launcher, 'capture_factory', side_effect=failure):
                        with self.assertRaisesRegex(ValueError, '^' + app.SOURCE_ERROR):
                            self.launcher._capture(as_of=DAY)
                else:
                    class Evidence:
                        @property
                        def model(self): raise failure
                        def revalidate(self): raise failure
                    evidence = app._ApplicationCapture(Evidence())
                    with self.assertRaisesRegex(ValueError, '^' + app.SOURCE_ERROR):
                        if stage == 'model': evidence.model
                        else: evidence.revalidate()

    def test_analytical_error_message_in_model_is_sanitized(self):
        evidence = app._ApplicationCapture(SimpleNamespace(model=dict(insights=dict(error='/private/diagnostic'))))
        self.assertEqual(evidence.model['insights']['error'], app.SOURCE_ERROR)

    def test_refresh_failure_is_visible_private_and_recoverable(self):
        self.capture.side_effect = OSError('/private/source payload')
        window = self.launcher.open()
        window.refresh_button.invoke()
        self.assertIn(app.SOURCE_ERROR, window.notice.get())
        self.assertNotIn('/private', window.notice.get())
        self.assertFalse(window._busy)
        self.assertEqual(window.refresh_button.options['state'], 'normal')
        self.capture.assert_called_once_with(self.paths, as_of=DAY, cost_start_date=None,
            cost_end_date=None, selected_profile_id=None, explicit_current_mileage=None)
        self.assert_main_survives()

    def test_real_read_only_stack_refresh_and_backup_use_only_temporary_fixtures(self):
        import data_backup
        fixture = source_fixtures.TestDashboardSources()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        launcher = app.DashboardLauncher(self.parent, fixture.paths, ask_date=self.ask,
            choose_archive=self.chooser, show_error=self.error)
        files = tuple(fixture.paths.mapping().values())
        before = {path: path.read_bytes() for path in files}
        with forbid_io():
            window = launcher.open()
        window.refresh_button.invoke()
        self.assertEqual(window.controller.state['status'], 'ready')
        self.assertIn('Current presentation', window.content_notice.get())
        archive = data_backup.create_backup(fixture.paths, fixture.root,
            timestamp=datetime.fromisoformat('2026-11-11T12:00:00+00:00'))
        tree_before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in fixture.root.iterdir()}
        self.chooser.return_value = str(archive)
        window.backup_buttons['choose'].invoke()
        window.backup_buttons['assess'].invoke()
        self.assertTrue(window.controller.backup_state['healthy'])
        self.assertNotIn(str(fixture.root), window.panels['backup'].content)
        self.assertEqual({path: path.read_bytes() for path in files}, before)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in fixture.root.iterdir()}, tree_before)
        self.error.assert_not_called()


class TestMainApplicationDashboardControl(unittest.TestCase):
    def test_startup_binds_explicit_paths_without_opening_and_control_delegates(self):
        fixture = gui_fixtures.TestChecklistGUI()
        try:
            with patch.object(app, 'DashboardLauncher') as factory, \
                 patch.object(app, 'capture_dashboard_sources') as capture, \
                 patch.object(app, 'assess_backup_health') as backup, \
                 patch.object(gui.tk, 'Tk', side_effect=AssertionError('Second root forbidden')), \
                 patch.object(gui.tk, 'mainloop', side_effect=AssertionError('Second loop forbidden')):
                fixture.setUp()
                factory.assert_not_called()
                capture.assert_not_called()
                backup.assert_not_called()
                main = fixture.app
                self.assertIsNone(main.optional_dashboard)
                buttons = {call.kwargs['text']: call.kwargs['command']
                           for call in fixture.widgets['Button'].call_args_list}
                self.assertIn('Open Dashboard', buttons)
                factory.return_value.open.return_value = 'opened'
                self.assertEqual(buttons['Open Dashboard'](), 'opened')
                self.assertEqual(buttons['Open Dashboard'](), 'opened')
                factory.assert_called_once_with(fixture.root, main.runtime_paths())
                self.assertIs(main.optional_dashboard, factory.return_value)
                self.assertEqual(factory.return_value.open.call_count, 2)
            fixture.root.destroy.assert_not_called()
        finally:
            fixture.doCleanups()

    def test_missing_optional_modules_preserve_startup_and_allow_retry(self):
        import builtins
        original_import = builtins.__import__
        def import_without_dashboard(name, *args, **kwargs):
            if name == 'dashboard_app':
                raise ImportError('/private/deployment/module')
            return original_import(name, *args, **kwargs)
        fixture = gui_fixtures.TestChecklistGUI()
        try:
            with patch('builtins.__import__', side_effect=import_without_dashboard), \
                 patch.object(gui.messagebox, 'showerror') as error:
                fixture.setUp()
                fixture.app.open_optional_dashboard()
                self.assertIsNone(fixture.app.optional_dashboard)
                error.assert_called_once()
                self.assertNotIn('/private', str(error.call_args))
                fixture.root.destroy.assert_not_called()
            with patch.object(app, 'DashboardLauncher') as factory:
                fixture.app.open_optional_dashboard()
                factory.return_value.open.assert_called_once_with()
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
