"""No real Tk windows: exercise maintenance callbacks against temporary stores."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import maintenance_gui as mg
import maintenance_schedule as ms
from vehicle_profiles import create_profile
from test_maintenance_schedule import details


class TestMaintenanceGUI(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'maintenance_schedule.json'
        self.profiles = self.root / 'vehicle_profiles.json'
        self.profile = create_profile(self.profiles, 'Unit', '', '', '', '42000')
        self.widgets = {}
        for namespace, names in ((mg.tk, ('Frame', 'Label', 'Entry', 'Button', 'Checkbutton', 'Toplevel', 'Text')),
                                  (mg.ttk, ('Combobox', 'Treeview'))):
            for name in names:
                patcher = patch.object(namespace, name, side_effect=lambda *a, **k: MagicMock())
                self.widgets[name] = patcher.start(); self.addCleanup(patcher.stop)
        combo = self.widgets['Combobox'].side_effect
        def combobox(*args, **kwargs):
            widget = combo(*args, **kwargs)
            current = [-1]
            def select(index=None):
                if index is not None:
                    current[0] = index
                return current[0]
            widget.current.side_effect = select
            return widget
        self.widgets['Combobox'].side_effect = combobox
        # Values behave like actual Tcl variables, without requiring a display.
        def variable(*a, **k):
            value = [k.get('value', '')]
            mock = MagicMock()
            mock.get.side_effect = lambda: value[0]
            mock.set.side_effect = lambda new: value.__setitem__(0, new)
            return mock
        for name in ('StringVar', 'BooleanVar'):
            patcher = patch.object(mg.tk, name, side_effect=variable)
            patcher.start(); self.addCleanup(patcher.stop)
        for name in ('showerror', 'askyesno'):
            patcher = patch.object(mg.messagebox, name)
            setattr(self, name, patcher.start()); self.addCleanup(patcher.stop)
        fixed_date = patch.object(mg, 'date')
        self.mock_date = fixed_date.start(); self.addCleanup(fixed_date.stop)
        self.mock_date.today.return_value = date(2026, 10, 22)
        self.window = mg.MaintenanceWindow(MagicMock(), self.path, self.profiles)
        self.window.profile_selector.current(2)
        self.window.tree.selection.return_value = ()

    def create(self):
        return ms.create_task(self.path, self.profile['profile_id'], details(), profiles_path=self.profiles, today=date.today())

    def select(self, task):
        self.window.tasks = ms.load_schedule(self.path)['tasks']
        self.window.tree.selection.return_value = (task['task_id'],)

    def editor(self, task=None):
        return mg.MaintenanceTaskEditor(MagicMock(), self.path, self.profiles, self.profile['profile_id'], MagicMock(), task=task)

    def fill(self, editor):
        for key, value in details().items():
            if key != 'enabled':
                editor.fields[key].set('' if value is None else str(value))

    def test_missing_read_no_write_and_button_connections(self):
        self.assertFalse(self.path.exists())
        buttons = {call.kwargs['text']: call.kwargs['command'] for call in self.widgets['Button'].call_args_list}
        self.assertEqual(buttons['New Task…'], self.window.new_task)
        self.assertEqual(buttons['Delete / Archive…'], self.window.remove_task)
        with patch.object(mg, 'MaintenanceTaskEditor') as editor:
            self.window.new_task(); editor.assert_called_once()
        self.window.select_profile()
        self.assertEqual(self.window.mileage.get(), '')

    def test_archived_filter_excludes_from_default_and_preserves_history(self):
        task = self.create()
        task = ms.record_service(self.path, task['task_id'], dict(completed_date='2026-10-22', mileage=46500, notes='done'),
             profiles_path=self.profiles, expected_task=task, today=date.today())
        self.select(task)
        self.askyesno.return_value = False
        before = self.path.read_bytes(); self.window.remove_task()
        self.assertEqual(self.path.read_bytes(), before)
        self.askyesno.return_value = True
        self.window.remove_task()
        archived = ms.load_schedule(self.path)['tasks'][0]
        self.assertTrue(archived['archived']); self.assertEqual(archived['services'], task['services'])
        self.window.tasks = [archived]
        self.window.tree.insert.reset_mock(); self.window.render()
        self.window.tree.insert.assert_not_called()
        self.window.view.set('Archived'); self.window.render()
        self.window.tree.insert.assert_called_once()
        self.window.show_details()
        self.assertIn('done', self.window.details.insert.call_args.args[1])
        self.window.remove_task()
        self.assertEqual(ms.load_schedule(self.path)['tasks'][0], archived)

    def test_empty_task_deletion_requires_confirmation(self):
        task = self.create(); self.select(task)
        self.askyesno.return_value = False; self.window.remove_task()
        self.assertEqual(ms.load_schedule(self.path)['tasks'], [task])
        self.askyesno.return_value = True; self.window.remove_task()
        self.assertEqual(ms.load_schedule(self.path)['tasks'], [])

    def test_editor_validation_failure_cancel_and_success(self):
        editor = self.editor(); editor.save()
        self.showerror.assert_called_once(); self.assertFalse(self.path.exists())
        editor.root.destroy.assert_not_called()
        self.fill(editor); editor.save()
        editor.on_saved.assert_called_once(); editor.root.destroy.assert_called_once()
        self.assertEqual(ms.load_schedule(self.path)['tasks'][0]['title'], 'Oil')
        before = self.path.read_bytes()
        cancelled = self.editor(); cancelled.root.destroy()
        self.assertEqual(self.path.read_bytes(), before)

    def test_service_confirmation_failure_stale_edit_and_draft_retention(self):
        task = self.create()
        editor = self.editor(task); self.fill(editor)
        dialog = mg.RecordServiceDialog(MagicMock(), self.path, self.profiles, task, MagicMock())
        dialog.fields['completed_date'].set('2026-10-22'); dialog.fields['mileage'].set('46500')
        self.askyesno.return_value = False; dialog.save()
        self.assertEqual(ms.load_schedule(self.path)['tasks'][0], task)
        self.askyesno.return_value = True; dialog.save()
        dialog.on_saved.assert_called_once()
        editor.save()
        self.showerror.assert_called_once(); editor.root.destroy.assert_not_called()
        self.assertEqual(editor.fields['title'].get(), 'Oil')
        updated = ms.load_schedule(self.path)['tasks'][0]
        locked = self.editor(updated)
        self.assertEqual(len(updated['services']), 1)
        self.assertIsNotNone(locked)

    def test_corruption_disables_actions_without_writes_and_refresh_retains_editor(self):
        editor = self.editor(); self.fill(editor)
        self.path.write_bytes(b'corrupt'); self.window.refresh()
        self.assertFalse(self.window.available)
        self.assertEqual(self.path.read_bytes(), b'corrupt')
        for button in self.window.buttons.values():
            button.configure.assert_called_with(state=mg.tk.DISABLED)
        self.assertEqual(editor.fields['title'].get(), 'Oil')

    def test_custom_paths_and_missing_profile_controls(self):
        task = self.create(); self.select(task)
        self.profiles.write_text('{"schema_version":1,"profiles":[]}')
        self.window.profiles = []; self.window.profile_selector.current(1)
        self.window.render()
        self.assertIn('missing profile', self.window.tree.insert.call_args.kwargs['values'][0])
        self.window.update_buttons()
        self.window.buttons['Record Service…'].configure.assert_called_with(state=mg.tk.DISABLED)
        self.window.buttons['New Task…'].configure.assert_called_with(state=mg.tk.DISABLED)

    def test_current_mileage_is_explicit_and_never_changes_profile(self):
        before = self.profiles.read_bytes()
        self.window.mileage.set('99999')
        self.window.select_profile()
        self.assertEqual(self.window.mileage.get(), '')
        self.window.mileage.set('46500')
        self.create()
        self.window.refresh()
        self.assertEqual(self.window.mileage.get(), '46500')
        self.assertEqual(self.profiles.read_bytes(), before)
        self.window.profile_selector.current(0)
        self.window.select_profile()
        self.assertEqual(self.window.mileage.get(), '')
