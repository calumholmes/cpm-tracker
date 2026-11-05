#!/usr/bin/env bash
# cpm-tracker launcher for macOS / Linux: sets up a virtual environment on first run, then starts the bot.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: setting up cpm-tracker..."
  python3 -m venv .venv || { echo "Python 3.10 or newer is required (with the venv module)."; exit 1; }
  .venv/bin/python -m pip install --upgrade pip
fi

.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python bot.py
