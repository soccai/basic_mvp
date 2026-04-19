# LifeOS Voice Server — Architecture

---

## 1. High-Level Component Map

```mermaid
graph TD
    Client["🌐 Browser Client\n(WebSocket + HTTP)"]

    subgraph FastAPI["FastAPI Application (main.py)"]
        WS["WebSocket Endpoint\n/ws"]
        HTTP_Health["GET /health"]
        HTTP_Sessions["GET /api/sessions\nGET /api/sessions/:id\nGET /api/state"]
        HTTP_Timeline["GET /api/timeline"]
        Static["Static Files\n/client (html=True)"]
    end

    subgraph Core["Core Processing"]
        Handler["ws/handler.py\nwebsocket_handler()"]
        GovLoop["governing_loop.py\nexecute_governing_loop()"]
    end

    subgraph Voice["Voice Pipeline"]
        VAD["voice/vad.py\nSimpleVAD"]
        Audio["voice/audio.py\nPCM helpers"]
        STT["voice/stt.py\nSTTAdapter\n(faster-whisper)"]
        TTS["voice/tts.py\nTTSAdapter\n(piper-tts)"]
    end

    subgraph Intent["Intent Layer"]
        Router["intent/router.py\nIntentRouter"]
        Keywords["intent/keywords.py\nkeyword_match()"]
        Ollama["intent/ollama.py\nOllamaClient"]
    end

    subgraph LLM["LLM Layer"]
        Responder["llm/responder.py\nLLMResponder"]
    end

    subgraph Session["Session Layer"]
        Manager["session/manager.py\nSessionManager"]
        Models["session/models.py\nSessionRecord / SessionState"]
    end

    subgraph Storage["Storage"]
        EventStore["events/store.py\nEventStore (aiosqlite)"]
        DB[("lifeos.db\nSQLite")]
    end

    subgraph Stubs["Stubs (placeholder)"]
        Presence["stubs/presence.py"]
        Identity["stubs/identity.py"]
        Policy["stubs/policy.py"]
    end

    subgraph External["External Services"]
        OllamaServer["Ollama Server\nlocalhost:11434"]
    end

    Client -- "WebSocket frames\n(audio bytes / JSON)" --> WS
    Client -- "HTTP" --> HTTP_Health
    Client -- "HTTP" --> HTTP_Sessions
    Client -- "HTTP" --> HTTP_Timeline

    WS --> Handler
    Handler --> VAD
    Handler --> Audio
    Handler --> STT
    Handler --> GovLoop
    Handler --> TTS
    Handler --> Manager

    GovLoop --> Router
    GovLoop --> Manager
    GovLoop --> EventStore
    GovLoop --> Responder
    GovLoop --> Stubs

    Router --> Keywords
    Router --> Ollama
    Ollama --> OllamaServer
    Responder --> OllamaServer

    Manager --> EventStore
    Manager --> Models

    EventStore --> DB

    HTTP_Health --> FastAPI
    HTTP_Sessions --> EventStore
    HTTP_Sessions --> Manager
    HTTP_Timeline --> EventStore
```

---

## 2. Request Lifecycle — Audio Path

```mermaid
sequenceDiagram
    participant C as Browser Client
    participant H as ws/handler.py
    participant VAD as SimpleVAD
    participant STT as STTAdapter
    participant GL as governing_loop.py
    participant SM as SessionManager
    participant LLM as LLMResponder
    participant IR as IntentRouter
    participant ES as EventStore
    participant TTS as TTSAdapter

    C->>H: Binary audio chunk (PCM16)
    H->>VAD: process_chunk(float32)
    VAD-->>H: "speech" | "silence" | "speech_end"

    alt speech detected
        H->>SM: transition(LISTENING)
    end

    alt speech_end detected
        H->>STT: transcribe(audio_buffer) [run_in_executor]
        STT-->>H: transcript: str
        H->>C: {"type":"transcript", "text": ...}
        H->>GL: execute_governing_loop(transcript)
        GL->>SM: resolve presence / identity / policy [stubs]
        GL->>IR: classify(transcript)
        IR->>IR: keyword_match()
        opt no keyword match
            IR->>IR: ollama.classify()
        end
        IR-->>GL: IntentResult
        opt session active + non-lifecycle intent
            GL->>LLM: generate_response()
            LLM-->>GL: response_text
        end
        opt intent == START_SESSION
            GL->>SM: start_session()
            SM->>ES: insert_session() + append_event()
        end
        opt intent == END_SESSION
            GL->>SM: end_session()
            SM->>ES: update_session() + append_event()
            GL->>LLM: generate_session_summary()
            GL->>ES: update_session_summary()
        end
        GL-->>H: LoopContext
        H->>C: {"type":"session_started"|"session_completed"}
        H->>C: {"type":"intent", "response_text": ...}
        H->>TTS: synthesize(response_text) [run_in_executor]
        TTS-->>H: wav_bytes
        H->>C: Binary WAV audio
        H->>C: {"type":"audio_done"}
    end
```

---

## 3. Text / Tap-to-End Path

```mermaid
sequenceDiagram
    participant C as Browser Client
    participant H as ws/handler.py
    participant GL as governing_loop.py
    participant SM as SessionManager

    alt text_input message
        C->>H: {"type":"text_input", "text":"..."}
        H->>SM: transition(LISTENING)
        H->>C: {"type":"transcript", "text":"..."}
        H->>GL: execute_governing_loop(transcript)
    else end_session message (tap button)
        C->>H: {"type":"end_session"}
        H->>SM: transition(LISTENING)
        H->>GL: execute_governing_loop("(tap to end)", force_intent="END_SESSION")
    end

    GL-->>H: LoopContext
    H->>C: session lifecycle + intent JSON frames
```

---

## 4. Session FSM (Finite State Machine)

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> LISTENING : speech / text_input detected

    LISTENING --> PROCESSING : transcript ready
    LISTENING --> IDLE : VAD reset / no speech

    PROCESSING --> INTENT_RESOLVED : intent classified

    INTENT_RESOLVED --> SESSION_ACTIVE : START_SESSION intent
    INTENT_RESOLVED --> IDLE : no lifecycle action
    INTENT_RESOLVED --> LISTENING : continue in session

    SESSION_ACTIVE --> LISTENING : user speaks again
    SESSION_ACTIVE --> COMPLETING : END_SESSION intent
    SESSION_ACTIVE --> SESSION_INTERRUPTED : WebSocket disconnect

    SESSION_INTERRUPTED --> SESSION_ACTIVE : client reconnects
    SESSION_INTERRUPTED --> ABANDONED : timeout (300 s)

    COMPLETING --> COMPLETED : DB update done
    COMPLETED --> IDLE : cleanup

    ABANDONED --> IDLE : cleanup
```

---

## 5. 12-Step Governing Loop

```mermaid
flowchart TD
    S1["1️⃣ Input received\n(transcript)"]
    S2["2️⃣ Presence resolved\n(stub)"]
    S3["3️⃣ Identity validated\n(stub)"]
    S4["4️⃣ Memory retrieved\n(stub — empty dict)"]
    S5["5️⃣ Context evaluated\n(session state snapshot)"]
    S6["6️⃣ Policy checked\n(stub)"]
    S6_deny{"Policy\nallowed?"}
    S7["7️⃣ Intent classification\n(keyword → Ollama fallback)"]
    S8a["8️⃣ LLM response\n(if session active + non-lifecycle)"]
    S8b["8️⃣ Canned response\n(fallback)"]
    S9a["9️⃣ start_session()\n→ DB insert"]
    S9b["9️⃣ end_session()\n→ DB update"]
    S9c["9️⃣ No-op"]
    S10["🔟 Event logged\n(lifecycle events only)"]
    S11["1️⃣1️⃣ Memory written\n(session summary via LLM)"]
    S12["1️⃣2️⃣ Response ready\n→ return LoopContext"]

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S6_deny
    S6_deny -- "No" --> S12
    S6_deny -- "Yes" --> S7
    S7 --> S8a
    S7 --> S8b
    S8a --> S9a
    S8a --> S9b
    S8a --> S9c
    S8b --> S9a
    S8b --> S9b
    S8b --> S9c
    S9a --> S10
    S9b --> S11
    S9c --> S10
    S10 --> S12
    S11 --> S12
```

---

## 6. Module Dependency Graph

```mermaid
graph LR
    main --> config
    main --> voice_stt["voice/stt"]
    main --> voice_tts["voice/tts"]
    main --> events_store["events/store"]
    main --> session_manager["session/manager"]
    main --> intent_router["intent/router"]
    main --> intent_ollama["intent/ollama"]
    main --> llm_responder["llm/responder"]
    main --> routes_health["routes/health"]
    main --> routes_sessions["routes/sessions"]
    main --> routes_timeline["routes/timeline"]
    main --> ws_handler["ws/handler"]

    ws_handler --> voice_audio["voice/audio"]
    ws_handler --> voice_vad["voice/vad"]
    ws_handler --> session_models["session/models"]
    ws_handler --> governing_loop

    governing_loop --> intent_keywords["intent/keywords"]
    governing_loop --> intent_router
    governing_loop --> session_manager
    governing_loop --> events_store
    governing_loop --> stubs_presence["stubs/presence"]
    governing_loop --> stubs_identity["stubs/identity"]
    governing_loop --> stubs_policy["stubs/policy"]

    intent_router --> intent_keywords
    intent_router --> intent_ollama
    intent_ollama --> config

    llm_responder --> config
    voice_stt --> config
    voice_tts --> config
    voice_vad --> config
    voice_audio --> config

    session_manager --> session_models
    session_manager --> events_store
    session_manager --> config

    events_store --> session_models
```

---

## 7. Data Storage Schema

```mermaid
erDiagram
    SESSIONS {
        TEXT session_id PK
        TEXT status "active | completed | abandoned"
        TEXT session_type "default: focus"
        TEXT started_at
        TEXT completed_at
        TEXT abandoned_at
        INTEGER duration_ms
        TEXT intent_transcript
        TEXT completion_transcript
        TEXT summary
        TEXT created_at
    }

    EVENTS {
        TEXT event_id PK
        TEXT event_type "session.created | session.completed | session.interrupted | session.abandoned | session.resumed"
        TEXT session_id FK
        TEXT timestamp
        TEXT payload "JSON blob"
        TEXT idempotency_key UNIQUE
        TEXT created_at
    }

    TIMELINE_VIEW {
        TEXT entry_id
        TEXT session_id
        TEXT text
        TEXT timestamp
        TEXT type "completed | abandoned | other"
    }

    SESSIONS ||--o{ EVENTS : "has"
    SESSIONS ||--o{ TIMELINE_VIEW : "shown in"
```
