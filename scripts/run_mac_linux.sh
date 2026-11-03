#!/usr/bin/env bash
# One-command launcher for macOS and Linux users
set -e

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  echo "[VideoDownloader] Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "[VideoDownloader] Starting Web & AI Workspace..."
exec python3 -m videodownloader.launcher "$@"
