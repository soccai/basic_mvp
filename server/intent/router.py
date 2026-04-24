import logging
import random
from dataclasses import dataclass
from server.intent.keywords import Intent, keyword_match
from server import config

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    intent: Intent
    transcript: str
    method: str
    response_text: str


class IntentRouter:
    def __init__(self, ollama_client=None):
        self.ollama = ollama_client
        self.last_conversation_response: str | None = None

    @staticmethod
    def _pick_without_immediate_repeat(options: list[str], last_value: str | None) -> str:
        if len(options) <= 1:
            return options[0] if options else ""
        filtered = [option for option in options if option != last_value]
        pool = filtered if filtered else options
        return random.choice(pool)

    async def classify(self, transcript: str, current_state: str) -> IntentResult:
        logger.debug("Classifying transcript: %r (state=%s)", transcript[:80], current_state)
        intent = keyword_match(transcript)
        method = "keyword"

        if intent is None and current_state == "session_active":
            intent = Intent.REQUEST_GUIDANCE
            method = "session_default"
        elif intent is None and self.ollama and config.INTENT_LLM_FALLBACK_ENABLED:
            logger.debug("No keyword match — trying Ollama fallback")
            intent = await self.ollama.classify(transcript)
            method = "llm" if intent else "keyword"

        if intent is None:
            intent = Intent.UNCLEAR

        response_text = self._get_response(intent, current_state, transcript)
        logger.debug("Classification result: intent=%s, method=%s, response=%r",
                     intent.value, method, response_text[:80])

        return IntentResult(
            intent=intent,
            transcript=transcript,
            method=method,
            response_text=response_text,
        )

    def _get_response(self, intent: Intent, state: str, transcript: str = "") -> str:
        if intent == Intent.START_SESSION:
            if state == "session_active":
                return "A session is already active."
            return "Starting your session."
        elif intent == Intent.END_SESSION:
            if state not in ("session_active", "completing"):
                return "No active session to end."
            return "You moved something forward. Take a pause."
        elif intent == Intent.REQUEST_GUIDANCE:
            if state == "session_active":
                return "I'm listening. Tell me more about what you're working through."
            return "I'd love to help with that. Start a session and we can dig into it together."
        elif intent == Intent.READ_EMAIL:
            return """I don't have access to your emails yet, but here's how I would help. 
                    "Once connected, I can read your unread messages, summarize them, "
                    "and let you reply by voice. For now, you can check your inbox directly."""
        elif intent == Intent.REQUEST_FINANCE:
            return "Finance ain't supported now."
        elif intent == Intent.CONVERSATION:
            normalized = transcript.lower().strip()
            if "good morning" in normalized:
                return "Good morning. Ready when you are."
            if "good afternoon" in normalized:
                return "Good afternoon. What feels important right now?"
            if "good evening" in normalized or "good night" in normalized:
                return "Good evening. Want to close one thing before you wind down?"

            options = [
                "Nice to hear from you. Want to start a quick session?",
                "I'm here. Say 'start' when you want to focus.",
                "Want to move one thing forward right now?",
            ]
            response = self._pick_without_immediate_repeat(options, self.last_conversation_response)
            self.last_conversation_response = response
            return response
        else:
            return "I can help with that in a session. Say 'start' when you're ready, or just say hi."
