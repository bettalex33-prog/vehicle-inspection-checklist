# Vehicle Maintenance Checklist

A simple command-line tool for tracking vehicle maintenance inspections using only Python standard library modules. No installation or external dependencies required.

## v2.0.0 optional Dashboard

From the graphical checklist, click **Open Dashboard** and enter an evaluation
date (YYYY-MM-DD). The dashboard opens as a child window and starts without
reading the data stores. Click **Refresh** in the dashboard to read the current
checklist, inspection history, vehicle profiles, and maintenance schedule. It
shows shared inspection progress, maintenance status, mileage forecasts, service
costs, recent service history, and data quality. There is no background
monitoring. Missing or invalid sources can prevent a current view, and stale
results are marked as needing refresh.

After the first refresh, use **Vehicle scope** to choose all vehicles or an
existing profile, then refresh to apply the choice. Profiles are identified by
stable IDs, so profiles with the same name remain distinct. To supply current
mileage, select one existing profile, enter a nonnegative whole number in
**Explicit current mileage (session only)**, and refresh. The dashboard applies
that value to the selected profile only. It never infers or saves the value.
Changing vehicle scope clears it, and reopening the dashboard starts a new
session. Inspection progress remains shared across vehicle scopes.

In **Backup health**, **Choose archive…** selects one ZIP for the session.
**Assess selected archive** validates it and compares the archived bytes with
the current stores. This assessment is independent of the dashboard's
**Refresh**. **Clear selection** discards both the selected archive and its
assessment. The backup-health panel and its diagnostics do not display backup
paths. Coverage describes which current store bytes match the archive. It
does not establish data correctness or long-term recoverability.

The dashboard is passive and read-only. It does not create or edit stores,
create backups, extract archives, or export reports. **No restore action
exists in the dashboard.** The established backup and restore controls remain
in the main checklist. Closing the dashboard leaves the checklist running.
Closing the main checklist closes its child windows. A normal packaged Close
returns exit status 0.

### Local macOS package

The v2.0.0 bundle runs natively on Apple Silicon with macOS 15.5 or later and
requires ARM64 Python 3.15 with Tcl/Tk 9 Aqua at the framework path
`/Library/Frameworks/Python.framework/Versions/3.15/bin/python3`.
Build from a trusted local checkout with `python3 -I -B build_macos_app.py`
using that runtime. The builder copies an explicit allowlist of production
modules, the launcher, and the plist. The bundle uses the installed runtime
and the existing separate Application Support store. It remains a local launcher.
The CLI and shell launchers have no separate product-version output.

Python 3.10 remains supported for source execution and tests. GUI modules and
their tests require tkinter. The complete macOS development test matrix also
requires the framework Python 3.15 runtime above, even when Python 3.10 runs
the suite. Only the translated-launch regression test requires Rosetta. The
packaged application runs natively on ARM64 without Rosetta.

Backup manifests retain version 1 and the `1.9.0` compatibility label for newly
created backups. Runtime paths and persistence schemas are unchanged.

See the [v2.0.0 release notes](CHANGELOG.md) for the release inventory and
validation. The historical v1.9.0 notes below preserve the original roadmap.
Native app identity, a custom icon, a bundled runtime, signing, notarization,
and distribution are outside this release.

## v2.1.0 standalone macOS development build

The standalone builder is separate from the v2.0 framework launcher. It
produces an ARM64 one-directory app for Apple Silicon on macOS 15.5 or later.
The standalone app carries Python, Tcl/Tk Aqua, and its other native runtime
libraries. It uses the same packaged Application Support directory and
persistence formats as the framework launcher.

Build from a trusted checkout using the Python.org ARM64 framework
Python 3.15.0 installation and macOS command-line developer tools. Create
the build environment and output in a disposable directory outside the checkout.

```bash
scratch="$(mktemp -d)"
runtime=/Library/Frameworks/Python.framework/Versions/3.15/bin/python3
lock=packaging/macos/standalone-build-requirements.txt

/usr/bin/arch -arm64 "$runtime" -I -B -m venv "$scratch/venv"
"$scratch/venv/bin/python" -I -B -m pip --isolated --no-cache-dir download \
  --index-url https://pypi.org/simple --only-binary=:all: --require-hashes \
  --dest "$scratch/wheels" -r "$lock"
"$scratch/venv/bin/python" -I -B -m pip --isolated install \
  --no-index --find-links "$scratch/wheels" --require-hashes -r "$lock"
"$scratch/venv/bin/python" -I -B build_macos_standalone.py "$scratch/VehicleInspectionChecklist.app"
```

The builder does not open the application. It checks that native load paths
resolve within the app or to macOS system libraries, rejects local build paths
and absolute Python code filenames, and includes the wheel and runtime notices
under `Contents/Resources/Notices`. It reports recognized upstream build
provenance strings separately. PyInstaller applies the ad-hoc signature needed
for local ARM64 execution. Developer ID signing and notarization are separate
distribution steps.

PyInstaller reads `base_library.zip` before Python can validate the bundle.
The runtime checks control which paths the initialized application accepts.
Pre-interpreter integrity depends on trusting the finished bundle and its
signature. Ad-hoc signing is for local development. A distributed bundle
requires Developer ID signing and notarization.

Use a trusted local checkout, pinned toolchain, and build directories that
other processes do not modify. The builder checks for directory replacement
and leaves artifacts in place when it cannot prove cleanup or rollback is
safe. PyInstaller runs in a child process. Publication uses an exclusive
same-volume rename, with descriptor-relative rollback until verification
finishes. After a verified publication, the completed output is retained.

Registered descriptors are closed on ordinary exceptions and on cancellation
after ownership registration and before cleanup begins. Normal builder
failures clean up unchanged owned scratch directories. Process termination
closes the process's descriptors. The contract excludes an interruption
between a native open returning and Python registering ownership, interruption
inside native close, repeated interruption during cleanup, and arbitrary
trace-injected exceptions at every bytecode boundary. Close is never retried
on a descriptor that may already have been reused.

For local validation, use synthetic stores under an isolated HOME. Compare two
clean builds by path, file hash, permissions, and symlink target. Inspect the
running app after Dashboard Refresh to verify the loaded Python, Tcl, Tk, and
other native libraries resolve to the app or macOS system paths.

## Fleet Dashboard and Maintenance Alerts (v1.7.0)

Open **Fleet Dashboard…** from the checklist. The dashboard is read-only: it does
not record services, update profiles, alter inspections, or change checklist
completion. Alerts are local visual indicators; no background processes,
notifications, or persistent dashboard settings are added.

Fleet totals include every task exactly once as **Overdue**, **Due**, **Due soon**,
**Upcoming**, **Mileage needed**, **Disabled**, or **Archived**. Archived takes
precedence over Disabled. For enabled tasks, an overdue interval takes precedence
over a due interval, which takes precedence over a warning interval. An unknown
mileage interval is marked Mileage needed unless its date interval already
produces Overdue, Due, or Due soon. Separate mileage-needed indicators remain
visible even when the date status takes precedence. Existing task intervals and
warning thresholds are unchanged.

The default evaluates dates for the whole fleet. To evaluate mileage, select one
existing profile, explicitly enter **Current mileage**, and click **Evaluate /
Refresh**. The value is never prefilled or inferred from a profile, inspection,
service record, or captured snapshot. It is never saved or written back to the
profile. Selecting a different profile clears it. Mileage below an enabled task's
latest service/baseline mileage is rejected. Disabled and archived tasks do not
consume evaluation mileage. Missing-profile tasks remain visible using captured
identity, but cannot receive current mileage. Restarting or restoring a backup
clears transient mileage evaluation.

Totals and per-vehicle summaries always cover the fleet. The selected profile
filters the task and appointment tables and receives explicit mileage evaluation;
the status selector filters only the task table. Vehicle groups use stable profile
IDs, never display names. Existing vehicles with no tasks are included, and
missing profiles retain their own groups. Captured snapshot variants remain
available in complete task details.

Appointment groups are mutually exclusive, relative to the evaluation date:

| Group | Calendar-day offset |
| --- | --- |
| Passed | Before today |
| 0–7 days | Today through day 7, inclusive |
| 8–30 days | Days 8 through 30, inclusive |
| 31–90 days | Days 31 through 90, inclusive |

Only enabled, nonarchived tasks enter appointment groups. Appointments beyond
90 days, disabled appointments, and archived appointments remain visible in task
details. An appointment never changes the task's due status.

Cost summaries cover the fleet, each vehicle, and each task, including disabled,
archived, and missing-profile tasks. Optional **Cost start** and **Cost end** use
YYYY-MM-DD and inclusive service completion dates; blank bounds are unbounded.
The start cannot follow the end. Costs use integer cents. Explicit zero is priced;
unpriced services and legacy services are counted separately. With no matching
priced services the total is **Not recorded**, rather than an invented $0.00.
Date ranges affect costs only, never status totals, appointments, or full history.

Select a task or appointment and click **Open Task / Full History**, or double-click
it, to navigate to the existing maintenance view. Navigation reloads by stable
task ID, opens the correct Active/Archived and profile view, and shows all captured
details and services. It does not transfer evaluation mileage. If source data has
changed, refresh the dashboard before navigating or exporting. Invalid data or
evaluation inputs make the dashboard unavailable; editing inputs marks displayed
results as needing refresh and disables export/navigation.

**Export CSV** and **Export HTML** save separate dashboard reports. Choose an
existing output directory; the default is `exports/`. Existing destinations require
explicit overwrite confirmation. CSV uses a fixed header, UTF-8 BOM, CRLF, proper
quoting, and spreadsheet-formula protection. Its `row_type` values are
`status_total`, `vehicle_summary`, `task`, `appointment`, `cost_overall`,
`cost_vehicle`, and `cost_task`. Rows include evaluation context, stable IDs,
status counts, due values, appointment buckets, and exact cents plus displayed
costs. Nonapplicable values are blank; unrecorded totals have blank cents and
Not recorded display text. Dashboard exports always cover the whole fleet and
record the selected profile used for mileage; visual table filters do not omit data.

HTML includes equivalent summary information and internal links to full task
details, captured profile snapshots, and **unfiltered** priced/unpriced/legacy
service history. Text is escaped; reports require no scripts or external assets.
Existing maintenance-history and inspection exports retain their original behavior.

Exports are published atomically from same-directory temporary files, which are
cleaned up on failure. Runtime/source aliases, symlinks, hard links, and custom
runtime paths are protected. Sources are revalidated immediately before publication;
an output changed during confirmation or preparation is not overwritten. Dashboard
source reads are bounded to 16 MiB; existing outputs are fingerprinted in chunks.
These checks detect stale data
but are not a cross-process locking transaction; use one writer at a time.

New backups use release **1.7.0**, manifest version 1, and the same four runtime
members. Restore continues to accept v1.4.0, v1.5.0, and v1.6.0 backups; maintenance
schemas 1 and 2 remain supported with their original release restrictions. Missing
backup members leave corresponding runtime files untouched. No dashboard inputs
or reports enter backups. Older app versions may reject a 1.7.0 manifest.

## Requirements

No external Python dependencies. The GUI requires a Python installation with tkinter support.

## Installation

No installation needed. Simply place this project in your Python environment and run directly.

## Usage Examples

### Open the graphical checklist on macOS

Double-click `launch_checklist.command` in Finder to open the GUI. If macOS blocks
the first launch, use **Control-click > Open** on the launcher. A Terminal window
may remain visible while the GUI runs.

You can also launch it from Terminal:

```bash
python3 gui.py
```

The GUI requires Python with tkinter support and uses only the Python standard
library. Click an unchecked inspection item to mark it complete. Completed items
remain checked and disabled until you use **Reset All** and confirm the dialog.
**Refresh** reloads changes made through the CLI, and **Close** exits the window.
Both interfaces share the same `checklist_data.json` beside the scripts.

### Save and review inspections (v1.1.0)

Enter a **Vehicle name / unit number**, **Mileage**, and optional multiline
**Notes**, then click **Save Inspection**. The vehicle name must not be blank.
Mileage must contain only digits for a nonnegative whole number; surrounding
whitespace and leading zeros are accepted. Signs, decimals, and separators are
rejected.

You can save at any progress level, including 0/7. Each save creates a separate
snapshot containing a timezone-aware UTC ISO 8601 timestamp, vehicle details,
all seven item names and completion statuses, and overall progress. Saving
retains your checklist and form entries; it never resets the checklist.
Form entries persist across restarts only within saved inspection records.

Click **History** to view saved inspections, newest first. Select a row to see
notes and individual item statuses. **Refresh** reloads history from disk.
History is read-only; **Reset All** affects only the current checklist.

History is stored separately in `inspection_history.json` beside the scripts,
using a versioned JSON format. Existing CLI commands and `checklist_data.json`
remain unchanged. The history file is created on the first successful save and
is ignored by Git; back it up separately along with your checklist data.

History writes use an atomic temporary-file replacement. Invalid, corrupt, or
unsupported history produces an error and is never replaced with empty history.
Keep the original file when investigating an error. Use one app instance at a
time: simultaneous saves from multiple instances can overwrite each other's
updates. A save snapshots the persisted checklist, including prior CLI changes;
avoid changing it through the CLI during a save.

### Inspection findings (v1.2.0)

Click **Edit Findings…** to open a compact editor. Select any inspection item,
including a completed item, and choose **OK**, **Needs attention**, or
**Not inspected**. Each item also accepts an optional multiline comment.
Switching items retains edits within the editor. **Apply** updates the current
form draft; **Cancel** or closing the editor discards that editor session.
New drafts start with Not inspected and empty comments for all seven items.

Before saving, explicitly choose an **Overall assessment**: **Ready for service**,
**Service recommended**, or **Do not operate**. This is a user-selected record,
not an automated mechanical or safety diagnosis. The application never infers
conditions or assessment from completion and does not enforce relationships
between those values. Completion and progress continue to work exactly as before.

Vehicle details, findings, and assessment are the **current draft**. Saving retains
all draft values and checklist completion. Refresh reloads completion only;
Reset All clears completion only. Review retained values before saving another
inspection. Draft form values are not restored after closing the application;
they persist only inside successfully saved inspection records.

History details show every item's completion status, condition, and comment,
plus the user-selected assessment. The details area scrolls for long comments.
Legacy records display **Not recorded (legacy inspection)** for absent findings
and assessment; no historical values are inferred.

Version-1 history remains readable without changing its file. Only a successful
new inspection append upgrades the history envelope to **schema_version 2**.
Existing records retain their exact JSON content (file whitespace may change).
Version 2 supports both legacy records and complete new records containing all
seven findings plus assessment. Partial, corrupt, malformed, duplicate-key, and
unsupported history is rejected without overwriting the original file. Failed
saves leave the previous file intact. Checklist data retains its original format;
findings and assessment are stored exclusively in inspection history.

Back up history before the first v1.2.0 save: v1.1.1 and earlier cannot read
schema-version-2 history and will reject it. There is no automatic downgrade.
The existing single-instance recommendation still applies.

### Vehicle profiles (v1.3.0)

The optional **Vehicle profile** selector starts at **Manual entry**. You can
always enter an inspection vehicle name and mileage without creating a profile.
Click **New Profile…** to save a vehicle name / unit number, optional year, make,
and model, and required current mileage. Name is trimmed and required. Year is
blank or a nonnegative whole number; mileage is a required nonnegative whole
number. Numeric fields accept ASCII digits, surrounding whitespace, and leading
zeros, but reject signs, decimals, separators, and exponents. Make and model are
optional text; surrounding whitespace is trimmed.

Selecting a saved profile fills inspection name and mileage. Both entries remain
editable: they are the actual values saved for that inspection. Saving an
inspection does **not** update profile mileage or any other profile detail.
Use **Edit Profile…** to explicitly update a selected profile; its generated UUID
remains stable even when its name changes. Successful creation or editing selects
the saved profile and fills inspection name and mileage. Duplicate names are
allowed; the selector includes each full profile ID to distinguish them.

Choose **Manual entry** to clear the association while retaining entered values.
**Delete Profile…** requires confirmation. Successful deletion clears the active
association and retains current inspection form values. Cancelled or failed
operations retain the draft. Profile actions do not clear notes, findings,
assessment, or checklist completion. The checklist is shared across vehicles;
profiles do not maintain separate completion state.

**Reload Profiles** reloads available choices without replacing the current
inspection draft or its selected snapshot. Reselect a profile to use its latest
details. If the selected profile has been removed, reloading clears its association
but retains form values. If profile loading fails, the app reports the error,
clears the association, and disables profile actions until a successful reload;
manual inspections remain available. Existing **Refresh** and **Reset All** still
affect completion only. Profile selection and inspection drafts are not restored
when the application restarts.

Profiles persist separately beside the scripts in `vehicle_profiles.json`, using
`{"schema_version": 1, "profiles": [...]}`. Each profile has exactly `profile_id`,
`vehicle_name`, `year` (integer or null), `make`, `model`, and `mileage` (integer).
Reading a missing file does not create it; the first successful profile creation
does. Writes use a flushed, fsynced temporary file in the same directory and atomic
replacement. Corrupt, malformed, partially valid, duplicate-key, duplicate-ID, or
unsupported data raises an error and is never overwritten. Keep the original
file when investigating errors. Use one app instance at a time: atomic replacement
does not prevent concurrent instances from overwriting each other's updates.

Profile-backed inspections preserve a `profile_id` and independent
`profile_snapshot` containing name, year, make, model, and mileage from selection
or the last successful profile edit in the current session. Top-level inspection
name and mileage remain the actual form values and may differ from the snapshot.
History displays both. Editing or deleting a profile never changes saved records;
history does not need the profile store to display them.

History schemas 1 and 2 remain readable without any write or record changes.
Only a successful profile-backed append upgrades the history envelope to
**schema_version 3**. Schema 3 accepts unchanged legacy records, findings records,
and profile-backed findings records; the latter require both profile fields and
all findings and assessment. Existing record values and keys are preserved during
upgrades, although JSON formatting may change. Manual saves keep the existing
schema-1-to-2 behavior and never downgrade schema 3. Failed saves preserve the
original file. Corrupt, malformed, partial, duplicate-key, or unsupported history
is rejected without overwriting it. Checklist persistence and CLI behavior are
unchanged. No historical vehicles are automatically converted to profiles.

Back up `checklist_data.json`, `inspection_history.json`, and
`vehicle_profiles.json` separately; all are ignored by Git. Back up history before
the first profile-backed save: v1.2.0 cannot read schema-3 history, and earlier
releases have their existing compatibility limits. There is no automatic downgrade.
Profile changes and inspection saves are separate operations; saving an inspection
writes history only.

Progress appears in this format:

```text
Progress: [██░░░░░] 2/7 (29%)
```

### Run the list command (show all items with status)

```bash
python3 checklist.py list
```

**Output:**
```
ID
Name
Status
battery
Battery Health & Terminals
□
brakes
Brake condition.
□
coolant
Coolant Reservoir Level
□
leaks
Fluid Leaks Under Vehicle
□
lights
All Light Functions
□
oil
Oil & Fluid Levels
□
tires
Tire Pressure & Wear
□
```

### Run the complete command (mark an item as complete)

```bash
python3 checklist.py complete oil
```

**Output:**
```
oil marked complete. Progress: [█░░░░░░]
```

Available item IDs: `oil`, `coolant`, `tires`, `brakes`, `lights`, `battery`, `leaks`

### Run the reset command (clear all items)

```bash
python3 checklist.py reset
```

**Output:**
```
All items cleared. Progress reset to 0/7 (0%)
```

### Run the progress command (show completion percentage with bar chart)

```bash
python3 checklist.py progress
```

**Output:**
```
Progress: [░░░░░░░] 0/7 (0%)
```

### Complete all items and show progress

```bash
python3 checklist.py complete oil
python3 checklist.py complete coolant
python3 checklist.py complete tires
python3 checklist.py complete brakes
python3 checklist.py complete lights
python3 checklist.py complete battery
python3 checklist.py complete leaks
```

**Progress output after:**
```
Progress: [███████] 7/7 (100%)
```

## Running Tests

Run the built-in unit tests using Python's unittest module:

Tests use temporary directories and injected paths for checklist, history, and profile data. GUI tests mock
widgets and dialogs without opening windows; history persistence has no tkinter
dependency and is tested separately.

```bash
python3 -m unittest discover -s tests -v
```

The test count and timings depend on the checkout. A successful run ends with
`OK`.

## Data Persistence

### How it works

- The tool stores completion status in `checklist_data.json` located in the same directory as `checklist.py`
- When you run the checklist for the first time, it auto-creates `checklist_data.json` with all items set to not complete (□)
- Data persists across runs; your progress is saved when you mark items complete

### Viewing data file

```bash
cat checklist_data.json
```

### Editing data manually

You can edit `checklist_data.json` as a standard JSON file:

```json
{
  "oil": {
    "name": "Oil & Fluid Levels",
    "complete": true
  },
  "brakes": {
    "name": "Brake condition.",
    "complete": false
  }
}
```

**Warning**: Manual editing may cause issues if you lose track of item IDs. Use the CLI commands when possible.

## Items Tracked

| Stable ID | Item Name |
|-----------|-----------|
| `oil` | Oil & Fluid Levels |
| `coolant` | Coolant Reservoir Level |
| `tires` | Tire Pressure & Wear |
| `brakes` | Brake condition. |
| `lights` | All Light Functions |
| `battery` | Battery Health & Terminals |
| `leaks` | Fluid Leaks Under Vehicle |

## Commands Reference

| Command | Description |
|---------|-------------|
| `python3 checklist.py list` | Show all items with current status (□ or ✓) |
| `python3 checklist.py complete ITEM_ID` | Mark an item as complete (idempotent operation) |
| `python3 checklist.py reset` | Clear all items to not complete |
| `python3 checklist.py progress` | Show overall completion percentage with bar chart |

## Running Tests Directly

For detailed output:

```bash
python3 -m unittest tests.test_checklist
```

## License

This project is provided as-is for personal vehicle maintenance tracking.

---

*Zero external dependencies. Just works.*

### Reports, exports, backup, and restore (v1.4.0)

In **History**, select a saved row and choose **Export Selected Report** for a
standalone printable HTML document. Open it yourself in a browser and use Print;
the app never opens a browser. The report has inline print styles, no scripts or
remote resources, and escapes every dynamic HTML value. It includes actual saved
vehicle name and mileage, UTC timestamp, progress, checklist statuses, all
findings/comments, assessment, notes, and the independent saved profile snapshot
when present. Snapshot mileage is separate from actual inspection mileage.
Legacy findings and assessment say **Not recorded (legacy inspection)**. Absent
profile metadata says **Profile association/snapshot not recorded**; it does not
infer how an older inspection was entered. Blank recorded comments remain blank.
Current profiles are never consulted to reconstruct a historical report.

**Export History CSV** exports one row per saved inspection, including duplicates,
in persisted history order. A missing history exports just the header without
creating history. The format is UTF-8 with BOM, comma-separated, CRLF-terminated,
and uses CSV quoting for commas, quotes, and multiline text. Columns are:

```text
history_schema_version,history_record_number,timestamp,vehicle_name,mileage,
completed,total,percentage,findings_recorded,assessment,notes,profile_recorded,
profile_id,profile_vehicle_name,profile_year,profile_make,profile_model,profile_mileage
```

These are followed by `<id>_name`, `<id>_complete`, `<id>_condition`, and
`<id>_comment` for oil, coolant, tires, brakes, lights, battery, and leaks, in that
order. Flags use `true`/`false`. Legacy findings use the explicit legacy label;
missing profile fields are empty with `profile_recorded=false`. The record number
is the one-based position in this export, not a permanent inspection ID.
Formula-like text beginning with `=`, `+`, `-`, or `@` after leading whitespace/control characters
receives a leading apostrophe to reduce spreadsheet formula execution risk.
This changes exported text only; it never changes stored JSON. Spreadsheet
programs differ in their handling of that apostrophe. Numeric columns stay numeric. NUL characters are exported as the visible text
`\u0000` because Python 3.10 CSV cannot represent them; stored text remains unchanged.

Save dialogs suggest the project-local `exports/` directory. Create it in the
save dialog if desired, or choose another existing directory. Existing outputs
require an explicit overwrite confirmation; cancelled exports do nothing.
Exports may not overwrite or alias runtime data. Exports and their tests do not
modify history or profiles.

**Backup All Data** asks for an existing destination directory (suggesting
`backups/`) and creates `vehicle-checklist-backup-<UTC timestamp>.zip`, with
microsecond precision. Name collisions fail rather than overwriting an archive.
Only existing `checklist_data.json`, `inspection_history.json`, and
`vehicle_profiles.json` files are included, alongside `manifest.json`. Missing
files are not created; a backup with no runtime files is rejected. Source code,
caches, other exports, and unrelated files are never included. Invalid existing
runtime data causes backup to fail rather than silently omitting it.

The manifest has exactly `manifest_version` (integer 1), `application`
(`vehicle-maintenance-checklist`), `release` (`1.4.0`), `created_at` (UTC ISO 8601),
and `files`. Each sorted file entry has exactly `filename`, `size` (byte count),
and `sha256` (lowercase hexadecimal digest). Runtime contents are archived as
exact bytes. SHA-256 detects accidental changes; it does not authenticate who
created a backup. Archives are not encrypted and may contain sensitive notes.

**Restore Backup** validates the entire archive before requesting confirmation.
The warning lists files to replace/create and omitted files to retain. Make a
current backup first: included stores are replaced, not merged. **An omitted
runtime file always leaves the current file untouched; omission never deletes.**
Cancel performs no writes. Restoring preserves original JSON bytes and history
schemas 1, 2, and 3 without migrations, normalization, or new inspection records.
Profiles use their existing schema 1; checklist data uses its existing seven-item
structure. No matching current profile is required for a saved profile snapshot.

Validation rejects unknown/unsafe names, directories, duplicate ZIP members,
symlinks/special members, encrypted members, unsupported compression (only stored
and deflated are accepted), corrupt ZIPs/CRCs, malformed or duplicate-key JSON,
nonstandard numeric constants, invalid persistence data, malformed manifests,
unsupported manifest versions, duplicate manifest entries, unlisted or missing
members, size discrepancies, and SHA-256 mismatches. Limits are 64 MiB per ZIP,
64 KiB for the manifest, 16 MiB per runtime file, and 48 MiB total runtime bytes,
with at most four members. Both declared sizes and bounded actual reads are
checked. Backup creation applies the same runtime limits. Valid larger datasets
need a future explicit limit change; they are not truncated.

Checklist backup/restore validation requires exactly the seven known IDs, each
with a string name and boolean completion. This is stricter than the original
permissive CLI JSON loader; CLI behavior itself is unchanged. Runtime destinations
must be distinct regular files or absent. Symlink leaves, special files, hard-link
aliases, resolved-path aliases, and aliases of the selected output/archive are
rejected. Parent path aliases such as macOS `/var` are resolved for comparisons.

Restore revalidates every payload and destination, fingerprints existing files,
stages all replacements and rollback copies in the target directories, flushes
and fsyncs those files, and checks fingerprints again before replacing anything.
Replacement uses `os.replace()`; affected parent directories are fsynced where
supported. A replacement failure triggers rollback, including removing newly
created targets that were originally absent. Successfully rolled-back failures
preserve original bytes. If rollback cannot finish, the error clearly reports
all retained `.recovery` files and originally absent targets requiring attention.
Keep those recovery files until every affected store is recovered; they contain
the original bytes and must not be committed. Do not treat that error as success.

**Atomic replacement applies to one file at a time.** Power loss, process death,
disk failure, or failed rollback can leave multiple stores at different points
in the restore. There is no true multi-file atomic guarantee or automatic crash
recovery. Do not run another GUI instance or CLI writer during backup/restore:
fingerprints detect changes during preparation, but cannot eliminate a race
between the final check and replacement. Backup reads stores sequentially and
cannot promise a single simultaneous snapshot of concurrent writes.

A successful restore refreshes checklist completion, profile choices, and open
history views. Vehicle fields, notes, findings, and assessment drafts remain.
Removed profile associations clear according to existing reload behavior.
Refreshing an absent checklist after restore shows defaults without creating a
file. Review retained draft values before saving another inspection.

`/exports/` and `/backups/` are ignored by Git. Outputs saved elsewhere in the
project are not covered by those directory rules: do not stage them. All filesystem
tests use injected paths under `TemporaryDirectory`; GUI tests mock widgets,
windows, and dialogs and never open exported documents.

### Maintenance Due and Service Scheduling (v1.5.0)

Open **Maintenance…** from the checklist. This feature uses only the Python
standard library and stores data separately in `/maintenance_schedule.json`,
which Git ignores. Viewing a missing schedule does not create it. Checklist CLI
commands and existing inspection, profile, HTML, and CSV formats are unchanged.
There is no migration or automatic conversion of inspection findings to tasks.

Select a saved profile, then **New Task…**. Enter a title, notes, an enabled state,
and an interval in days, miles, or both. Leave unused intervals and their warning
fields blank. Warnings are nonnegative and no larger than the matching interval;
30 days and 500 miles are typical choices. The new-task form starts with a
180-day interval and a 30-day warning. Baseline date/mileage represent the last
service or an explicitly chosen starting point. A date baseline is required for
a date interval; a mileage baseline is required for a mileage interval. An unused
axis may have a blank baseline. Days are elapsed calendar days, not months.

Each task captures a separate, immutable snapshot of its profile at creation.
The table identifies vehicles using that snapshot. Editing or deleting a profile
never changes the snapshot, task, or service history. **Missing profiles** shows
orphaned tasks; these remain viewable and editable, but cannot record new service
until their original profile ID exists again. A recreated profile with the same
name has a different ID and does not reconnect a task.

**Evaluate / Refresh** reloads saved tasks and profiles. Select a profile and
explicitly enter current mileage for the evaluation. Selecting a profile clears
this input; profile mileage and inspection records never prefill it. This input
never updates a profile or inspection. Mileage cannot be below a task's latest
service anchor. **All profiles** evaluates dates and marks
applicable mileage checks as unknown; it does not combine mileage across
vehicles or infer current mileage from inspection records.

The last recorded service anchors the next due date and mileage. Before any
service, the baseline anchors them. Due date is anchor date plus interval days;
due mileage is anchor mileage plus interval miles. Either axis can trigger:

| Status | Meaning |
| --- | --- |
| Overdue | At least one threshold has been exceeded |
| Due | At least one threshold equals today/current mileage |
| Due soon | Within either warning distance, with no Due or Overdue result |
| Upcoming | All applicable checks are known and beyond warning distances |
| Mileage needed | Mileage is unknown and no urgent date result applies |
| Disabled | Task is disabled |
| Archived | Task is archived and disabled |

Details show both axis assessments, remaining days/miles, the mileage-unknown
flag, baseline, intervals, appointment, snapshot, and complete service history.
Dates use the machine's local calendar date. New dates use `YYYY-MM-DD`.
An optional appointment is independent of due status: scheduling does not reset
thresholds. Past appointments remain visible and are marked as passed.

**Record Service…** requires completion date, mileage, and optional notes.
Confirming appends a service with a unique ID, advances both applicable
thresholds, and clears the appointment in one schedule write. Service dates may
not be in the future. Dates and mileage must be nondecreasing from the baseline
through all services; equal values are allowed. Baselines become read-only once
service is recorded. If a previously unused axis has no baseline, that interval
cannot be added after service has been recorded. Task title, notes, intervals,
warnings, enabled state, and appointment can otherwise be edited.

**Delete / Archive…** asks for confirmation. A task without services can be
permanently deleted. A task with any services can only be archived and disabled;
its entire history and snapshot remain. The default **Active** view excludes
archived tasks. Select **Archived** to inspect them. Archived tasks with services
are read-only and cannot be deleted through the application. Disabling an active
task is reversible by editing it; archival has no reactivation control in v1.5.0.

Task titles are trimmed, nonempty, and limited to 200 characters. Task/service
notes are limited to 10,000 characters. These text fields reject NUL. Mileage and
warnings are whole nonnegative numbers, intervals are positive, and numeric
booleans are rejected. IDs are canonical UUID strings. Saved objects require
exact fields, unique task/service IDs, valid normalized profile snapshots, and
schema version 1. Malformed UTF-8, duplicate JSON keys, nonfinite constants,
unsupported versions, impossible dates, and date arithmetic overflow are
rejected without repairing or overwriting the data. Corrupt maintenance data
disables maintenance actions while existing checklist/inspection actions remain
available. Correct the file or restore a valid backup, then refresh.

The exact schedule shape is illustrated below. All keys are required; unused
values are JSON `null`. `profile_snapshot` contains the five profile detail
fields, with its ID stored separately. Calculated due results are never saved.

```json
{
  "schema_version": 1,
  "tasks": [
    {
      "task_id": "72a7f52a-a464-4471-bc43-998ffea1dcc2",
      "profile_id": "f70f8899-4da5-4636-9660-0edf9c1d094a",
      "profile_snapshot": {
        "vehicle_name": "Unit 12",
        "year": null,
        "make": "",
        "model": "",
        "mileage": 42000
      },
      "title": "Oil and filter",
      "notes": "",
      "enabled": true,
      "archived": false,
      "interval_days": 180,
      "interval_miles": 5000,
      "warning_days": 30,
      "warning_miles": 500,
      "baseline_date": "2026-04-25",
      "baseline_mileage": 42000,
      "scheduled_date": null,
      "services": [
        {
          "service_id": "5f5305c8-6236-465f-aa92-edf984e369de",
          "completed_date": "2026-10-22",
          "mileage": 46500,
          "notes": "Oil and filter replaced"
        }
      ]
    }
  ]
}
```

**Backup compatibility:** new backups use manifest version 1 and release 1.5.0,
with up to four runtime stores plus the manifest. Existing 1.4.0 backups still
restore; their omitted maintenance store is left untouched. A maintenance-only
backup is valid, including tasks whose profiles are absent. New backups preserve
all source bytes and snapshots. The previous limits remain: 16 MiB per runtime
store, 48 MiB combined, 64 MiB archive, and 64 KiB manifest. v1.4.0 applications
cannot read v1.5.0 backups. Output alias checks include maintenance data, including
custom GUI paths, so inspection exports cannot overwrite it.

Schedule edits validate first, write a same-directory temporary file, flush and
fsync it, check for changed original bytes, then publish with `os.replace`.
Temporary files are cleaned after success or failure; a failed write preserves
the original store. Symlink destinations, hard-linked schedule stores, special
files, and aliases to other runtime files are rejected. Stale selected-task
edits are refused; changes to unrelated tasks are retained. These checks reduce
conflicts but do not eliminate the final race with another process: use one
writer at a time. Restore retains the existing per-file atomic replacement and
rollback/recovery behavior, rather than a multi-file power-loss guarantee.
Open maintenance views refresh after successful restore. Unsaved editor text is
retained; saves against changed tasks require refreshing and reopening the editor.

Maintenance tests use disposable paths, injected dates, mocked Tk widgets and
dialogs, and no display server. CLI subprocess tests operate on a temporary copy
of the original CLI. The backup tests cover all 15 nonempty runtime-store subsets
and failure/rollback paths across all four destinations.

### Service work orders, costs, and maintenance exports (v1.6.0)

**Record Service…** now accepts a service provider and invoice/reference number,
plus labor, parts, and tax in dollars. Provider and reference are optional text,
trimmed and limited to 200 characters. The confirmation shows the calculated
total before saving. Invalid input keeps the dialog, draft, and saved data intact.
Selected-task details show every work order and its itemized costs.

Enter plain nonnegative dollar amounts, such as `12`, `12.5`, or `12.50`.
Surrounding whitespace is accepted. Signs, currency symbols, commas, exponents,
leading zeros such as `012.50`, missing integer digits such as `.50`, trailing
points, non-ASCII digits, NaN, infinity, and more than two decimal places are
rejected. Values are never rounded or represented as binary floating point.
They are stored as integer cents: `labor_cost_cents`, `parts_cost_cents`,
`tax_cents`, and `total_cost_cents`. Reports and details format recorded values
consistently as `$12.50`.

**Blank costs mean “Not recorded”, not zero.** Blank components are JSON `null`;
explicit `0.00` is the recorded integer `0` and displays `$0.00`. The total is
`null` when every component is unrecorded. Otherwise it is the sum of recorded
components, using zero for omitted components only for this calculation. Those
components remain visibly unrecorded. Saving calculates the total internally;
persisted totals must match the components. Provider and invoice/reference are
stored as `provider` and `invoice_reference`, each text or `null`.

Historical service records are immutable through the application. Editing a
task or profile, deleting a profile, or archiving a task never changes existing
work orders, costs, or captured vehicle snapshots. Tasks with any service history
remain protected from permanent deletion. Service recording still advances the
applicable due date/mileage and clears the appointment. **Recorded service
mileage never updates a vehicle profile automatically.** Current evaluation
mileage is still entered explicitly and never inferred from service history,
inspection records, or profile mileage.

The v1.5.0 schema 1 format described above remains readable. Reads, viewing,
summaries, and exports never rewrite or upgrade a store. Original four-field
legacy service objects retain every original key and value; missing provider,
reference, and cost fields display **Not recorded**. No values are inferred.
A successful maintenance write publishes schema 2, which permits both unchanged
legacy records and complete new work-order records. Failed writes leave the
existing bytes and schema intact. Partially populated work-order objects,
inconsistent totals, duplicate keys, unsupported schemas, stale writes, and
corrupt data are rejected. v1.5.0 cannot read schema 2 maintenance stores.

**Export All History CSV** and **Export All History HTML** export the entire saved
maintenance store, including archived and missing-profile tasks, regardless of
the current table filters. The selected profile's explicitly entered evaluation
mileage is used only for that profile; other mileage checks remain unknown.
Exports include the evaluation date, vehicle snapshot, task configuration and
state, both due assessments, appointments, service IDs, work-order details,
itemized costs, totals, and unpriced-service counts. An invalid evaluation
mileage prevents export so it cannot produce a misleading report.

CSV has one row per service; tasks without services have a row marked
`service_recorded=false`. Task fields and `task_*` summaries repeat on service
rows: do not sum those repeated task totals. Sum `service_total_cost_cents`
values once per service instead. Despite the storage-oriented column names,
exported cost cells contain formatted dollars or `Not recorded`. Text cells
with spreadsheet formula prefixes are escaped, including prefixes hidden by
leading whitespace/control characters. HTML is a self-contained, escaped report
with overall and per-snapshot historical cost summaries. Ordering is stable by
profile/task ID and service date, mileage, and ID; a fixed evaluation date and
mileage input produce deterministic output. No browser or document is opened.

Exports require confirmation to replace an existing file and use a temporary
file in the destination directory, flush/fsync, and atomic publication. Failed
validation or writes before publication preserve the previous destination.
Source aliases, symlinks, hard-link aliases to protected runtime stores, and
custom supplied runtime paths are rejected. Exports never change their source.
Use one writer at a time; filesystem checks cannot eliminate concurrent changes
between the final alias check and publication.

Pure functions in `maintenance_schedule.py` provide `task_cost_summary(task)`
and `summarize_costs(schedule)`. Summaries contain recorded cents (or `null` when
nothing is priced), service count, unpriced-service count, and legacy-service
count. They include archives and validate unique service IDs to avoid counting a
service twice. Vehicle summaries group by stable profile ID **and exact captured
snapshot**, so later profile edits cannot relabel historical costs. Unpriced
services are counted separately from recorded zero-cost services. A partially
priced service contributes its recorded total while its missing components stay
marked Not recorded.

The v1.6.0 release introduced backup release `1.6.0` with manifest version 1. These backups retain
strict validation, byte-for-byte restoration, alias checks, staging, rollback,
and recovery behavior. v1.4.0 and v1.5.0 archives still restore without fabricated
fields or maintenance migrations; omitted stores remain untouched. Both schema
1 and schema 2 maintenance data round-trip exactly through current backups.
Older release manifests cannot claim schema 2 maintenance data. Existing
checklist CLI commands and their exact outputs remain unchanged.

### Maintenance Insights and Forecasting (v1.8.0)

Open **Maintenance Insights…** from the main checklist. One local window provides
**Forecasts**, **Costs and Months**, **Observations**, and **Data Quality** pages.
Choose a vehicle by its stable profile ID, optionally enter explicit current
mileage, and press **Evaluate / Refresh**. The initial evaluation date is today's
local calendar date; it can be changed for the session. Cost dates and explicit
mileage initially are blank. All-profile summaries remain fleet-wide; the profile
selection filters detail tables and scopes explicit mileage to one live profile.
Missing-profile historical IDs remain selectable for viewing, never for entering
current mileage. Equal vehicle names never join records.

Input edits require evaluation before navigation or export. Changing profiles or
the evaluation date clears explicit mileage. Detected source changes clear the
successful presentation and mileage; refresh reads the new data. Focus return,
evaluation, navigation and export revalidate sources without polling or a timer.
Restore resets open Insights windows' session filters. Closing/reopening resets
all inputs. **Open Task / Full History** passes only a stable task ID to the
existing Maintenance view; cost filters and mileage are never transferred.

**Facts, thresholds, rates and estimates are distinct.** Service and inspection
mileage/date entries are observed records. Next due date is the final service
date plus interval days; next due mileage is final service mileage plus interval
miles. Only a task with no services uses its baseline. Date and mileage axes stay
independent. Unconfigured axes say **Not applicable**. Disabled and archived
tasks retain thresholds and complete history but say **Excluded** operationally.
The existing seven mutually exclusive maintenance statuses and precedence remain
unchanged; estimated dates never determine those statuses. No current mileage
is inferred, prefilled or saved, and no calculated value is written to any store.

**Observed endpoint rate** uses two or more distinct trustworthy calendar dates
for one stable profile ID. Eligible observations are maintenance service mileage
on its completion date (including legacy, disabled and archived task history),
and schema-3 profile-linked inspection record mileage on its stored **UTC**
calendar date. Inspection snapshot mileage is not the record mileage. Unlinked
inspections, task baselines, appointments, undated live-profile or captured
snapshot mileage, and explicit session inputs are not rate observations.

Identical date/mileage observations collapse while retaining every source
reference and SHA-256 digest. Different mileages on the same date, or any
chronological mileage decrease across the profile's observations, make its rate
unavailable; no convenient subset is substituted. Future observations are warned
about and excluded. The latest accepted observation must be at most **90 days**
old, inclusive; **91 days** is stale for forecasting. Older points still remain
evidence. Undated profile mileage disagreement is informational and does not
create a dated conflict. Missing live profiles do not erase explicit historical
ID associations. A recreated vehicle with the same name has a different identity.

The endpoint rate is exactly `(newest mileage - oldest mileage) / (newest date -
oldest date)`, stored as integer miles and days. Every intermediate point is
checked for conflicts/decreases. The optional two-decimal miles/day display uses
integer half-up rounding and is never used in calculations. For a positive rate,
estimated days to threshold are the integer ceiling of
`(threshold - reference mileage) * rate days / rate miles`. With valid explicit
mileage, the reference is that mileage at the evaluation date. Otherwise the
label is **Estimate from last recorded observation**, using that dated record;
an estimated current odometer value is never calculated or displayed. Already
reached thresholds have no invented crossing date. Zero endpoint rate, missing
observations, conflicts, stale observations, future anchors and calendar overflow
have explicit unavailable reasons. Estimates assume historical usage continues;
they are not guaranteed service dates or appointment recommendations.

Explicit mileage below accepted history or an applicable task anchor is invalid.
Date and recorded-cost sections remain available, but mileage-dependent results,
navigation and exports are blocked until corrected. Nonnumeric or negative inputs
are not converted into a guessed value. No value is inferred from the stored
profile mileage, which may legitimately be older than service history.

**Cost filters** use inclusive service completion dates in canonical YYYY-MM-DD.
They affect overall/per-vehicle costs and monthly summaries only. All task states,
including missing-profile tasks, participate. Service counts equal priced plus
unpriced; legacy count is a subset of unpriced count. Explicit zero is priced.
Unpriced services do not enter monetary calculations. Partially priced services
contribute their validated recorded total, with omitted components still unrecorded.
With no priced matches, total, average, minimum and maximum say **Not recorded**,
never a fabricated zero. All arithmetic uses integer cents. Average cents use
`(2 * total_cents + priced_count) // (2 * priced_count)` (half-up), and total/count
remain available to recover the exact rational average. Vehicle aggregation uses
stable IDs; immutable captured snapshots are exported separately from live names.

Monthly costs are sorted by YYYY-MM, omit months with no matching services, and
retain unpriced-only months with counts and Not recorded money. Recently serviced
means `[evaluation date - 30 days, evaluation date]`, inclusive. Approaching lists
show date thresholds and mileage-estimate dates in `[evaluation date, evaluation
date + 30 days]`, independently. Already-due/overdue tasks are also listed separately.
Recent and approaching lists, existing status totals, and complete history ignore
cost filters. Future service dates are excluded from observed trends/rates but may
match literal cost queries; warnings explicitly disclose this distinction.

**Data Quality** lists deterministic warnings with stable identities and source
references. Missing stores are validated empty data with a warning and are never
created. Invalid UTF-8/JSON, duplicate keys, nonfinite constants, unsupported schemas,
invalid IDs/snapshots/costs/associations, duplicate service IDs, unsafe source files
and corrupt records make the complete capture unavailable, not partially successful
zero totals. Sources are bounded to 16 MiB each and 128 JSON nesting levels for
Insights. Nothing is repaired, normalized, migrated or discarded silently. A changed
source capture requires refresh, which is distinct from the 90-day age policy.

**CSV and HTML** exports include assumptions, source digests, warnings, stable IDs,
observation provenance, exact rates, both forecast axes, cost/month summaries,
recent/approaching entries, immutable task snapshots and complete unfiltered
maintenance service history. Each service appears once in `service_history`;
other row types may reference it and must not be summed as extra services. CSV
report version 1 uses the fixed `COLUMNS` schema in `maintenance_insights_exports.py`
and ordered `row_type` sections: report_context, assumption, source, warning,
status_total, vehicle_summary, profile_snapshot, observation, usage_rate,
task_forecast, cost_overall, cost_vehicle, cost_month, recent_service,
approaching_service, service_history. Blank CSV fields mean absent/not applicable;
availability and unavailable_reason explain unavailable estimates. Cost columns
contain integer cents, including zero. HTML formats dollars and Not recorded.

CSV uses UTF-8 BOM, CRLF, standard quoting and text formula-prefix protection,
including hidden whitespace/control prefixes; numeric columns remain numeric.
Spreadsheet handling of the protective apostrophe varies. HTML escapes every
dynamic value and uses no scripts or external resources. Fixed source bytes and
evaluation arguments produce deterministic reports, with no current export
timestamp, random value or absolute source path embedded. Files are not opened.

Publication stages a same-directory temporary file, flushes/fsyncs it and
revalidates sources, resolved aliases, parent identity and destination fingerprint
immediately before linking a new file or atomically replacing a confirmed output.
Replacing an existing output requires explicit confirmation of its fingerprint.
Default runtime filenames, injected runtime paths and actual source paths are
protected; leaf symlinks, hard-linked files, special files and resolved aliases
are rejected. Parent aliases such as macOS /var are resolved consistently. Failed
prepublication checks preserve existing destinations and remove temporary files.
These checks cannot eliminate every race with a hostile concurrent writer without
transactional filesystem support; use one writer at a time.

**Compatibility:** new backups identify release 1.8.0 with manifest version **1**.
The same four runtime members and existing maintenance schemas 1/2 are supported;
there is no Insights store. v1.4.0 through v1.7.0 backups remain readable with their
existing release/schema restrictions, omitted-member retention, byte preservation,
size limits and rollback/recovery behavior. Current dashboard, inspection and
maintenance-history export formats are unchanged, as are CLI behavior and checklist
state. Insights tests use disposable TemporaryDirectory stores and fully mocked Tk.

Non-goals: cost prediction, inferred current mileage, conflict reconciliation,
odometer-reset detection, automatic maintenance edits, schema migrations, AI,
network access, notifications, scheduling daemons, background services, telemetry,
or persistent Insights preferences. A stable ID alone cannot prove physical vehicle
continuity or identify an odometer replacement. UTC inspection dates may differ from
local service dates; daily conflicts deliberately favor unavailable estimates over
false precision.


## v1.9.0 local macOS application

This release adds a local Apple Silicon `.app` launcher. It requires the existing
verified runtime at `/Library/Frameworks/Python.framework/Versions/3.15/bin/python3`
with its working Tcl/Tk. There is no PATH/system-Python fallback, runtime download,
signing, notarization, Intel support, or public-distribution claim. macOS 15.5 is
the initial acceptance target. Python dependencies remain standard library only.

Build using that runtime with `-I -B build_macos_app.py` (after implementation
validation and before manual acceptance). The default output is
`dist/Vehicle Inspection Checklist.app`; an explicit disposable output path is
also supported. Existing output is never overwritten. The builder copies only
an explicit production-file allowlist, metadata, and launcher. Generated bundles
are ignored and must never be committed. No live data is included.

The supported source launcher `./launch_checklist.command` is unchanged. Source
GUI and CLI continue using repository-adjacent stores. The packaged application
forces native ARM64 execution with `/usr/bin/arch -arm64` and the exact framework
runtime. It resolves code inside its own bundle, ignores Python environment overrides and
user-site packages, and disables bytecode. Its separate writable location is:

`~/Library/Application Support/Vehicle Inspection Checklist/`

The four runtime files retain their existing names and schemas. Exports and
backups initially suggest `exports/` and `backups/` under that directory; their
save dialogs still allow another destination. A process-lifetime advisory lock
`.instance.lock` prevents concurrent packaged writers and is excluded from backups.
The empty lock file remains after exit; OS lock ownership ends when the process
exits. Do not delete the lock while the app is running.

On an empty packaged store, choose Restore, Start New, or Cancel. Restore requires
explicit selection of a validated ZIP and confirmation before any default
checklist is created. Cancellation or invalid archives do not initialize stores.
Existing packaged data opens normally; use Backup All Data before the existing
Restore Backup action to replace it, with explicit confirmation. There is no
automatic discovery, copying, migration, or synchronization with the source
installation. Partial backups preserve omitted files under existing restore rules.
Backup manifest version remains 1 with exactly the same four permitted runtime
members; release metadata is 1.9.0 and supported older backups remain restorable.

Before release, run all tests with `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest
discover -s tests -v`, compile modules in memory, check both shell launchers with
`bash -n`, and run `git diff --check`. Tests and disposable bundle inspection use
temporary homes and fixtures, never live repository or Application Support data.

Manual acceptance (not performed by automated validation):

1. Make an explicit acceptance backup; keep the original source installation.
2. Double-click a disposable app from Finder with Terminal closed. Check Dock,
   focus, close/reopen, repeated launch, spaces/Unicode paths, and moving the app.
3. Exercise Cancel, Start New, and restore of a fixture backup before adopting
   real data. Confirm the Application Support location and source/store separation.
4. Repeat v1.8.0 GUI acceptance: 5/7 progress, all windows/tabs, complete history,
   costs, profile mileage 43000, and session-only evaluation mileage. Restart to
   verify filter/mileage reset. Check exports and backup/restore destinations.
5. Verify missing-runtime and startup errors visibly; verify no bundle writes
   during read-only-bundle operation. Do not remove the installed runtime to test
   failure; use a disposable launcher fixture.
6. Recheck protected-data hashes, exact changed files, final unique test count,
   clean whitespace, backup compatibility, and ignored build artifacts before
   separately authorizing staging and release actions.

Rollback: quit the packaged app, retain its Application Support data and backups,
and use the v1.8.0 source installation with its untouched source data. v1.8.0 does
not accept 1.9.0-labelled backups; preserve a pre-upgrade backup for rollback.
There is no schema conversion or automatic reverse migration. Finder lifecycle
and Tk activation remain manual acceptance gates; this bundle is a local launcher,
not a self-contained or publicly distributed application.

Accepted v1.9.0 application identity: this app is a local double-clickable wrapper
that depends on the installed, verified Python 3.15/Tk 9 framework runtime. macOS
displays **Python** in the application menu because that external runtime owns
the process. This cosmetic limitation does not affect the **Vehicle Inspection
Checklist** window title, source/packaged data separation, safety, or functionality.
No application-menu relabeling or Python installation changes are performed.

Native application identity, a custom icon, a self-contained runtime, signing,
notarization, and distribution are deferred to **v2.0.0**.
