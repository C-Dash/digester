#!/usr/bin/env bash
# Bootstrap script — creates .venv and installs all dependencies
# Run once on a new machine: bash setup_venv.sh
set -e

echo "[cdash-digester] Creating virtual environment..."
python3 -m venv .venv

echo "[cdash-digester] Upgrading pip..."
.venv/bin/python -m pip install --upgrade pip

echo "[cdash-digester] Installing dependencies..."
.venv/bin/pip install -r requirements.txt

echo "[cdash-digester] Installing package in editable mode..."
.venv/bin/pip install -e .

echo ""
echo "Done. Activate the environment with:"
echo "  source .venv/bin/activate"
