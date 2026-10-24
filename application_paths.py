"""Explicit storage modes; resolving paths never creates files."""
from pathlib import Path
from data_backup import NAMES, RuntimePaths

RUNTIME = "/Library/Frameworks/Python.framework/Versions/3.15/bin/python3"

def runtime_paths(directory):
    return RuntimePaths(*(Path(directory) / name for name in NAMES))

def source_paths(code_file=__file__):
    return runtime_paths(Path(code_file).resolve().parent)

def packaged_paths(home=None):
    home = Path.home() if home is None else Path(home)
    return runtime_paths(home / "Library" / "Application Support" / "Vehicle Inspection Checklist")
