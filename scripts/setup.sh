#!/bin/bash
set -e

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

echo "=== LifeOS Voice MVP Setup ==="

# 1. Python virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# 2. Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r server/requirements.txt -q

# 3. Create runtime directories
mkdir -p models data

# 4. Pre-download faster-whisper model
echo "Pre-loading faster-whisper base.en model (first run downloads ~150MB)..."
python3 -c "
from faster_whisper import WhisperModel
print('Downloading/loading base.en model...')
model = WhisperModel('base.en', device='cpu', compute_type='int8')
print('STT model ready.')
"

# 5. Download Piper TTS model
PIPER_DIR="$PROJECT_ROOT/models/piper-en_US-amy-medium"
if [ ! -f "$PIPER_DIR/en_US-amy-medium.onnx" ]; then
    echo "Downloading Piper TTS voice model..."
    mkdir -p "$PIPER_DIR"
    curl -L -o "$PIPER_DIR/en_US-amy-medium.onnx" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx"
    curl -L -o "$PIPER_DIR/en_US-amy-medium.onnx.json" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    echo "Piper TTS model downloaded."
else
    echo "Piper TTS model already present."
fi

# 6. Check for piper CLI (optional)
if command -v piper &> /dev/null; then
    echo "Piper CLI found: $(which piper)"
else
    echo "WARNING: piper CLI not found. TTS will try piper-tts Python package or fall back to browser speechSynthesis."
    echo "  Install: pip install piper-tts  OR  brew install piper"
fi

echo ""
echo "=== Setup complete ==="
echo "Run 'make dev' to start the server."
