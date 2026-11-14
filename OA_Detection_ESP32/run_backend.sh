#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  echo "[Setup] Creating Python virtual environment in .venv..."
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, uvicorn, aiosqlite, sklearn" 2>/dev/null; then
  echo "[Setup] Installing backend dependencies..."
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

mkdir -p data

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '127.0.0.1')"
echo "=========================================================="
echo " SIH26004 - OA Risk Detection Backend"
echo " Dashboard URL : http://localhost:8000"
echo " LAN IP (ESP32): ${LAN_IP}:8000"
echo " ESP32 WS Push : ws://${LAN_IP}:8000/ws/esp32"
echo " API Docs      : http://localhost:8000/docs"
echo "=========================================================="

exec .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000

