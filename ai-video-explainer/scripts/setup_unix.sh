#!/usr/bin/env bash
# One-time setup for non-Windows development machines (Linux/macOS).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

echo "[1/4] Creating virtual environment..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/4] Installing backend dependencies..."
pip install --upgrade pip
pip install -r backend/requirements-dev.txt

echo "[3/4] Installing frontend dependencies..."
(cd frontend && npm install)

echo "[4/4] Creating .env from env.example (if missing)..."
[ -f .env ] || cp env.example .env

echo
echo "Setup complete. Run: scripts/run_backend_unix.sh, scripts/run_frontend_unix.sh, scripts/run_tests_unix.sh"
