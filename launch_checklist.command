#!/bin/bash

launcher_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || {
    printf 'Error: Could not determine the launcher directory.\n' >&2
    exit 1
}

cd -- "$launcher_dir" || {
    printf 'Error: Could not change to the launcher directory.\n' >&2
    exit 1
}

python_path="/Library/Frameworks/Python.framework/Versions/3.15/bin/python3"

if [ ! -x "$python_path" ]; then
    echo "Error: Python 3 was not found at $python_path"
    read -r -p "Press Enter to close..."
    exit 1
fi

exec "$python_path" "$launcher_dir/gui.py"
