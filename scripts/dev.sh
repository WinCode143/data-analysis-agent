#!/usr/bin/env bash
# Starts the backend (port 8000) and the frontend with hot reload (port 5173).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend" && uv sync -q && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
BACK=$!
trap 'kill $BACK' EXIT
cd "$ROOT/frontend" && npm install --silent && npm run dev
