"""Build and inspect a standalone ARM64 macOS app from pinned local inputs."""
import argparse
from contextlib import ExitStack, contextmanager
import ctypes
import fcntl
import importlib.metadata
import json
import marshal
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from types import CodeType
import zipfile

ROOT = Path(__file__).resolve().parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from application_paths import RUNTIME
from app_entry import BundleIdentity, StartupFailure, is_system_image, native_inventory, owned_fd
from build_macos_app import PRODUCTION_FILES


PACKAGING = ROOT / "packaging/macos"
NAME = "VehicleInspectionChecklist"
MACHO = tuple(bytes.fromhex(value) for value in
              ("cffaedfe", "feedfacf", "cafebabe", "bebafeca", "cafebabf", "bfbafeca"))
UPSTREAM_USER_PATH = re.compile(rb"/(?:Users|home)/([A-Za-z0-9_.-]+)")
PROHIBITED = re.compile(
    r"(^|/)(tests?|__pycache__|\.git|\.aws|\.env|backups|exports|logs|credentials)(/|$)"
    r"|\.(json|log|zip\.bak|bak)$", re.IGNORECASE)


def build_requirements():
    lock = PACKAGING / "standalone-build-requirements.txt"
    return {name: version for name, version, _ in re.findall(
        r"^([\w-]+)==([\w.]+) --hash=sha256:([0-9a-f]{64})$",
        lock.read_text(encoding="utf-8"), re.MULTILINE)}


def validate_build_environment():
    if (sys.platform != "darwin" or platform.machine() != "arm64"
            or sys.version_info[:3] != (3, 15, 0)
            or sys.prefix == sys.base_prefix
            or Path(sys.base_prefix).resolve() != Path(RUNTIME).resolve().parent.parent):
        raise ValueError("Build requires a virtual environment based on ARM64 framework Python 3.15.0.")
    requirements = build_requirements()
    if len(requirements) != 6 or any(importlib.metadata.version(name) != version
                                     for name, version in requirements.items()):
        raise ValueError("Install the exact standalone build lock into an isolated virtual environment.")
    import _tkinter

    if (_tkinter.TCL_VERSION, _tkinter.TK_VERSION) != ("9.0", "9.0"):
        raise ValueError("Build requires the framework interpreter with Tcl/Tk 9.")


def copy_notices(destination):
    destination.mkdir()
    shutil.copyfile(PACKAGING / "THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")
    runtime = Path(sys.base_prefix)
    notices = {
        "CPython/LICENSE.txt": runtime / "lib/python3.15/LICENSE.txt",
        "CPython/license.html": runtime / "Resources/English.lproj/Documentation/license.html",
        "Tcl/license.terms": runtime / "Frameworks/Tcl.framework/Resources/license.terms",
        "Tk/license.terms": runtime / "Frameworks/Tk.framework/Resources/license.terms",
    }
    for name, path in notices.items():
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
    for name in build_requirements():
        package = importlib.metadata.distribution(name)
        licenses = [path for path in package.files or ()
                    if ".dist-info/" in path.as_posix()
                    and ("license" in path.as_posix().split(".dist-info/", 1)[1].lower()
                         or path.name.lower().startswith("copying"))]
        if not licenses:
            raise ValueError(f"Missing wheel notices for {name}.")
        for path in licenses:
            relative = path.as_posix().split(".dist-info/", 1)[1]
            output = destination / "Build tools" / name / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package.locate_file(path), output)


def tool(*args):
    return subprocess.check_output(args, text=True)


def load_commands(path):
    details = tool("/usr/bin/otool", "-l", str(path)).splitlines()
    load_types = {"cmd LC_LOAD_DYLIB", "cmd LC_LOAD_WEAK_DYLIB", "cmd LC_REEXPORT_DYLIB",
                  "cmd LC_LOAD_UPWARD_DYLIB", "cmd LC_LAZY_LOAD_DYLIB"}
    dependencies = [details[index + 2].strip().split(" (", 1)[0].removeprefix("name ")
                    for index, line in enumerate(details) if line.strip() in load_types]
    rpaths = [details[index + 2].strip().split(" (", 1)[0].removeprefix("path ")
              for index, line in enumerate(details) if line.strip() == "cmd LC_RPATH"]
    return dependencies, rpaths


def _relative_load_path(path, image, executable):
    for prefix, directory in (("@loader_path", image.parent),
                              ("@executable_path", executable.parent)):
        if path == prefix or path.startswith(prefix + "/"):
            return directory / path.removeprefix(prefix).lstrip("/")
    raise ValueError(f"Unexpected bundle load path: {path}")


def inspect_dependencies(bundle, executable, images, inventory=None):
    inventory = inventory if inventory is not None else {
        image.relative_to(bundle): image.relative_to(bundle) for image in images
    }
    commands = {image: load_commands(image) for image in images}
    executable_rpaths = [_relative_load_path(path, executable, executable)
                         for path in commands[executable][1]]
    for image, (dependencies, rpaths) in commands.items():
        search = [_relative_load_path(path, image, executable) for path in rpaths] + executable_rpaths
        if any(not path.resolve().is_relative_to(bundle) or not path.is_dir() for path in search):
            raise ValueError(f"External or missing rpath in {image.relative_to(bundle)}.")
        for dependency in dependencies:
            if os.path.isabs(dependency) and is_system_image(dependency):
                continue
            if dependency.startswith("@rpath/"):
                candidates = [path / dependency.removeprefix("@rpath/") for path in search]
            else:
                candidates = [_relative_load_path(dependency, image, executable)]
            if not any(_approved_dependency(bundle, path, inventory, commands) for path in candidates):
                raise ValueError(f"Unresolved dependency in {image.relative_to(bundle)}: {dependency}")


def _approved_dependency(bundle, path, inventory, commands):
    advertised = Path(os.path.normpath(path))
    if not advertised.is_relative_to(bundle):
        return False
    physical = inventory.get(advertised.relative_to(bundle))
    return physical is not None and advertised.resolve() == bundle / physical in commands


def _check_code(code):
    if isinstance(code, CodeType):
        if Path(code.co_filename).is_absolute():
            raise ValueError("Frozen Python archive contains an absolute code filename.")
        for value in code.co_consts:
            _check_code(value)


def inspect_python_archive(executable, library, private):
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive(next(key for key in archive.toc if key.endswith(".pyz")))
    expected = {Path(path).stem for path in PRODUCTION_FILES}
    if "app_entry" not in archive.toc or not expected - {"app_entry"} <= pyz.toc.keys():
        raise ValueError("Incomplete application module closure.")

    def check(code):
        _check_code(code)
        data = marshal.dumps(code)
        if any(value in data for value in private):
            raise ValueError("Private build path in frozen Python archive.")

    for name in pyz.toc:
        check(pyz.extract(name))
    for name, (*_, kind) in archive.toc.items():
        if kind in ("s", "m", "M"):
            check(marshal.loads(archive.extract(name)))
    with zipfile.ZipFile(library) as stdlib:
        for name in stdlib.namelist():
            if name.endswith(".pyc"):
                check(marshal.loads(stdlib.read(name)[16:]))


def inspect_bundle(bundle, private_paths):
    contents = bundle / "Contents"
    executable = contents / "MacOS" / NAME
    images = set()
    upstream = set()
    private = tuple(os.fsencode(path) for path in private_paths)
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        if PROHIBITED.search(relative):
            raise ValueError(f"Prohibited artifact: {relative}")
        if path.is_symlink():
            if os.path.isabs(os.readlink(path)) or not path.resolve(strict=True).is_relative_to(bundle):
                raise ValueError(f"External bundle symlink: {relative}")
            continue
        if not path.is_file():
            continue
        data = path.read_bytes()
        if any(value in data for value in private):
            raise ValueError(f"Private build path in {relative}.")
        if data.startswith(MACHO):
            if tool("/usr/bin/lipo", "-archs", str(path)).strip() != "arm64":
                raise ValueError(f"Non-ARM64 binary: {relative}")
            images.add(path)
            upstream.update(match.decode() for match in UPSTREAM_USER_PATH.findall(data))
    if upstream - {"nad"}:
        raise ValueError("Unexpected upstream build-user provenance.")
    with ExitStack() as stack:
        identity = BundleIdentity(bundle, stack)
        info = identity.read_plist()
        identity.stock_resources()
        inventoried = native_inventory(identity, executable.relative_to(bundle))
        if {bundle / path for path in inventoried.values()} != images:
            raise ValueError("Unexpected or missing native image in standalone output.")
        identity.revalidate()
    inspect_dependencies(bundle, executable, images, inventoried)
    resources = contents / "Resources"
    for path in ("_tcl_data/init.tcl", "_tk_data/tk.tcl", "Notices/THIRD_PARTY_NOTICES.md"):
        if not (resources / path).is_file():
            raise ValueError(f"Required resource missing: {path}")
    inspect_python_archive(executable, resources / "base_library.zip", private)
    if (info["CFBundleExecutable"], info["CFBundleShortVersionString"], info["CFBundleVersion"],
            info["LSMinimumSystemVersion"]) != (NAME, "2.1.0", "2.1.0", "15.5"):
        raise ValueError("Unexpected standalone plist metadata.")
    for image in (*sorted(images), bundle):
        signature = subprocess.check_output(
            ["/usr/bin/codesign", "--display", "--verbose=2", str(image)],
            stderr=subprocess.STDOUT, text=True).splitlines()
        if not {"Signature=adhoc", "TeamIdentifier=not set"} <= set(signature):
            raise ValueError("Standalone output must use ad-hoc signatures without a team.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    return upstream


def _output_path(requested):
    requested = Path(requested)
    if (requested.suffix != ".app" or ".." in requested.parts
            or requested.name in (".", "..") or any(ord(char) < 32 for char in requested.name)):
        raise ValueError("Choose a new .app output outside the source checkout.")
    try:
        requested.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("Standalone output already exists.")
    parent = requested.parent.absolute()
    physical = parent.resolve(strict=True)
    # macOS spells its system temporary roots with /var or /tmp as well.
    # Reject any additional alias in caller-controlled parent components.
    if parent.parts[1:2] in (("var",), ("tmp",)):
        parent = Path("/private") / parent.relative_to("/")
    if parent != physical or not physical.is_dir() or physical.is_relative_to(ROOT):
        raise ValueError("Choose an ordinary output parent outside the source checkout.")
    return physical / requested.name


class BuildBoundaryError(RuntimeError):
    """A directory identity changed and automated cleanup would be unsafe."""


class RetainBuildArtifacts(BuildBoundaryError):
    """Rollback could not establish ownership of everything in scratch."""


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _entry_info(directory, name):
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _same_directory(directory, name, expected):
    info = _entry_info(directory, name)
    return (info is not None and stat.S_ISDIR(info.st_mode)
            and os.path.samestat(info, expected))


def _retained_path(expected, parent_fd, parent):
    try:
        held = os.fstat(parent_fd)
        if not parent.is_dir() or not os.path.samestat(parent.lstat(), held):
            # Darwin F_GETPATH returns the current path of the held directory.
            raw = fcntl.fcntl(parent_fd, 50, bytes(1024)).split(b"\0", 1)[0]
            parent = Path(os.fsdecode(raw))
            if not parent.is_absolute() or not os.path.samestat(parent.lstat(), held):
                return "the held directory (its pathname could not be verified)"
    except OSError:
        return "the held directory (its pathname could not be verified)"
    for name in os.listdir(parent_fd):
        if _same_directory(parent_fd, name, expected):
            return str(parent / name)
    return "an unverified location"


def _clear_owned_tree(directory, parent, name, expected):
    if not _same_directory(parent, name, expected):
        raise BuildBoundaryError("Scratch identity changed during cleanup.")
    for child in os.listdir(directory):
        info = _entry_info(directory, child)
        if info is None:
            continue
        if stat.S_ISDIR(info.st_mode):
            with ExitStack() as stack:
                child_fd = owned_fd(stack, child, DIRECTORY_FLAGS, dir_fd=directory)
                if not os.path.samestat(info, os.fstat(child_fd)):
                    raise BuildBoundaryError("Scratch subdirectory changed during cleanup.")
                _clear_owned_tree(child_fd, directory, child, info)
        else:
            # unlinkat does not follow a symlink and will not remove a directory.
            os.unlink(child, dir_fd=directory)
    if not _same_directory(parent, name, expected):
        raise BuildBoundaryError("Scratch identity changed during cleanup.")
    os.rmdir(name, dir_fd=parent)


@contextmanager
def _scratch_directory(parent=Path("/private/tmp")):
    with ExitStack() as stack:
        parent_fd = owned_fd(stack, parent, DIRECTORY_FLAGS)
        work = Path(tempfile.mkdtemp(prefix="checklist-standalone-", dir=parent))
        initial = _entry_info(parent_fd, work.name)
        if initial is None or not stat.S_ISDIR(initial.st_mode):
            raise BuildBoundaryError("New scratch pathname changed before ownership could be established.")
        work_fd = owned_fd(stack, work.name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        if not os.path.samestat(initial, os.fstat(work_fd)):
            raise BuildBoundaryError("New scratch directory changed before ownership could be established.")

        def revalidate():
            if (not os.path.samestat(parent.lstat(), os.fstat(parent_fd))
                    or not _same_directory(parent_fd, work.name, initial)):
                location = _retained_path(initial, parent_fd, parent)
                raise BuildBoundaryError(f"Scratch identity changed. Retained build files: {location}.")

        retain = False
        try:
            yield work, revalidate
        except RetainBuildArtifacts:
            retain = True
            raise
        finally:
            if not retain:
                revalidate()
                try:
                    _clear_owned_tree(work_fd, parent_fd, work.name, initial)
                except BuildBoundaryError as error:
                    location = _retained_path(initial, parent_fd, parent)
                    raise BuildBoundaryError(f"{error} Retained build files: {location}.") from error


def _rename_exclusive(source_fd, source, parent_fd, destination):
    rename = ctypes.CDLL(None, use_errno=True).renameatx_np
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    # Darwin RENAME_EXCL. The kernel also refuses a cross-volume move.
    if rename(source_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 4):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _publish_exclusive(bundle, output, parent_fd, revalidate_parent, expected):
    """Move the original bundle, rolling it back by descriptor on failure."""
    with ExitStack() as stack:
        source_fd = owned_fd(stack, bundle.parent, DIRECTORY_FLAGS)
        bundle_fd = owned_fd(stack, bundle.name, DIRECTORY_FLAGS, dir_fd=source_fd)
        if (not os.path.samestat(os.fstat(bundle_fd), expected)
                or not _same_directory(source_fd, bundle.name, expected)):
            raise BuildBoundaryError("Staging bundle identity changed before publication.")
        revalidate_parent()
        try:
            _rename_exclusive(source_fd, bundle.name, parent_fd, output.name)
            if not _same_directory(parent_fd, output.name, expected):
                raise BuildBoundaryError("Published bundle identity changed.")
            revalidate_parent()
        except BaseException:
            # Inspect both entries even if cancellation arrived immediately
            # after the syscall, before Python could record the move.
            if _same_directory(parent_fd, output.name, expected):
                try:
                    _rename_exclusive(parent_fd, output.name, source_fd, bundle.name)
                except OSError as error:
                    location = _retained_path(expected, parent_fd, output.parent)
                    raise RetainBuildArtifacts(
                        f"Cannot safely roll back. Retained bundle: {location}. Scratch: {bundle.parent.parent}.") from error
            if not _same_directory(source_fd, bundle.name, expected):
                location = _retained_path(expected, parent_fd, output.parent)
                raise RetainBuildArtifacts(
                    f"Cannot prove rollback. Retained bundle: {location}. Scratch: {bundle.parent.parent}.")
            raise


def build_standalone(output):
    validate_build_environment()
    requested_parent = Path(output).parent
    parent_before = requested_parent.lstat()
    output = _output_path(output)
    with ExitStack() as stack:
        parent = BundleIdentity(output.parent, stack)
        parent_fd = parent.directory(Path("."))
        if not os.path.samestat(parent_before, os.fstat(parent_fd)):
            raise ValueError("Output parent changed before the build.")
        work, revalidate_work = stack.enter_context(_scratch_directory())
        source = work / "src"
        source.mkdir()
        for name in PRODUCTION_FILES:
            shutil.copyfile(ROOT / name, source / name)
        (work / "modules.json").write_text(json.dumps(PRODUCTION_FILES), encoding="utf-8")
        copy_notices(work / "Notices")
        home = work / "home"
        home.mkdir()
        env = {
            "HOME": str(home), "CFFIXED_USER_HOME": str(home), "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1", "PYINSTALLER_CONFIG_DIR": str(work / "config"),
        }
        subprocess.run([sys.executable, "-B", "-m", "PyInstaller", "--clean", "--noconfirm",
                        "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
                        str(PACKAGING / "standalone.spec")], cwd=work, env=env, check=True)
        revalidate_work()
        parent.revalidate()
        bundle = work / "dist" / (NAME + ".app")
        stage = BundleIdentity(work, stack)
        staged_bundle = stage.directory(bundle.relative_to(work))
        try:
            upstream = inspect_bundle(bundle, (ROOT, work, Path(sys.prefix), Path.home()))
            if upstream:
                print("Inactive upstream build provenance retained for:", ", ".join(sorted(upstream)))
            _output_path(output)
            revalidate_work()
            stage.revalidate()
            _publish_exclusive(bundle, output, parent_fd, parent.revalidate, os.fstat(staged_bundle))
        except RetainBuildArtifacts:
            raise
        except BaseException:
            revalidate_work()
            try:
                stage.revalidate()
            except (OSError, StartupFailure) as error:
                raise RetainBuildArtifacts(
                    f"Staging identity changed. Retained build files: {work}.") from error
            raise
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(build_standalone(parser.parse_args().output))
