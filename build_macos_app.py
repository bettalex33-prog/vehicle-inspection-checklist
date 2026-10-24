"""Build a local Apple Silicon launcher bundle using only explicit code inputs."""
from pathlib import Path
import argparse
import shutil

PRODUCTION_FILES = (
    "application_paths.py", "app_entry.py", "checklist.py", "gui.py",
    "data_backup.py", "inspection_history.py", "vehicle_profiles.py",
    "inspection_exports.py", "maintenance_schedule.py", "maintenance_gui.py",
    "maintenance_exports.py", "fleet_dashboard.py", "fleet_dashboard_gui.py",
    "fleet_dashboard_exports.py", "maintenance_insights.py", "maintenance_insights_sources.py",
    "maintenance_insights_gui.py", "maintenance_insights_exports.py",
)


def build_app(output, *, source=None):
    source = Path(__file__).resolve().parent if source is None else Path(source).resolve()
    output = Path(output).absolute()
    # Exclusive creation: never replace an existing app or recursively copy user data.
    output.mkdir(parents=True, exist_ok=False)
    contents = output / "Contents"
    code = contents / "Resources" / "app"
    code.mkdir(parents=True)
    executable = contents / "MacOS" / "VehicleInspectionChecklist"
    executable.parent.mkdir()
    for name in PRODUCTION_FILES:
        shutil.copyfile(source / name, code / name)
    shutil.copyfile(source / "packaging/macos/Info.plist", contents / "Info.plist")
    shutil.copyfile(source / "packaging/macos/VehicleInspectionChecklist", executable)
    executable.chmod(0o755)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="dist/Vehicle Inspection Checklist.app")
    build_app(parser.parse_args().output)
