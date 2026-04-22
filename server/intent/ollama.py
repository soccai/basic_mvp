import logging

import httpx
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import OllamaLLM

from server.intent.keywords import Intent
from server import config

logger = logging.getLogger(__name__)

INTENT_PROMPT = PromptTemplate.from_template(
    "Classify the user's intent into exactly one of:\n"
    "- START_SESSION (user wants to begin a session)\n"
    "- END_SESSION (user wants to finish the current session)\n"
    "- REQUEST_GUIDANCE (user is asking what to do)\n"
    "- READ_EMAIL (user wants to check, read, or manage their emails)\n"
    "- CONVERSATION (user is greeting, making small talk, or being casual)\n"
    "- UNCLEAR (cannot determine intent)\n\n"
    "User said: \"{transcript}\"\n\n"
    "Respond with ONLY the intent name, nothing else."
)


class OllamaClient:
    def __init__(self):
        self.available = False
        self._chain = None

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

        if self.available:
            llm = OllamaLLM(
                model=config.OLLAMA_MODEL,
                base_url=config.OLLAMA_BASE_URL,
                timeout=config.OLLAMA_TIMEOUT_SECONDS,
                temperature=0.0,
                num_predict=10,
            )
            self._chain = INTENT_PROMPT | llm | StrOutputParser()

        return self.available

    async def classify(self, transcript: str) -> Intent | None:
        if not self.available or not self._chain:
            return None
        try:
            result = await self._chain.ainvoke({"transcript": transcript})
            result = result.strip().upper()
            return Intent(result)
        except ValueError:
            logger.debug("Ollama returned unrecognized intent: %r", result)
            return None
        except Exception as e:
            logger.debug("Ollama classify failed: %s", e)
            return None
