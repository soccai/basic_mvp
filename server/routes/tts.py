import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.post("/tts")
async def synthesize(request: Request):
    tts = getattr(request.app.state, "tts", None)
    if not tts or not tts.ready:
        logger.debug("POST /api/tts -> 503 (tts unavailable)")
        return Response(status_code=503, content=b"TTS unavailable")

    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        logger.debug("POST /api/tts -> 400 (empty text)")
        return Response(status_code=400, content=b"Empty text")

    try:
        logger.debug("POST /api/tts text chars=%d", len(text))
        loop = asyncio.get_running_loop()
        wav_bytes = await loop.run_in_executor(None, tts.synthesize, text)
        if not wav_bytes:
            logger.warning("POST /api/tts: Piper returned empty bytes for text: %r", text[:80])
            return Response(status_code=503, content=b"TTS returned empty audio")
        logger.debug("POST /api/tts -> 200 (%d WAV bytes)", len(wav_bytes))
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        logger.error("POST /api/tts synthesis failed: %s", e)
        return Response(status_code=503, content=b"TTS synthesis error")
