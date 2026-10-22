#!/bin/bash

launcher_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || {
    printf 'Error: Could not determine the launcher directory.\n' >&2
    exit 1
}

cd -- "$launcher_dir" || {
    printf 'Error: Could not change to the launcher directory.\n' >&2
    exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
    printf 'Error: python3 is unavailable. Install Python 3 and try again.\n' >&2
    exit 1
fi

exec python3 "$launcher_dir/gui.py"
