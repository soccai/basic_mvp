import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = Path(os.environ.get("LIFEOS_DB_PATH", str(DATA_DIR / "lifeos.db")))

# STT
WHISPER_MODEL_SIZE = "base.en"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"
WHISPER_BEAM_SIZE = 1

# TTS
PIPER_VOICE = "en_US-amy-medium"
PIPER_MODEL_PATH = MODELS_DIR / "piper-en_US-amy-medium" / "en_US-amy-medium.onnx"
PIPER_CONFIG_PATH = MODELS_DIR / "piper-en_US-amy-medium" / "en_US-amy-medium.onnx.json"
PIPER_SAMPLE_RATE = 22050

# Audio
STT_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1

# VAD
VAD_MIN_SILENCE_MS = 800           # wait 0.8s of silence before treating speech as finished
VAD_ENERGY_THRESHOLD = 0.04
VAD_MIN_SPEECH_MS = 200            # ignore utterances shorter than 200ms (fragments)

# Session
SESSION_INTERRUPT_TIMEOUT_SECONDS = 30
SESSION_TYPE_DEFAULT = "focus"

# Connection gate
CONNECTION_GRACE_PERIOD_SECONDS = int(os.environ.get("LIFEOS_CONNECTION_GRACE_SECONDS", "10"))

# LLM provider (ollama, openai, anthropic — only ollama implemented for now)
LLM_PROVIDER = os.environ.get("LIFEOS_LLM_PROVIDER", "ollama")

# Ollama (optional)
OLLAMA_BASE_URL = os.environ.get("LIFEOS_OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("LIFEOS_OLLAMA_MODEL", "gemma4:e4b")
OLLAMA_TIMEOUT_SECONDS = 10
OLLAMA_GENERATE_TIMEOUT_SECONDS = 60   # gemma4 thinking model needs ~5-8s for reasoning alone
OLLAMA_SUMMARY_TIMEOUT_SECONDS = 90   # summaries are longer; budget accordingly
INTENT_LLM_FALLBACK_ENABLED = os.environ.get("LIFEOS_INTENT_LLM_FALLBACK", "true").lower() == "true"

# Graphiti / Kuzu (embedded graph DB — no external server needed)
KUZU_DB_PATH = Path(os.environ.get("LIFEOS_KUZU_DB", str(DATA_DIR / "identity.kuzu")))
GRAPHITI_GROUP_ID = os.environ.get("LIFEOS_GRAPHITI_GROUP_ID", "lifeos_user")

# Server
HOST = os.environ.get("LIFEOS_HOST", "0.0.0.0")
PORT = int(os.environ.get("LIFEOS_PORT", "8000"))
LOG_LEVEL = os.environ.get("LIFEOS_LOG_LEVEL", "INFO")


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


#Quest/Exploration/Discovery/Journey
#Mission/Challenge/Experience
