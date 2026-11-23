# -*- mode: python ; coding: utf-8 -*-
import json
from pathlib import Path

from PyInstaller.utils.hooks.tcl_tk import tcltk_info

root = Path.cwd()
source = root / "src"
modules = json.loads((root / "modules.json").read_text(encoding="utf-8"))
analysis = Analysis(
    [str(source / "app_entry.py")],
    pathex=[str(source)],
    hiddenimports=[Path(name).stem for name in modules if name != "app_entry.py"],
    datas=[(str(root / "Notices"), "Notices")],
)

# The pinned tkinter hook supplies the entire Tcl and Tk script trees, not
# just the initial scripts. Fail if the hook changes or omits either resource.
resources = {name for name, _, _ in tcltk_info.data_files}
collected = {name for name, _, _ in analysis.datas}
if not {"_tcl_data/init.tcl", "_tk_data/tk.tcl"} <= resources <= collected:
    raise RuntimeError("Incomplete Tcl/Tk resource collection")

name = "VehicleInspectionChecklist"
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [],
    exclude_binaries=True, name=name, console=False, target_arch="arm64",
    strip=False, upx=False,
)
distribution = COLLECT(
    executable, analysis.binaries, analysis.datas,
    name=name, strip=False, upx=False,
)
bundle = BUNDLE(
    distribution, name=name + ".app", bundle_identifier="local.kinu.vehicle-inspection-checklist",
    info_plist={
        "CFBundleDisplayName": "Vehicle Inspection Checklist",
        "CFBundleName": "Vehicle Inspection Checklist",
        "CFBundleExecutable": name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "2.1.0",
        "CFBundleVersion": "2.1.0",
        "LSMinimumSystemVersion": "15.5",
        "LSMultipleInstancesProhibited": True,
        "NSHighResolutionCapable": True,
    },
)
