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
