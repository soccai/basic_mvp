import logging

from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health(request: Request):
    stt = getattr(request.app.state, "stt", None)
    tts = getattr(request.app.state, "tts", None)
    llm = getattr(request.app.state, "llm_responder", None)
    intent_router = getattr(request.app.state, "intent_router", None)
    ollama_available = bool(intent_router and intent_router.ollama)
    payload = {
        "status": "ok",
        "stt": "ready" if (stt and stt.ready) else "unavailable",
        "tts": "ready" if (tts and tts.ready) else "unavailable",
        "llm": "available" if llm else "unavailable",
        "ollama": "available" if ollama_available else "unavailable",
    }
    logger.debug("GET /health -> %s", payload)
    return payload
