#!/usr/bin/env python3
"""Vehicle Maintenance Checklist CLI Tool.

Uses only Python standard library modules. Data persists to a JSON file
in the same directory as this script.
"""

import argparse
import json
import sys
from pathlib import Path


# All items with their stable IDs and names (7 total)
ALL_ITEMS = {
    "oil": {"name": "Oil & Fluid Levels"},
    "coolant": {"name": "Coolant Reservoir Level"},
    "tires": {"name": "Tire Pressure & Wear"},
    "brakes": {"name": "Brake condition."},
    "lights": {"name": "All Light Functions"},
    "battery": {"name": "Battery Health & Terminals"},
    "leaks": {"name": "Fluid Leaks Under Vehicle"},
}

TOTAL_ITEMS = len(ALL_ITEMS)


def create_default_data():
    """Create default data structure with all items set to False."""
    return {item_id: {"name": info["name"], "complete": False}
             for item_id, info in ALL_ITEMS.items()}


def get_all_items():
    """Return a copy of the ALL_ITEMS dictionary (unmodified)."""
    return dict(ALL_ITEMS)


def load_data(data_path):
    """Load data from JSON file. Create default if file doesn't exist."""
    path = Path(data_path)
    if not path.exists():
        path.write_text(json.dumps(create_default_data()))
        return create_default_data()

    with open(path, "r") as f:
        return json.load(f)


def save_data(data_path, data):
    """Save data to JSON file (replaces any existing file)."""
    path = Path(data_path)
    path.write_text(json.dumps(data, indent=2))


def set_complete(data_path, item_id):
    """Set the item with stable ID as complete. Idempotent operation.

    Raises ValueError if item_id is not known.
    """
    data = load_data(data_path)

    if item_id not in ALL_ITEMS:
        raise ValueError(f"Unknown item '{item_id}'")

    data[item_id]["complete"] = True
    save_data(data_path, data)


def reset_items(data_path):
    """Reset all items to not complete."""
    data = load_data(data_path)
    for item_id in ALL_ITEMS:
        data[item_id]["complete"] = False
    save_data(data_path, data)


def calculate_progress(data):
    """Calculate progress percentage and return as tuple (percentage, bar_string)."""
    completed = sum(1 for item_id, info in ALL_ITEMS.items()
                    if data[item_id]["complete"])

    percentage = (completed / TOTAL_ITEMS) * 100
    filled_char = "█"
    empty_char = "░"

    # Seven-cell bar: one cell per checklist item
    bar_width = 7
    filled_count = completed
    bars = [filled_char] * filled_count + [empty_char] * (bar_width - filled_count)

    return percentage, "".join(bars), f"{completed}/{TOTAL_ITEMS} ({percentage:.0f}%)"


def print_table(data):
    """Print items as a formatted table with status symbols."""
    # Column widths: ID=8, name=30+
    row = ["ID".ljust(8), "Name".ljust(35), "Status"]

    for item_id in sorted(get_all_items().keys()):
        info = get_all_items()[item_id]
        complete = data.get(item_id, {}).get("complete", False)
        symbol = "✓" if complete else "□"
        row.append(f"{item_id:8}")
        row.append(info["name"].ljust(35))
        row.append(symbol)

    print("\n".join(row))
    return


def get_complete_count(data_path):
    """Get number of completed items."""
    data = load_data(data_path)
    return sum(1 for item_id, info in ALL_ITEMS.items()
               if data[item_id]["complete"])


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        prog="python3 checklist.py",
        description="Vehicle maintenance inspection checklist tool."
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list command
    list_parser = subparsers.add_parser("list", help="Show all items and their status")

    # complete command - no choices, validate in core function
    complete_parser = subparsers.add_parser("complete",
                                            help="Mark an item as complete")
    complete_parser.add_argument("item_id", type=str,
                                 help="Item stable ID (any value)")

    # reset command
    reset_parser = subparsers.add_parser("reset", help="Clear all items")

    # progress command
    progress_parser = subparsers.add_parser("progress",
                                            help="Show overall completion percentage")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    data_path = Path(__file__).parent / "checklist_data.json"

    try:
        if args.command == "list":
            data = load_data(data_path)
            print_table(data)
            return

        elif args.command == "complete":
            # Pass the supplied ID to set_complete() and handle validation in core
            set_complete(data_path, args.item_id)
            count, bar_str, percentage = calculate_progress(load_data(data_path))
            print(f"{args.item_id} marked complete. Progress: [{bar_str}]")
            return

        elif args.command == "reset":
            reset_items(data_path)
            count, bar_str, percentage = calculate_progress(load_data(data_path))
            print(f"All items cleared. Progress reset to {percentage}")
            return

        elif args.command == "progress":
            data = load_data(data_path)
            percentage, bar, progress_str = calculate_progress(data)
            print(f"Progress: [{bar}] {progress_str}")

    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
