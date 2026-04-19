#!/usr/bin/env bash
# =============================================================================
# LifeOS — One-Click Start
# Works on macOS and Linux. Run: bash start.sh
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve project root (works whether called as ./start.sh or bash scripts/start.sh)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PROJECT_ROOT="$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Colours (disabled on non-interactive terminals)
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  GREEN="\033[32m" YELLOW="\033[33m" RED="\033[31m" BOLD="\033[1m" RESET="\033[0m"
else
  GREEN="" YELLOW="" RED="" BOLD="" RESET=""
fi

ok()   { echo -e "  ${GREEN}✓${RESET} $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET} $*"; }
err()  { echo -e "  ${RED}✗${RESET} $*" >&2; }
step() { echo -e "\n${BOLD}[$1/9]${RESET} $2"; }

echo -e "\n${BOLD}=== LifeOS ===${RESET}\n"

# ===========================================================================
# [1/9] OS detection
# ===========================================================================
step 1 "Detecting OS..."
OS="$(uname -s)"
case "$OS" in
  Darwin) OPEN_CMD="open";    ok "macOS detected" ;;
  Linux)  OPEN_CMD="xdg-open"; ok "Linux detected" ;;
  *)      err "Unsupported OS: $OS"; exit 1 ;;
esac

# ===========================================================================
# [2/9] Python ≥ 3.11 check
# ===========================================================================
step 2 "Checking Python..."
if ! command -v python3 &>/dev/null; then
  err "python3 not found."
  echo "  macOS: brew install python@3.11"
  echo "  Linux: sudo apt install python3.11  OR  sudo dnf install python3.11"
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER##*.}"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  err "Python 3.11+ required (found $PY_VER)."
  echo "  macOS: brew install python@3.11"
  echo "  Linux: sudo apt install python3.11"
  exit 1
fi
ok "Python $PY_VER"

# ===========================================================================
# [3/9] Virtual environment
# ===========================================================================
step 3 "Setting up environment..."
VENV="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV" ]; then
  echo "  Creating .venv..."
  python3 -m venv "$VENV"
  ok "Created .venv"
else
  ok ".venv exists"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# ===========================================================================
# [4/9] Python dependencies (hash-cached for fast subsequent runs)
# ===========================================================================
step 4 "Installing dependencies..."
REQ_FILE="$PROJECT_ROOT/server/requirements.txt"
HASH_FILE="$VENV/.req_hash"
CURRENT_HASH="$(sha256sum "$REQ_FILE" 2>/dev/null || shasum -a 256 "$REQ_FILE" | awk '{print $1}')"

if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$CURRENT_HASH" ]; then
  echo "  Installing packages (first run ~2 min)..."
  pip install --upgrade pip -q
  pip install -r "$REQ_FILE" -q
  echo "$CURRENT_HASH" > "$HASH_FILE"
  ok "Installed"
else
  ok "Cached (no changes to requirements.txt)"
fi

# ===========================================================================
# [5/9] Runtime directories
# ===========================================================================
mkdir -p "$PROJECT_ROOT/models" "$PROJECT_ROOT/data"

# ===========================================================================
# [6/9] Whisper base.en model (downloads to ~/.cache/huggingface on first run)
# ===========================================================================
step 5 "Whisper STT model..."
WHISPER_READY="$(python3 -c "
import sys
try:
    from faster_whisper import WhisperModel
    # Check if model files are cached (avoid full download probe)
    import huggingface_hub, os
    cache = huggingface_hub.constants.HUGGINGFACE_HUB_CACHE
    model_dir = os.path.join(cache, 'models--Systran--faster-whisper-base.en')
    print('yes' if os.path.isdir(model_dir) else 'no')
except Exception:
    print('no')
" 2>/dev/null)"

if [ "$WHISPER_READY" = "no" ]; then
  echo "  Downloading Whisper base.en (~148 MB, one-time)..."
  python3 -c "
from faster_whisper import WhisperModel
print('  Loading model...')
WhisperModel('base.en', device='cpu', compute_type='int8')
print('  Done.')
"
  ok "Whisper model ready"
else
  ok "Whisper model cached"
fi

# ===========================================================================
# [7/9] Piper TTS voice model
# ===========================================================================
step 6 "Piper TTS voice..."
PIPER_DIR="$PROJECT_ROOT/models/piper-en_US-amy-medium"
PIPER_ONNX="$PIPER_DIR/en_US-amy-medium.onnx"

if [ ! -f "$PIPER_ONNX" ]; then
  echo "  Downloading Piper amy-medium voice (~65 MB, one-time)..."
  mkdir -p "$PIPER_DIR"
  BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium"
  curl -fsSL -o "$PIPER_ONNX"       "$BASE_URL/en_US-amy-medium.onnx"
  curl -fsSL -o "${PIPER_ONNX}.json" "$BASE_URL/en_US-amy-medium.onnx.json"
  ok "Piper voice ready"
else
  ok "Piper voice cached"
fi

# ===========================================================================
# [8/9] Ollama + llama3.1:8b
# ===========================================================================
step 7 "Ollama / LLM..."
if ! command -v ollama &>/dev/null; then
  warn "Ollama not found — LLM features will be disabled."
  echo "    Install: https://ollama.com/download"
  echo "    Then re-run start.sh — the model will be pulled automatically."
else
  if ollama list 2>/dev/null | grep -q "llama3.1:8b"; then
    ok "llama3.1:8b ready"
  else
    echo "  Pulling llama3.1:8b (~4.7 GB, one-time — this will take a while)..."
    ollama pull llama3.1:8b
    ok "llama3.1:8b pulled"
  fi
fi

# ===========================================================================
# [9/9] Clean up stale server, then start
# ===========================================================================
step 8 "Starting server..."
PID_FILE="$PROJECT_ROOT/.lifeos.pid"
LOG_FILE="$PROJECT_ROOT/data/lifeos.log"

# Kill any existing LifeOS server
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "  Stopping previous server (PID $OLD_PID)..."
    kill -TERM "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

# Also free port 8000 if something else is squatting on it
if command -v lsof &>/dev/null; then
  STALE="$(lsof -ti:8000 2>/dev/null || true)"
  if [ -n "$STALE" ]; then
    echo "  Freeing port 8000..."
    echo "$STALE" | xargs kill -TERM 2>/dev/null || true
    sleep 1
  fi
fi

# Launch uvicorn in background
echo "  Launching uvicorn..."
nohup python -m uvicorn server.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --log-level info \
  >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
ok "Server started (PID $SERVER_PID)"

# ---------------------------------------------------------------------------
# Health poll — wait up to 20 s for /health to return 200
# ---------------------------------------------------------------------------
step 9 "Waiting for server to be healthy..."
MAX_WAIT=20
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS="$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")"
  if [ "$STATUS" = "200" ]; then
    ok "Healthy (${ELAPSED}s)"
    break
  fi
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done

if [ "$STATUS" != "200" ]; then
  err "Server did not become healthy within ${MAX_WAIT}s."
  echo "  Last 20 lines of log:"
  tail -20 "$LOG_FILE" 2>/dev/null | sed 's/^/    /'
  exit 1
fi

# ---------------------------------------------------------------------------
# Open browser
# ---------------------------------------------------------------------------
"$OPEN_CMD" "http://localhost:8000" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Ready
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}✓ LifeOS is running at http://localhost:8000${RESET}"
echo "  Logs  : data/lifeos.log"
echo "  Stop  : bash stop.sh   (or Ctrl+C — server keeps running in background)"
echo ""

# Tail the log so the terminal stays useful (Ctrl+C exits tail, server keeps running)
echo "--- Live server log (Ctrl+C to detach) ---"
tail -f "$LOG_FILE"
