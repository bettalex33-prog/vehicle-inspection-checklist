# Vehicle Maintenance Checklist

A simple command-line tool for tracking vehicle maintenance inspections using only Python standard library modules. No installation or external dependencies required.

## Requirements

**None** - This tool uses only the Python standard library: `argparse`, `json`, `sys`, and `pathlib`.

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
ID         Name                             Status
oil        Oil & Fluid Levels                □
coolant    Coolant Reservoir Level           □
tires      Tire Pressure & Wear              □
brakes     Brake condition.                  □
lights     All Light Functions               □
battery    Battery Health & Terminals        □
leaks      Fluid Leaks Under Vehicle         □
```

### Run the complete command (mark an item as complete)

```bash
python3 checklist.py complete oil
```

**Output:**
```
oil marked complete. Progress: 14%
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
Progress: [░░░░░░░░░░░░░░░░░░] 0%
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
Progress: [████████░░░░░░░░] 100%
Total: 7/7
```

## Running Tests

Run the built-in unit tests using Python's unittest module:

```bash
python3 -m unittest discover -s tests -v
```

**Expected output:**
```
.................
----------------------------------------------------------------------
Ran 9 tests in 0.002s

OK
```

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
