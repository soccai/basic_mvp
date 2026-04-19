#!/usr/bin/env bash
# =============================================================================
# LifeOS — Stop
# Gracefully shuts down the background server started by start.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.lifeos.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No .lifeos.pid found — server may not be running."
  # Try to free port 8000 anyway
  if command -v lsof &>/dev/null; then
    PIDS="$(lsof -ti:8000 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
      echo "Found process(es) on port 8000 — stopping..."
      echo "$PIDS" | xargs kill -TERM 2>/dev/null || true
      echo "✓ Stopped"
    fi
  fi
  exit 0
fi

PID="$(cat "$PID_FILE")"

if ! kill -0 "$PID" 2>/dev/null; then
  echo "Process $PID is not running (already stopped)."
  rm -f "$PID_FILE"
  exit 0
fi

echo "Stopping LifeOS server (PID $PID)..."
kill -TERM "$PID" 2>/dev/null || true

# Wait up to 5 s for clean exit
for i in $(seq 1 5); do
  sleep 1
  if ! kill -0 "$PID" 2>/dev/null; then
    break
  fi
done

# Force-kill if still running
if kill -0 "$PID" 2>/dev/null; then
  echo "  Force-killing..."
  kill -KILL "$PID" 2>/dev/null || true
fi

rm -f "$PID_FILE"
echo "✓ LifeOS stopped"
