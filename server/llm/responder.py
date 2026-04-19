import logging
import re

import httpx
from server import config

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Summarize this session as a validated memory record. Focus on:
- What the user intended and accomplished
- Any meaningful signals (decisions made, topics explored, guidance requested)
- Outcome or state at session end

Do NOT reproduce raw conversation. Do NOT list every utterance. Extract only what is meaningful, validated, and relevant to continuity.

Session type: {session_type}
Duration: {duration}
Started with: "{intent_transcript}"
Ended with: "{completion_transcript}"
{interactions_section}
Write 1-4 sentences. Be factual and structured. This is a memory record, not a chat log."""

RESPONSE_PROMPT = """You are the voice of a personal operating system called LifeOS.
You speak in brief, clear sentences suitable for voice output.
Keep responses under 2 sentences unless the user specifically asks for detail.
Be warm but not sycophantic. Be direct.

Current state: {session_state}
User's intent: {intent}

User said: "{transcript}"

Respond naturally to the user. Do not mention that you are an AI or a language model."""


MAX_TRANSCRIPT_LENGTH = 500


def _sanitize_transcript(text: str) -> str:
    """Truncate and strip control characters from user transcript before LLM interpolation."""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    if len(text) > MAX_TRANSCRIPT_LENGTH:
        text = text[:MAX_TRANSCRIPT_LENGTH] + "..."
    return text


def _sanitize_response(text: str) -> str:
    """Strip prompt-leakage artifacts that small models (e.g. phi3) emit."""
    for marker in ("\n---", "\n##", "\n**Instruction", "\n## Instruction"):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
    return text.strip()


class LLMResponder:
    def __init__(self):
        self.available: bool = False

    async def check_availability(self) -> bool:
        try:
            logger.debug(
                "Checking LLM availability at %s for model %s",
                config.OLLAMA_BASE_URL,
                config.OLLAMA_MODEL,
            )
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{config.OLLAMA_BASE_URL}/api/tags",
                    timeout=2.0,
                )
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_names = [m.get("name", "") for m in models]
                    self.available = any(
                        config.OLLAMA_MODEL in name for name in model_names
                    )
                    logger.debug("LLM availability result: available=%s models=%d", self.available, len(model_names))
                else:
                    self.available = False
                    logger.debug("LLM availability check returned HTTP %d", resp.status_code)
        except Exception:
            self.available = False
            logger.debug("LLM availability check failed", exc_info=True)
        return self.available

    async def generate_response(
        self,
        transcript: str,
        intent: str,
        session_state: str,
        context: dict | None = None,
    ) -> str | None:
        """
        Generate a contextual response via Ollama.
        Returns None on failure so caller falls back to canned responses.
        """
        if not self.available:
            logger.debug("Skipping LLM response generation: responder unavailable")
            return None

        safe_transcript = _sanitize_transcript(transcript)
        prompt = RESPONSE_PROMPT.format(
            transcript=safe_transcript,
            intent=intent,
            session_state=session_state,
        )

        try:
            logger.debug(
                "Generating LLM response: intent=%s state=%s transcript_chars=%d",
                intent,
                session_state,
                len(safe_transcript),
            )
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{config.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": config.OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 100,
                            "num_ctx": 2048,
                        },
                    },
                    timeout=config.OLLAMA_GENERATE_TIMEOUT_SECONDS,
                )
                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    text = _sanitize_response(text)
                    if text:
                        logger.debug("LLM response generated: %d chars", len(text))
                        return text
                    logger.debug("LLM response was empty after sanitization")
                else:
                    logger.debug("LLM response generation returned HTTP %d", resp.status_code)
        except httpx.TimeoutException:
            logger.debug("LLM response generation timed out")
        except Exception as e:
            logger.debug("LLM response generation failed: %s", e)

        return None

    async def generate_session_summary(
        self,
        session_type: str,
        duration_ms: int | None,
        intent_transcript: str,
        completion_transcript: str,
        interactions: list[dict] | None = None,
    ) -> str | None:
        """
        Generate a validated memory summary for a completed session.
        Interactions are ephemeral context — they inform the summary but are
        NOT stored themselves. Returns None on failure — caller stores a fallback.
        """
        if not self.available:
            logger.debug("Skipping session summary generation: responder unavailable")
            return None

        if duration_ms:
            seconds = duration_ms // 1000
            minutes = seconds // 60
            duration = f"{minutes}m {seconds % 60}s" if minutes else f"{seconds}s"
        else:
            duration = "unknown"

        # Build ephemeral interaction context for the prompt
        safe_intent = _sanitize_transcript(intent_transcript or "(none)")
        safe_completion = _sanitize_transcript(completion_transcript or "(none)")

        interactions_section = ""
        if interactions:
            lines = []
            for i in interactions:
                safe_t = _sanitize_transcript(i['transcript'])
                lines.append(f"- [{i['intent']}] \"{safe_t}\"")
            interactions_section = f"Interactions during session ({len(interactions)} total):\n" + "\n".join(lines)

        prompt = SUMMARY_PROMPT.format(
            session_type=session_type,
            duration=duration,
            intent_transcript=safe_intent,
            completion_transcript=safe_completion,
            interactions_section=interactions_section,
        )

        try:
            logger.debug(
                "Generating session summary: session_type=%s duration=%s interactions=%d prompt_chars=%d",
                session_type,
                duration,
                len(interactions or []),
                len(prompt),
            )
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{config.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": config.OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 150,
                        },
                    },
                    timeout=config.OLLAMA_SUMMARY_TIMEOUT_SECONDS,
                )
                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    text = _sanitize_response(text)
                    if text:
                        logger.debug("Session summary generated: %d chars", len(text))
                        return text
                    logger.debug("Session summary was empty after sanitization")
                else:
                    logger.debug("Session summary generation returned HTTP %d", resp.status_code)
        except httpx.TimeoutException:
            logger.warning("Session summary generation timed out after %ds", config.OLLAMA_SUMMARY_TIMEOUT_SECONDS)
        except Exception as e:
            logger.debug("Session summary generation failed: %s", e)

        return None
