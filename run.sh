#!/usr/bin/env sh
# Wrapper used by cron / systemd. Pass "full" or "light" as $1.
set -eu

PROJECT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$PROJECT_DIR"

# Activate the project venv. Fail loudly if it's missing.
if [ ! -f .venv/bin/activate ]; then
    echo "run.sh: .venv not found in $PROJECT_DIR — create it with 'python3 -m venv .venv'" >&2
    exit 2
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# Make Playwright browser lookup deterministic across cron + interactive shells.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

if [ $# -lt 1 ]; then
    echo "usage: run.sh {full|light}" >&2
    exit 2
fi

exec python main.py --mode "$1"
