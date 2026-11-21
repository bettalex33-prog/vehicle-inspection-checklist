"""Local macOS packaged entry point. No data discovery or automatic migration."""
import fcntl
import os
from pathlib import Path
import platform
import subprocess
import sys
from contextlib import contextmanager

# Isolated Python omits the script directory. Only this bundled directory is added.
sys.dont_write_bytecode = True
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from application_paths import packaged_paths, RUNTIME
from data_backup import validate_backup, restore_backup, reject_output_alias


STARTUP_MESSAGES = {
    "architecture": "This local application requires native ARM64 execution on an Apple Silicon Mac.",
    "runtime": "Required runtime: " + RUNTIME + ". Launch the application with its bundled launcher.",
    "duplicate": "Vehicle Inspection Checklist is already running. Close the existing instance before opening another.",
}


class StartupFailure(RuntimeError):
    """Known startup condition with a fixed, non-sensitive message."""
    def __init__(self, code):
        self.code = code
        super().__init__(STARTUP_MESSAGES.get(code, "Application startup failed."))


def native_error(message):
    # Pass text as argv, never interpolate it into AppleScript source.
    script = ('on run argv\n display alert "Vehicle Inspection Checklist" '
              'message (item 1 of argv) as critical\nend run')
    subprocess.run(["/usr/bin/osascript", "-e", script, str(message)], check=False)


@contextmanager
def instance_guard(directory):
    """Hold an advisory lock for the process lifetime; never unlink its inode."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / ".instance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupFailure("duplicate") from None
        yield
    finally:
        os.close(descriptor)


def prepare_data(root, paths, dialogs, picker):
    """Return False on cancellation; never initialize stores before restore choice."""
    mapping = paths.mapping()
    existing = [name for name, path in mapping.items() if path.exists()]
    if existing:
        return True
    choice = dialogs.askyesnocancel("First launch",
        "Restore a backup? Yes: choose a ZIP backup. No: start a new checklist. Cancel: quit. "
        "The source installation uses separate data.", parent=root)
    if choice is None:
        return False
    if not choice:
        return True
    selected = picker.askopenfilename(parent=root, title="Choose existing backup",
                                      filetypes=[("ZIP backup", "*.zip")])
    if not selected:
        return False
    reject_output_alias(selected, mapping.values())
    backup = validate_backup(Path(selected))
    included = [name for name, _ in backup.files]
    if not dialogs.askyesno("Restore backup", "Replace/create: " + ", ".join(included) +
            "\nOmitted files remain untouched. Restore now?", parent=root):
        return False
    restore_backup(backup, paths, confirmed=True)
    return True


def destroy_root(root):
    from tkinter import TclError

    try:
        root.destroy()
    except TclError as error:
        # The main Close button can destroy the root before mainloop returns.
        if str(error) != 'cannot invoke "destroy" command: application has been destroyed':
            raise


def run(*, home=None):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise StartupFailure("architecture")
    if Path(sys.executable).resolve() != Path(RUNTIME).resolve():
        raise StartupFailure("runtime")
    paths = packaged_paths(home)
    with instance_guard(paths.checklist.parent):
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from gui import ChecklistApp
        root = tk.Tk()
        root.withdraw()
        try:
            if not prepare_data(root, paths, messagebox, filedialog):
                return
            ChecklistApp(root, paths.checklist, paths.history, paths.profiles, paths.maintenance)
            root.deiconify()
            root.mainloop()
        finally:
            destroy_root(root)


def main():
    try:
        run()
    except StartupFailure as error:
        native_error(STARTUP_MESSAGES.get(error.code, "Application startup failed."))
        return 1
    except Exception as error:
        # Avoid exposing record contents or tracebacks in diagnostics.
        native_error("Startup failed (" + type(error).__name__ + "). Required Python/Tk: " + RUNTIME +
                     ". Check runtime availability, data permissions, and whether the app is already open.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
