"""Optional application boundary for the read-only dashboard.

Paths and the Tk parent belong to the caller. Opening asks for an explicit date;
it never captures sources, selects a backup, or reads the clock.
"""
from datetime import date
from tkinter import messagebox, simpledialog

from backup_health import assess_backup_health
from dashboard_controller import DashboardController
from dashboard_gui import DashboardWindow
from dashboard_sources import capture_dashboard_sources
from data_backup import RuntimePaths


OPEN_ERROR = 'Dashboard could not be opened. Close it and try again.'
SOURCE_ERROR = 'Dashboard sources could not be read or revalidated. Refresh to try again.'


class _ApplicationCapture:
    """Keep filesystem exception diagnostics out of the application view."""
    def __init__(self, capture):
        self.capture = capture

    @property
    def model(self):
        try:
            model = self.capture.model
            if model['insights'].get('error'):
                model['insights']['error'] = SOURCE_ERROR
            return model
        except Exception:
            raise ValueError(SOURCE_ERROR) from None

    def revalidate(self):
        try:
            return self.capture.revalidate()
        except Exception:
            raise ValueError(SOURCE_ERROR) from None


class _ApplicationWindow(DashboardWindow):
    """Destroy a partially built Toplevel even when widget setup fails."""
    def __init__(self, parent, controller, **options):
        try:
            super().__init__(parent, controller, **options)
        except BaseException:
            window = getattr(self, 'root', None)
            if window is not None and window is not parent:
                window.destroy()
            raise


class DashboardLauncher:
    """One optional window per application; factories are passive until requested.

    Injected window factories must clean up their own partial construction, as
    the production factory does. No workflow callbacks are supplied: this
    application surface only inspects data.
    """
    def __init__(self, parent, paths, *, capture_factory=None, backup_factory=None,
                 controller_factory=None, window_factory=None, ask_date=None,
                 choose_archive=None, show_error=None):
        if parent is None or not isinstance(paths, RuntimePaths) or any(
                value is None for value in (paths.checklist, paths.history,
                                           paths.profiles, paths.maintenance)):
            raise ValueError('An existing parent and four explicit runtime paths are required.')
        self.parent, self.paths = parent, paths
        self.capture_factory = capture_dashboard_sources if capture_factory is None else capture_factory
        self.backup_factory = assess_backup_health if backup_factory is None else backup_factory
        self.controller_factory = DashboardController if controller_factory is None else controller_factory
        self.window_factory = _ApplicationWindow if window_factory is None else window_factory
        self.ask_date = ask_date
        self.choose_archive = choose_archive
        self.show_error = messagebox.showerror if show_error is None else show_error
        self.window = None
        self._opening = False

    def _capture(self, **inputs):
        try:
            return _ApplicationCapture(self.capture_factory(self.paths, **inputs))
        except Exception:
            raise ValueError(SOURCE_ERROR) from None

    def _assess(self, archive):
        return self.backup_factory(archive, paths=self.paths)

    def _choose_archive(self):
        # The dialog is imported and invoked only by an explicit Choose command.
        if self.choose_archive is not None:
            return self.choose_archive()
        from tkinter import filedialog
        return filedialog.askopenfilename(parent=self.window.root,
            title='Choose archive to assess', filetypes=[('ZIP backup', '*.zip')])

    def open(self):
        if self._opening:
            return None
        self._opening = True
        try:
            if self.window is not None:
                if not self.window._closed and self.window.root.winfo_exists():
                    self.window.root.deiconify()
                    self.window.root.lift()
                    self.window.root.focus_set()
                    return self.window
                self.window = None
            text = (self.ask_date() if self.ask_date is not None else
                simpledialog.askstring('Open Dashboard',
                    'Evaluation date (YYYY-MM-DD). No data is read until Refresh.',
                    parent=self.parent))
            if text is None:
                return None
            try:
                as_of = date.fromisoformat(text)
                if as_of.isoformat() != text:
                    raise ValueError()
            except (TypeError, ValueError):
                self.show_error('Open Dashboard', 'Enter an evaluation date as YYYY-MM-DD.',
                                parent=self.parent)
                return None
            controller = self.controller_factory(self._capture, as_of=as_of,
                                                 backup_assessment_factory=self._assess)
            self.window = self.window_factory(self.parent, controller,
                                             choose_backup_archive=self._choose_archive)
            return self.window
        except Exception:
            self.show_error('Open Dashboard', OPEN_ERROR, parent=self.parent)
            return None
        finally:
            self._opening = False
