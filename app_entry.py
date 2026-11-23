"""Local macOS packaged entry point. No data discovery or automatic migration."""
import fcntl
import os
from pathlib import Path
import plistlib
import platform
import stat
import subprocess
import sys
from contextlib import ExitStack, contextmanager

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


SYSTEM_LIBRARY_ROOTS = (Path("/System/Library"), Path("/usr/lib"))
DATA_VOLUME = Path("/System/Volumes/Data")
OS_CRYPTEX_LIBRARY = Path("/System/Volumes/Preboot/Cryptexes/OS/System/Library")
MAX_PLIST_BYTES = 64 * 1024
MAX_STDLIB_BYTES = 16 * 1024 * 1024
STOCK_ALIASES = {
    "base_library.zip": "../Resources/base_library.zip",
    "_tcl_data": "../Resources/_tcl_data",
    "_tk_data": "../Resources/_tk_data",
}


def is_system_image(path):
    path = Path(path)
    resolved = path.resolve()
    if (".." in path.parts or path.is_relative_to(DATA_VOLUME)
            or resolved.is_relative_to(DATA_VOLUME)):
        return False
    for root in SYSTEM_LIBRARY_ROOTS:
        if path.is_relative_to(root) and resolved.is_relative_to(root) and resolved != root:
            return True
    # macOS 15.5 advertises WebKit and related images under the sealed
    # /System/Library tree. Their Apple-supplied links point to the identical
    # relative path in the OS Cryptex. Direct Preboot paths remain untrusted.
    root = SYSTEM_LIBRARY_ROOTS[0]
    if path.is_relative_to(root):
        relative = path.relative_to(root)
        return (relative.parts[:1] in (("Frameworks",), ("PrivateFrameworks",))
                and resolved == OS_CRYPTEX_LIBRARY / relative)
    return False


def _identity(info, *, contents=False):
    identity = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
    if contents:
        return (*identity, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return identity


def owned_fd(stack, name, flags, *, dir_fd=None):
    """Register the acquired descriptor once. Never retry close."""
    descriptor = os.open(name, flags, dir_fd=dir_fd)
    stack.callback(os.close, descriptor)
    return descriptor


class BundleIdentity:
    """Pin ordinary bundle entries by descriptor until validation is complete."""

    def __init__(self, bundle, stack):
        self.bundle = bundle
        self.stack = stack
        self.entries = {}
        self.aliases = {}

    def _open(self, relative, kind, limit=None):
        path = self.bundle / relative
        parent = self.directory(relative.parent) if relative.parts else None
        name = relative.name if relative.parts else str(self.bundle)
        key = (relative, kind)
        if key in self.entries:
            return self.entries[key][0]
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        regular = kind == stat.S_IFREG
        if stat.S_IFMT(before.st_mode) != kind or (regular and before.st_nlink != 1):
            raise StartupFailure("runtime")
        if limit is not None and before.st_size > limit:
            raise StartupFailure("runtime")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if kind == stat.S_IFDIR:
            flags |= os.O_DIRECTORY
        descriptor = owned_fd(self.stack, name, flags, dir_fd=parent)
        fingerprint = _identity(before, contents=regular)
        if _identity(os.fstat(descriptor), contents=regular) != fingerprint:
            raise StartupFailure("runtime")
        self.entries[key] = (descriptor, path, fingerprint)
        return descriptor

    def directory(self, relative):
        return self._open(relative, stat.S_IFDIR)

    def file(self, relative, limit=None):
        return self._open(relative, stat.S_IFREG, limit)

    def read_plist(self):
        descriptor = self.file(Path("Contents/Info.plist"), MAX_PLIST_BYTES)
        size = os.fstat(descriptor).st_size
        if size > MAX_PLIST_BYTES:
            raise StartupFailure("runtime")
        chunks = []
        remaining = size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise StartupFailure("runtime")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise StartupFailure("runtime")
        self.revalidate()
        return plistlib.loads(b"".join(chunks))

    def stock_resources(self):
        frameworks = Path("Contents/Frameworks")
        resources = Path("Contents/Resources")
        for name, expected in STOCK_ALIASES.items():
            target = resources / name
            self.stock_alias(frameworks / name, expected, target,
                             directory=name != "base_library.zip", limit=MAX_STDLIB_BYTES)
        self.file(resources / "_tcl_data/init.tcl")
        self.file(resources / "_tk_data/tk.tcl")

    def stock_alias(self, relative, text, target, *, directory=False, limit=None):
        descriptor = self.directory(relative.parent)
        info = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        if (not stat.S_ISLNK(info.st_mode) or os.readlink(relative.name, dir_fd=descriptor) != text
                or os.path.isabs(text) or Path(os.path.normpath(relative.parent / text)) != target):
            raise StartupFailure("runtime")
        if directory:
            self.directory(target)
        else:
            self.file(target, limit)
        self.aliases[relative] = (_identity(info, contents=True), text, target)

    def revalidate(self):
        for (_, kind), (descriptor, path, expected) in self.entries.items():
            regular = kind == stat.S_IFREG
            if (_identity(path.lstat(), contents=regular) != expected
                    or _identity(os.fstat(descriptor), contents=regular) != expected):
                raise StartupFailure("runtime")
        for relative, (expected, text, target) in self.aliases.items():
            path = self.bundle / relative
            if (_identity(path.lstat(), contents=True) != expected or os.readlink(path) != text
                    or path.resolve(strict=True) != self.bundle / target):
                raise StartupFailure("runtime")


NATIVE_FRAMEWORKS = tuple(Path("Contents/Frameworks") / name for name in (
    "Tcl", "Tk", "Python.framework/Versions/3.15/Python"))
NATIVE_EXTENSIONS = Path("Contents/Frameworks/python3__dot__15/lib-dynload")
TK_EXTENSION = NATIVE_EXTENSIONS / "_tkinter.cpython-315-darwin.so"

def native_inventory(identity, launcher):
    """Return the permitted advertised paths and their pinned physical files."""
    frameworks = Path("Contents/Frameworks")
    physical = {launcher, *NATIVE_FRAMEWORKS, TK_EXTENSION}
    physical.update(frameworks / name for name in os.listdir(identity.directory(frameworks))
                    if name.endswith(".dylib"))
    physical.update(NATIVE_EXTENSIONS / name for name in os.listdir(identity.directory(NATIVE_EXTENSIONS))
                    if name.endswith(".so"))
    for image in sorted(physical):
        identity.file(image)
    advertised = {image: image for image in physical}
    # Pinned PyInstaller onedir layout exposes the bootloader's Python name
    # and the extension-directory spelling as these two exact native aliases.
    python_alias = frameworks / "Python"
    extension_alias = frameworks / "python3.15"
    if os.path.lexists(identity.bundle / python_alias):
        physical_python = NATIVE_FRAMEWORKS[2]
        identity.stock_alias(python_alias, "Python.framework/Versions/3.15/Python", physical_python)
        advertised[python_alias] = physical_python
    if os.path.lexists(identity.bundle / extension_alias):
        physical_directory = NATIVE_EXTENSIONS.parent
        identity.stock_alias(extension_alias, "python3__dot__15", physical_directory, directory=True)
        advertised.update({extension_alias / image.relative_to(physical_directory): image
                           for image in physical if image.is_relative_to(physical_directory)})
    return advertised


def _bundle_advertised_path(path, bundle):
    # Normalize the two macOS system shorthand prefixes without resolving away
    # any caller-controlled symlink in dyld's advertised spelling.
    if not path.is_absolute() or ".." in path.parts:
        return None
    if path.parts[1:2] in (("tmp",), ("var",)):
        path = Path("/private") / path.relative_to("/")
    return path.relative_to(bundle) if path.is_relative_to(bundle) else None


def _frozen_images(bundle, inventory):
    """Validate the images dyld has actually mapped, including Tk and Python."""
    import ctypes

    dyld = ctypes.CDLL(None)
    count = dyld._dyld_image_count
    count.restype = ctypes.c_uint32
    name = dyld._dyld_get_image_name
    name.argtypes = [ctypes.c_uint32]
    name.restype = ctypes.c_char_p
    paths = set()
    for index in range(count()):
        path = Path(os.fsdecode(name(index)))
        physical = path.resolve()
        if path.is_relative_to(DATA_VOLUME) or physical.is_relative_to(DATA_VOLUME):
            raise StartupFailure("runtime")
        advertised = _bundle_advertised_path(path, bundle)
        if advertised is not None or physical.is_relative_to(bundle):
            if advertised not in inventory or physical != bundle / inventory[advertised]:
                raise StartupFailure("runtime")
        elif not is_system_image(path):
            raise StartupFailure("runtime")
        paths.add(physical)
    if not {bundle / path for path in (*NATIVE_FRAMEWORKS, TK_EXTENSION)} <= paths:
        raise StartupFailure("runtime")
    # The Aqua port exports this symbol. Inspect the already-loaded image without
    # initializing Tk or creating an extra root to ask for its windowing system.
    aqua = ctypes.CDLL(str(bundle / NATIVE_FRAMEWORKS[1]), mode=os.RTLD_NOLOAD)
    if not hasattr(aqua, "TkMacOSXGetRootControl"):
        raise StartupFailure("runtime")


def _validate_frozen_runtime():
    if (getattr(sys, "frozen", None) is not True or sys.platform != "darwin"
            or platform.machine() != "arm64" or sys.version_info[:2] != (3, 15)):
        raise StartupFailure("runtime")

    executable = Path(sys.executable)
    contents = executable.parent.parent
    bundle = contents.parent
    if (not executable.is_absolute() or ".." in executable.parts
            or bundle.suffix != ".app" or contents.name != "Contents"
            or executable.parent.name != "MacOS"):
        raise StartupFailure("runtime")
    bundle = bundle.parent.resolve(strict=True) / bundle.name
    frameworks = bundle / "Contents/Frameworks"
    meipass = Path(sys._MEIPASS)
    if (not meipass.is_absolute() or ".." in meipass.parts
            or meipass.name != "Frameworks" or meipass.parent.name != "Contents"
            or meipass.parent.parent.name != bundle.name
            or meipass.parent.parent.parent.resolve(strict=True) != bundle.parent):
        raise StartupFailure("runtime")
    if (any(bundle.is_relative_to(path) for path in
            ("/opt/homebrew", "/usr/local/Cellar", "/usr/local/opt",
             "/Library/Frameworks/Python.framework"))
            or ".pyenv" in bundle.parts
            or any((parent / "pyvenv.cfg").is_file()
                   or ((parent / ".git").exists() and (parent / "build_macos_app.py").is_file())
                   for parent in bundle.parents)):
        raise StartupFailure("runtime")

    with ExitStack() as stack:
        identity = BundleIdentity(bundle, stack)
        launcher = identity.file(Path("Contents/MacOS") / executable.name)
        if not os.fstat(launcher).st_mode & 0o111:
            raise StartupFailure("runtime")
        info = identity.read_plist()
        if (info.get("CFBundleExecutable") != executable.name
                or info.get("CFBundlePackageType") != "APPL"
                or info.get("CFBundleIdentifier") != "local.kinu.vehicle-inspection-checklist"):
            raise StartupFailure("runtime")
        identity.stock_resources()
        inventory = native_inventory(identity, Path("Contents/MacOS") / executable.name)

        # The bootloader imports base_library.zip before this code runs.
        # These checks cannot establish pre-interpreter integrity. Ad-hoc
        # signatures serve local development; distribution needs Developer ID
        # signing and notarization of the finished bundle.
        import tkinter
        import _tkinter

        extension = Path(_tkinter.__file__)
        advertised = _bundle_advertised_path(extension, bundle)
        if ((tkinter.TclVersion, tkinter.TkVersion) != (9.0, 9.0)
                or inventory.get(advertised) != TK_EXTENSION
                or extension.resolve(strict=True) != bundle / TK_EXTENSION):
            raise StartupFailure("runtime")
        _frozen_images(bundle, inventory)
        identity.revalidate()
    for variable, directory in (("TCL_LIBRARY", "_tcl_data"), ("TK_LIBRARY", "_tk_data")):
        os.environ[variable] = str(frameworks.parent / "Resources" / directory)


def validate_runtime():
    """Return whether this is a validated frozen launch."""
    if hasattr(sys, "frozen") or hasattr(sys, "_MEIPASS"):
        try:
            _validate_frozen_runtime()
        except Exception:
            raise StartupFailure("runtime") from None
        return True
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise StartupFailure("architecture")
    if Path(sys.executable).resolve() != Path(RUNTIME).resolve():
        raise StartupFailure("runtime")
    return False


def run(*, home=None):
    frozen = validate_runtime()
    paths = packaged_paths(home)
    with instance_guard(paths.checklist.parent):
        import tkinter as tk
        from tkinter import filedialog, messagebox
        if not frozen:
            from gui import ChecklistApp
        try:
            root = tk.Tk()
        except tk.TclError:
            if frozen:
                raise StartupFailure("runtime") from None
            raise
        root.withdraw()
        try:
            if frozen:
                if root.tk.call("tk", "windowingsystem") != "aqua":
                    raise StartupFailure("runtime")
                from gui import ChecklistApp
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
