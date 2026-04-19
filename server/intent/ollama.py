import logging
import httpx
from server.intent.keywords import Intent
from server import config

logger = logging.getLogger(__name__)

INTENT_PROMPT = """Classify the user's intent into exactly one of:
- START_SESSION (user wants to begin a session)
- END_SESSION (user wants to finish the current session)
- REQUEST_GUIDANCE (user is asking what to do)
- UNCLEAR (cannot determine intent)

User said: "{transcript}"

Respond with ONLY the intent name, nothing else."""


class OllamaClient:
    def __init__(self):
        self.available = False

    async def check_availability(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{config.OLLAMA_BASE_URL}/api/tags",
                    timeout=2.0,
                )
                self.available = resp.status_code == 200
        except Exception:
            self.available = False
        return self.available

    async def classify(self, transcript: str) -> Intent | None:
        if not self.available:
            return None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{config.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": config.OLLAMA_MODEL,
                        "prompt": INTENT_PROMPT.format(transcript=transcript),
                        "stream": False,
                    },
                    timeout=config.OLLAMA_TIMEOUT_SECONDS,
                )
                if resp.status_code == 200:
                    result = resp.json()["response"].strip().upper()
                    try:
                        return Intent(result)
                    except ValueError:
                        return None
        except Exception as e:
            logger.debug("Ollama classify failed: %s", e)
            return None
