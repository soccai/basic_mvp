# LifeOS Voice Server

A local-first, voice-driven personal operating system. Talk to it to start a focused work session, check in during it, and close it out. Everything runs on your machine no cloud, no subscriptions.

---

## What It Does

- **Start a session** by saying *"let's go"* or *"start session"*
- **Talk freely** during a session — the LLM responds contextually
- **End a session** by saying *"done"* or tapping the End button
- **Hear a summary** and a session memory is stored locally in SQLite
- **Review your timeline**— a chronological log of all completed sessions

All audio processing (STT, TTS) runs locally. Ollama provides the optional LLM layer.

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | ≥ 3.11 | |
| [Ollama](https://ollama.com/download) | any | Optional — enables LLM responses & summaries |

> **macOS:** `brew install python@3.11`  
> **Linux (Ubuntu/Debian):** `sudo apt install python3.11`

---

## Quick Start

```bash
git clone <repo-url>
cd basic_mvp
bash start.sh
```

That's it. On first run `start.sh` will:

1. Create a Python virtual environment
2. Install all Python dependencies
3. Download the Whisper `base.en` speech-to-text model (~148 MB)
4. Download the Piper `amy-medium` text-to-speech voice (~65 MB)
5. Pull `gemma4:e4b` via Ollama if Ollama is installed (one-time)
6. Start the server and open your browser at `http://localhost:8000`

**Subsequent starts take ~3 seconds** — all downloads are cached.

To stop the server:

```bash
bash stop.sh
# or
make stop
```

---

## Project Structure

```
lifeos/5/
├── start.sh                  ← One-click start (macOS + Linux)
├── stop.sh                   ← Graceful shutdown
├── Makefile                  ← make start / stop / test / clean-db
│
├── client/                   ← Browser UI (plain HTML + JS, no build step)
│   ├── index.html
│   ├── app.js                ← Main app logic and WebSocket event handlers
│   ├── voice.js              ← Audio capture, playback, browser TTS fallback
│   ├── ws.js                 ← WebSocket client wrapper
│   ├── session-surface.js    ← Session UI panel
│   ├── timeline-surface.js   ← Timeline UI panel
│   ├── pcm-processor.js      ← AudioWorklet for real-time PCM capture
│   └── styles.css
│
├── server/
│   ├── main.py               ← FastAPI app, lifespan, route registration
│   ├── config.py             ← All config (env-var overrideable)
│   ├── governing_loop.py     ← 12-step processing loop for each utterance
│   │
│   ├── ws/
│   │   └── handler.py        ← WebSocket handler (audio/text/control messages)
│   │
│   ├── voice/
│   │   ├── stt.py            ← Speech-to-text (faster-whisper)
│   │   ├── tts.py            ← Text-to-speech (piper-tts Python or CLI)
│   │   ├── vad.py            ← Voice activity detection (energy-based)
│   │   └── audio.py          ← PCM helpers (resample, mono, float32)
│   │
│   ├── intent/
│   │   ├── keywords.py       ← Keyword-based intent classification
│   │   ├── router.py         ← Intent router (keyword → Ollama fallback)
│   │   └── ollama.py         ← Ollama HTTP client for LLM intent classification
│   │
│   ├── llm/
│   │   └── responder.py      ← LLM response + session summary generation
│   │
│   ├── session/
│   │   ├── manager.py        ← Session FSM + lifecycle management
│   │   └── models.py         ← SessionRecord, SessionState, SessionStatus
│   │
│   ├── events/
│   │   └── store.py          ← SQLite event store (aiosqlite)
│   │
│   ├── routes/
│   │   ├── health.py         ← GET /health
│   │   ├── sessions.py       ← GET /api/sessions, /api/sessions/:id, /api/state
│   │   ├── timeline.py       ← GET /api/timeline
│   │   └── tts.py            ← POST /api/tts (HTTP TTS for client-initiated speech)
│   │
│   └── stubs/
│       ├── presence.py       ← Stub: presence resolution
│       ├── identity.py       ← Stub: identity validation
│       └── policy.py         ← Stub: policy check
│
├── models/                   ← Downloaded model files (gitignored)
├── data/                     ← SQLite database + logs (gitignored)
└── scripts/
    ├── setup.sh              ← Legacy setup script
    └── dev.sh                ← Legacy dev server script
```

---

## How It Works

### Audio Pipeline

```
Microphone → PCM chunks (AudioWorklet) → WebSocket (binary)
  → VAD (energy-based silence detection)
    → speech_end → STT (faster-whisper, run_in_executor)
      → Transcript → Governing Loop → Response text
        → TTS (piper-tts) → WAV bytes → WebSocket → AudioContext.playWAV()
```

### The 12-Step Governing Loop

Every utterance passes through `governing_loop.py`:

```
1.  Input received
2.  Presence resolved       (stub)
3.  Identity validated      (stub)
4.  Memory retrieved        (stub)
5.  Context evaluated       (session state snapshot)
6.  Policy checked          (stub)
7.  Intent classified       (keywords → Ollama fallback)
8.  LLM response generated  (inside session, non-lifecycle intents only)
9.  Engine execution        (start_session / end_session)
10. Event logged            (lifecycle events only; utterances are ephemeral)
11. Memory written          (LLM session summary at session close)
12. Response ready
```

### Session State Machine

```
IDLE → LISTENING → PROCESSING → INTENT_RESOLVED
                                        ├── SESSION_ACTIVE ──→ COMPLETING → COMPLETED → IDLE
                                        └── IDLE

SESSION_ACTIVE ──disconnect──→ SESSION_INTERRUPTED ──timeout──→ ABANDONED → IDLE
                                        └──reconnect──→ SESSION_ACTIVE
```

### Intent Classification

Intents are classified in two layers:

1. **Keyword matching** — fast, deterministic, word-boundary safe (e.g. "start", "done", "help")
2. **Ollama LLM fallback** — if no keyword matches and Ollama is available

| Intent | Triggers |
|--------|---------|
| `START_SESSION` | "let's go", "start", "begin", "ready", "prepare" |
| `END_SESSION` | "done", "finish", "end session", "stop session" |
| `REQUEST_GUIDANCE` | "help", "what now", "what should I do", "show timeline" |
| `REQUEST_FINANCE` | "I need money", "send me money", "ask my parents" |
| `UNCLEAR` | anything else |

---

## Configuration

All settings can be overridden via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LIFEOS_DB_PATH` | `data/lifeos.db` | SQLite database path |
| `LIFEOS_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `LIFEOS_OLLAMA_MODEL` | `gemma4:e4b` | Model to use for intent + responses |
| `LIFEOS_HOST` | `0.0.0.0` | Bind host |
| `LIFEOS_PORT` | `8000` | Bind port |
| `LIFEOS_CORS_ORIGINS` | `http://localhost:8000,...` | Allowed CORS origins (comma-separated) |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server + component status |
| `GET` | `/api/sessions` | List all sessions |
| `GET` | `/api/sessions/:id` | Get single session |
| `GET` | `/api/state` | Current session FSM state |
| `GET` | `/api/timeline` | Completed/abandoned session timeline |
| `POST` | `/api/tts` | Synthesize text → WAV (body: `{"text": "..."}`) |
| `WS` | `/ws` | Main voice WebSocket |

### WebSocket Message Types

**Client → Server**

| Type | Payload | Description |
|------|---------|-------------|
| binary | PCM16 bytes | Raw audio chunk |
| `config` | `{sampleRate: number}` | Set client sample rate |
| `ping` | — | Keepalive |
| `text_input` | `{text: string}` | Text utterance (no mic) |
| `end_session` | — | Tap-to-end current session |

**Server → Client**

| Type | Payload | Description |
|------|---------|-------------|
| binary | WAV bytes | TTS audio |
| `audio_done` | — | TTS playback complete |
| `tts_text` | `{text}` | TTS fallback (browser speech) |
| `transcript` | `{text, is_final}` | STT result |
| `intent` | `{intent, response_text}` | Classified intent + response |
| `session_started` | `{session_id, started_at, intent_transcript}` | Session began |
| `session_completed` | `{session_id, duration_ms, summary}` | Session ended |
| `session_resumed` | `{session_id}` | Reconnected to interrupted session |
| `pong` | — | Ping response |
| `error` | `{message}` | Server-side error |

---

## Development

```bash
# Run tests
make test

# Start with hot-reload (dev mode)
source .venv/bin/activate
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

# Clear the database
make clean-db

# Clear only sessions (keep events)
make clean-sessions
```

---

## Troubleshooting

**"What do you want to move forward right now?" is in a male voice**  
TTS is falling back to browser `speechSynthesis`. The browser voice picker prefers Samantha (en-US) on macOS. If Piper failed to load, check `data/lifeos.log` for `STT/TTS failed to load`.

**Server starts but browser shows nothing**  
Check that `client/` directory exists at the project root. The FastAPI app serves it as static files.

**Ollama responses are slow or timing out**  
Default timeout is 10 s for responses, 20 s for session summaries. Increase via `OLLAMA_GENERATE_TIMEOUT_SECONDS` / `OLLAMA_SUMMARY_TIMEOUT_SECONDS` in `config.py`.

**WebSocket "Cannot call receive once a disconnect message has been received"**  
Fixed in `ws/handler.py` — update to the latest version.

---

## Data & Privacy

- All audio is processed **locally** : nothing is sent to external servers
- Transcriptions are **ephemeral** : only session lifecycle events are stored
- Session summaries (LLM-generated) are stored in `data/lifeos.db`
- The database is a plain SQLite file : inspect it with any SQLite browser
