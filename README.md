# Vehicle Maintenance Checklist

A simple command-line tool for tracking vehicle maintenance inspections using only Python standard library modules. No installation or external dependencies required.

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
