import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server import config
from server.voice.stt import STTAdapter
from server.voice.tts import TTSAdapter
from server.events.store import EventStore
from server.session.manager import SessionManager
from server.intent.router import IntentRouter
from server.intent.ollama import OllamaClient
from server.llm.responder import LLMResponder
from server.routes import health, timeline, sessions, tts
from server.ws.handler import websocket_handler
from server.ws.connection_gate import ConnectionGate

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()

    # Load STT
    stt = STTAdapter()
    try:
        stt.load()
    except Exception as e:
        logger.error("STT failed to load: %s", e)
    app.state.stt = stt

    # Load TTS
    tts = TTSAdapter()
    try:
        tts.load()
    except Exception as e:
        logger.error("TTS failed to load: %s", e)
    app.state.tts = tts

    # Init event store
    event_store = EventStore(str(config.DB_PATH))
    await event_store.initialize()
    app.state.event_store = event_store

    # Init session manager
    app.state.session_manager = SessionManager(event_store)

    # Init connection gate (single-connection enforcement)
    app.state.connection_gate = ConnectionGate()

    # Init intent router with optional Ollama
    ollama = OllamaClient()
    await ollama.check_availability()
    app.state.intent_router = IntentRouter(ollama_client=ollama if ollama.available else None)

    # Init LLM responder for contextual response generation
    llm_responder = LLMResponder()
    await llm_responder.check_availability()
    app.state.llm_responder = llm_responder if llm_responder.available else None

    logger.info(
        "LifeOS Voice Server ready — STT: %s, TTS: %s, Ollama: %s, LLM: %s",
        "ready" if stt.ready else "unavailable",
        "ready" if tts.ready else "unavailable",
        "available" if ollama.available else "unavailable",
        "available" if llm_responder.available else "unavailable",
    )

    yield

    await app.state.connection_gate.shutdown()
    await app.state.session_manager.shutdown()
    await event_store.close()


app = FastAPI(title="LifeOS Voice Server", lifespan=lifespan)

_cors_origins = [
    o.strip() for o in
    os.environ.get("LIFEOS_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health.router)
app.include_router(timeline.router)
app.include_router(sessions.router)
app.include_router(tts.router)


# WebSocket
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket_handler(websocket)


# Static files — must be last so it doesn't shadow API routes
client_dir = config.PROJECT_ROOT / "client"
if client_dir.exists():
    app.mount("/", StaticFiles(directory=str(client_dir), html=True), name="client")
