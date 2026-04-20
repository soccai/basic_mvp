import logging
import re

import httpx
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_ollama import ChatOllama, OllamaLLM

from server import config

logger = logging.getLogger(__name__)

# ---------- System prompts ----------

GENERIC_RESPONSE_SYSTEM = (
    "You are the voice of a personal operating system called LifeOS.\n"
    "You speak in brief, clear sentences suitable for voice output.\n"
    "Keep responses under 2 sentences unless the user specifically asks for detail.\n"
    "Be warm but not sycophantic. Be direct.\n"
    "Do not mention that you are an AI or a language model.\n\n"
    "Current state: {session_state}\n"
    "User's intent: {intent}"
)

GUIDED_CLARITY_SYSTEM = (
    "You are the voice of a personal operating system called LifeOS.\n"
    "You are in a guided session helping the user work through a challenge.\n\n"
    "Your approach based on the conversation so far:\n"
    "- If this is the user's FIRST message: Ask 1-2 sharp clarifying questions. "
    "Do NOT give solutions yet.\n"
    "- If you have asked questions and the user has answered: Identify the core issue "
    "in one sentence, then suggest 1-2 concrete actionable next steps.\n"
    "- If next steps were already given: Reinforce, adjust, or help the user act.\n\n"
    "Rules:\n"
    "- Keep every response under 3 sentences. This is voice output.\n"
    "- Never list more than 2 questions or 2 action items.\n"
    "- Be warm but direct. No filler.\n"
    "- Do not mention that you are an AI or a language model.\n\n"
    "Current state: {session_state}\n"
    "User's intent: {intent}\n"
    "Turn number in this session: {turn_number}"
)

SUMMARY_PROMPT = PromptTemplate.from_template(
    "Summarize this session as a structured memory record.\n\n"
    "Extract and format:\n"
    "- GOAL: What challenge or problem the user brought (1 sentence)\n"
    "- QUESTIONS: Key clarifying questions asked and user's answers (2-3 bullet points max)\n"
    "- ACTIONS: Concrete next steps agreed upon (1-2 bullet points)\n"
    "- FOLLOW_UP: Whether follow-up is needed and on what (1 sentence, or \"None\")\n\n"
    "Session type: {session_type}\n"
    "Duration: {duration}\n"
    "Started with: \"{intent_transcript}\"\n"
    "Ended with: \"{completion_transcript}\"\n"
    "{interactions_section}\n\n"
    "Write in factual, structured format. This is a memory record, not a chat log.\n"
    "Keep total length under 200 words."
)

# ---------- Sanitization guards ----------

MAX_TRANSCRIPT_LENGTH = 500
MAX_CONVERSATION_TURNS = 6


def _sanitize_transcript(text: str) -> str:
    """Truncate and strip control characters from user transcript before LLM interpolation."""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    if len(text) > MAX_TRANSCRIPT_LENGTH:
        text = text[:MAX_TRANSCRIPT_LENGTH] + "..."
    return text


def _sanitize_response(text: str) -> str:
    """Strip prompt-leakage artifacts that small models (e.g. phi3) emit."""
    for marker in ("\n---", "\n**Instruction", "\n## Instruction"):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
    return text.strip()


def _sanitize_summary(text: str) -> str:
    """Light sanitization for structured summaries — preserves section headers."""
    for marker in ("\n**Instruction", "\n## Instruction"):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
    return text.strip()


# ---------- LLMResponder ----------

class LLMResponder:
    def __init__(self):
        self.available: bool = False
        self._response_llm: ChatOllama | None = None
        self._summary_chain = None

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

        if self.available:
            # ChatOllama for responses (needs multi-turn message format)
            self._response_llm = ChatOllama(
                model=config.OLLAMA_MODEL,
                base_url=config.OLLAMA_BASE_URL,
                timeout=config.OLLAMA_GENERATE_TIMEOUT_SECONDS,
                temperature=0.7,
                num_predict=100,
                num_ctx=2048,
            )
            # OllamaLLM for summaries (single-shot, uses /api/generate — faster)
            summary_llm = OllamaLLM(
                model=config.OLLAMA_MODEL,
                base_url=config.OLLAMA_BASE_URL,
                timeout=config.OLLAMA_SUMMARY_TIMEOUT_SECONDS,
                temperature=0.3,
                num_predict=150,
            )
            self._summary_chain = SUMMARY_PROMPT | summary_llm | StrOutputParser()

        return self.available

    async def generate_response(
        self,
        transcript: str,
        intent: str,
        session_state: str,
        context: dict | None = None,
        conversation_history: list[dict] | None = None,
        memory: dict | None = None,
    ) -> str | None:
        """
        Generate a contextual response via LangChain + Ollama.
        Returns None on failure so caller falls back to canned responses.
        """
        if not self.available or not self._response_llm:
            logger.debug("Skipping LLM response generation: responder unavailable")
            return None

        safe_transcript = _sanitize_transcript(transcript)
        turn_number = len(conversation_history) if conversation_history else 1

        # Select system prompt: guided clarity during early session turns, generic otherwise
        if session_state == "session_active" and turn_number <= 4:
            system_text = GUIDED_CLARITY_SYSTEM.format(
                session_state=session_state,
                intent=intent,
                turn_number=turn_number,
            )
        else:
            system_text = GENERIC_RESPONSE_SYSTEM.format(
                session_state=session_state,
                intent=intent,
            )

        # Append previous session context if available
        if memory and memory.get("recent_sessions"):
            summaries = memory["recent_sessions"]
            memory_lines = [f"- {s['summary']}" for s in summaries if s.get("summary")]
            if memory_lines:
                system_text += "\n\nPrevious session context:\n" + "\n".join(memory_lines)

        # Build message list directly (avoids per-call ChatPromptTemplate construction)
        messages = [SystemMessage(content=system_text)]

        if conversation_history and len(conversation_history) > 1:
            prior_turns = conversation_history[-(MAX_CONVERSATION_TURNS + 1):-1]
            for turn in prior_turns:
                messages.append(HumanMessage(content=_sanitize_transcript(turn["transcript"])))
                if "response" in turn:
                    messages.append(AIMessage(content=turn["response"]))

        messages.append(HumanMessage(content=safe_transcript))

        try:
            logger.debug(
                "Generating LLM response: intent=%s state=%s turn=%d history=%d",
                intent, session_state, turn_number,
                len(conversation_history) if conversation_history else 0,
            )
            result = await self._response_llm.ainvoke(messages)
            text = _sanitize_response(result.content)
            if text:
                logger.debug("LLM response generated: %d chars", len(text))
                return text
            logger.debug("LLM response was empty after sanitization")
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
        if not self.available or not self._summary_chain:
            logger.debug("Skipping session summary generation: responder unavailable")
            return None

        if duration_ms:
            seconds = duration_ms // 1000
            minutes = seconds // 60
            duration = f"{minutes}m {seconds % 60}s" if minutes else f"{seconds}s"
        else:
            duration = "unknown"

        safe_intent = _sanitize_transcript(intent_transcript or "(none)")
        safe_completion = _sanitize_transcript(completion_transcript or "(none)")

        interactions_section = ""
        if interactions:
            lines = []
            for i in interactions:
                safe_t = _sanitize_transcript(i['transcript'])
                lines.append(f"- [{i['intent']}] \"{safe_t}\"")
            interactions_section = f"Interactions during session ({len(interactions)} total):\n" + "\n".join(lines)

        try:
            logger.debug(
                "Generating session summary: session_type=%s duration=%s interactions=%d",
                session_type, duration, len(interactions or []),
            )
            text = await self._summary_chain.ainvoke({
                "session_type": session_type,
                "duration": duration,
                "intent_transcript": safe_intent,
                "completion_transcript": safe_completion,
                "interactions_section": interactions_section,
            })
            text = _sanitize_summary(text)
            if text:
                logger.debug("Session summary generated: %d chars", len(text))
                return text
            logger.debug("Session summary was empty after sanitization")
        except Exception as e:
            logger.debug("Session summary generation failed: %s", e)

        return None
