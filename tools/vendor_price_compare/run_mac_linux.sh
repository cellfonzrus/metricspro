#!/usr/bin/env bash
# Run:  bash run_mac_linux.sh      (first run installs everything — a few minutes)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Setting up for the first time..."
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python -m playwright install chromium
else
  . .venv/bin/activate
fi
[ -f credentials.csv ] || cp credentials_TEMPLATE.csv credentials.csv
python compare_prices.py "$@"
