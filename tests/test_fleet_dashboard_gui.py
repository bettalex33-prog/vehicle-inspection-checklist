"""Every Tk window, widget, variable and dialog is mocked; stores are disposable."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import fleet_dashboard_gui as dg
from test_fleet_dashboard import TODAY, PID, OTHER, profile, task, service


class TestFleetDashboardGUI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.schedule = self.root / 'custom-maintenance.json'
        self.profiles = self.root / 'custom-profiles.json'
        self.schedule.write_text(json.dumps({'schema_version': 2, 'tasks': [task(
            interval_miles=1000, warning_miles=100, baseline_mileage=48001,
            scheduled_date=TODAY.isoformat(), services=[service(labor_cost='62.37')])]}))
        self.profiles.write_text(json.dumps({'schema_version': 1, 'profiles': [profile(), profile(OTHER)]}))
        self.runtime = (self.schedule, self.profiles, self.root / 'custom-checklist.json', self.root / 'custom-inspections.json')
        for path in self.runtime[2:]:
            path.write_bytes(b'protected')
        self.before = {p: p.read_bytes() for p in self.runtime}
        self.widgets = {}
        for namespace, names in ((dg.tk, ('Toplevel', 'Frame', 'Label', 'Entry', 'Button')),
                                  (dg.ttk, ('Combobox', 'Treeview', 'Scrollbar'))):
            for name in names:
                factory = lambda *a, **k: MagicMock()
                if name == 'Combobox':
                    def factory(*args, **kwargs):
                        widget = MagicMock(); index = [-1]
                        def current(value=None):
                            if value is not None:
                                index[0] = value
                            return index[0]
                        widget.current.side_effect = current
                        return widget
                patcher = patch.object(namespace, name, side_effect=factory)
                self.widgets[name] = patcher.start(); self.addCleanup(patcher.stop)
        def variable(*args, **kwargs):
            value, callbacks = [kwargs.get('value', '')], []
            mock = MagicMock()
            mock.get.side_effect = lambda: value[0]
            def change(new):
                value[0] = new
                for callback in callbacks:
                    callback('', '', 'write')
            mock.set.side_effect = change
            mock.trace_add.side_effect = lambda mode, callback: callbacks.append(callback)
            return mock
        patcher = patch.object(dg.tk, 'StringVar', side_effect=variable)
        patcher.start(); self.addCleanup(patcher.stop)
        for namespace, names in ((dg.messagebox, ('showerror', 'showinfo', 'askyesno')),
                                  (dg.filedialog, ('asksaveasfilename',))):
            for name in names:
                patcher = patch.object(namespace, name)
                setattr(self, name, patcher.start()); self.addCleanup(patcher.stop)
        patcher = patch.object(dg, 'date'); clock = patcher.start(); self.addCleanup(patcher.stop)
        clock.today.return_value = TODAY
        self.open_task = MagicMock()
        self.window = dg.FleetDashboardWindow(MagicMock(), self.schedule, self.profiles,
                                             runtime_paths=self.runtime, open_task=self.open_task)

    def test_buttons_default_date_only_and_no_runtime_changes(self):
        self.assertTrue(self.window.ready)
        self.assertIsNone(self.window.model['tasks'][0]['current_mileage'])
        self.assertTrue(self.window.model['tasks'][0]['mileage_unknown'])
        buttons = {c.kwargs['text']: c.kwargs['command'] for c in self.widgets['Button'].call_args_list}
        self.assertEqual(buttons['Evaluate / Refresh'], self.window.refresh)
        self.assertEqual(buttons['Export CSV'], self.window.export_csv)
        self.assertEqual(buttons['Export HTML'], self.window.export_html)
        self.assertEqual(buttons['Open Task / Full History'], self.window.navigate)
        self.assertEqual({p: p.read_bytes() for p in self.runtime}, self.before)

    def test_explicit_selection_switch_clear_and_disabled_export_when_dirty(self):
        self.window.profile_selector.current(1); self.window.select_profile()
        self.assertEqual(self.window.mileage.get(), '')
        self.window.mileage.set('48001')
        self.assertFalse(self.window.ready)
        self.window.buttons['Export CSV'].configure.assert_called_with(state=dg.tk.DISABLED)
        self.window.refresh()
        self.assertTrue(self.window.ready)
        self.assertEqual(self.window.model['tasks'][0]['current_mileage'], 48001)
        self.window.profile_selector.current(2); self.window.select_profile()
        self.assertEqual(self.window.mileage.get(), '')
        self.assertIsNone(self.window.model['tasks'][0]['current_mileage'])
        self.assertEqual({p: p.read_bytes() for p in self.runtime}, self.before)

    def test_invalid_mileage_unavailable_and_recovery(self):
        self.window.profile_selector.current(1); self.window.select_profile()
        for text in ('-1', '1.2', 'True', '48000'):
            self.window.mileage.set(text); self.window.refresh()
            self.assertFalse(self.window.ready)
            self.assertIsNone(self.window.model)
            self.assertIn('Unavailable', self.window.totals.get())
        self.window.mileage.set('48001'); self.window.refresh()
        self.assertTrue(self.window.ready)
        self.window.profile_selector.current(0)
        self.window.mileage.set('48001'); self.window.refresh()
        self.assertFalse(self.window.ready)

    def test_corrupt_sources_never_show_zero_totals(self):
        for path in (self.schedule, self.profiles):
            original = path.read_bytes(); path.write_bytes(b'broken')
            self.window.refresh()
            self.assertFalse(self.window.ready)
            self.assertIsNone(self.window.snapshot)
            self.assertEqual(self.window.totals.get(), 'Unavailable — refresh required')
            self.window.export_csv()
            self.asksaveasfilename.assert_not_called()
            path.write_bytes(original); self.window.refresh()
            self.assertTrue(self.window.ready)

    def test_deleted_profile_and_restore_reset_clear_mileage(self):
        self.window.profile_selector.current(1); self.window.select_profile()
        self.window.mileage.set('48001'); self.window.refresh()
        self.profiles.write_text(json.dumps({'schema_version': 1, 'profiles': [profile(OTHER)]}))
        self.window.refresh()
        self.assertEqual(self.window.mileage.get(), '')
        self.assertIsNone(self.window.selected_profile_id())
        self.assertTrue(self.window.model['tasks'][0]['profile_missing'])
        self.profiles.write_bytes(self.before[self.profiles]); self.window.refresh()
        self.window.profile_selector.current(1); self.window.select_profile()
        self.window.mileage.set('48001'); self.window.refresh()
        self.window.refresh(reset=True)
        self.assertEqual(self.window.mileage.get(), '')
        self.assertIsNone(self.window.model['tasks'][0]['current_mileage'])

    def test_cost_filter_and_navigation_keep_complete_history(self):
        self.window.start.set('2026-10-24'); self.window.refresh()
        self.assertEqual(self.window.model['costs']['service_count'], 0)
        self.assertEqual(len(self.window.model['tasks'][0]['task']['services']), 1)
        self.window.tasks.selection.return_value = (task()['task_id'],)
        self.window.select_task(); self.window.navigate()
        self.open_task.assert_called_once_with(task()['task_id'])
        self.window.appointments.selection.return_value = (task()['task_id'],)
        self.window.select_appointment()
        self.assertEqual(self.window.active_task_id, task()['task_id'])
        self.window.end.set('2026-10-23'); self.window.refresh()
        self.assertFalse(self.window.ready)

    def test_status_filter_and_appointment_buckets_render(self):
        tasks = [task(10 + i, scheduled_date=(TODAY + timedelta(days=n)).isoformat())
                 for i, n in enumerate((-1, 7, 30, 90))]
        tasks.append(task(20, enabled=False, scheduled_date=TODAY.isoformat()))
        self.schedule.write_text(json.dumps({'schema_version': 2, 'tasks': tasks})); self.window.refresh()
        values = [c.kwargs['values'][0] for c in self.window.appointments.insert.call_args_list[-4:]]
        self.assertEqual(values, list(dg.BUCKETS))
        self.window.tasks.insert.reset_mock()
        self.window.status.set('Disabled'); self.window.render()
        self.assertEqual(self.window.tasks.insert.call_count, 1)
        self.assertEqual(self.window.tasks.insert.call_args.kwargs['values'][2], 'Disabled')

    def test_stale_navigation_and_exports_require_refresh(self):
        self.window.tasks.selection.return_value = (task()['task_id'],)
        self.window.select_task()
        self.schedule.write_bytes(self.schedule.read_bytes() + b'\n')
        self.window.navigate()
        self.open_task.assert_not_called(); self.assertFalse(self.window.ready)
        self.window.refresh()
        output = self.root / 'dashboard.csv'
        self.asksaveasfilename.return_value = str(output)
        self.profiles.write_bytes(self.profiles.read_bytes() + b'\n')
        self.window.export_csv()
        self.assertFalse(output.exists()); self.assertFalse(self.window.ready)

    def test_export_cancel_confirmation_alias_and_success(self):
        self.asksaveasfilename.return_value = ''
        self.window.export_csv(); self.askyesno.assert_not_called()
        output = self.root / 'dashboard.csv'; output.write_bytes(b'old')
        self.asksaveasfilename.return_value = str(output); self.askyesno.return_value = False
        self.window.export_csv(); self.assertEqual(output.read_bytes(), b'old')
        self.askyesno.return_value = True
        self.window.export_csv(); self.assertTrue(output.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.showinfo.assert_called_once()
        html = self.root / 'dashboard.html'; self.asksaveasfilename.return_value = str(html)
        self.window.export_html(); self.assertIn('Captured profile snapshot', html.read_text())
        self.asksaveasfilename.return_value = str(self.runtime[2])
        self.window.export_csv(); self.assertFalse(self.window.ready)
        self.assertEqual({p: p.read_bytes() for p in self.runtime}, self.before)

    def test_output_changed_during_confirmation_is_preserved(self):
        output = self.root / 'dashboard.html'; output.write_bytes(b'confirmed')
        self.asksaveasfilename.return_value = str(output)
        def confirm(*args, **kwargs):
            output.write_bytes(b'newer report')
            return True
        self.askyesno.side_effect = confirm
        self.window.export_html()
        self.assertEqual(output.read_bytes(), b'newer report')
        self.assertFalse(self.window.ready)
        self.showerror.assert_called_once()
