import logging
import re
from enum import Enum

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    START_SESSION = "START_SESSION"
    END_SESSION = "END_SESSION"
    REQUEST_GUIDANCE = "REQUEST_GUIDANCE"
    REQUEST_FINANCE = "REQUEST_FINANCE"
    UNCLEAR = "UNCLEAR"


# More specific patterns first within each group
KEYWORD_PATTERNS: list[tuple[list[str], Intent]] = [
    # START_SESSION — multi-word first
    (
        [
            "start session", "start a session", "begin session",
            "lets go", "let's go", "lets start", "let's start",
            "im ready", "i'm ready", "ready to start",
            "prepare",
        ],
        Intent.START_SESSION,
    ),
    (["start", "begin", "ready"], Intent.START_SESSION),
    # END_SESSION — multi-word first
    (
        [
            "end session", "finish session",
            "im done", "i'm done", "i am done",
            "session done", "stop session",
        ],
        Intent.END_SESSION,
    ),
    (["done", "finish", "end", "stop", "complete"], Intent.END_SESSION),
    # REQUEST_FINANCE — must be before guidance to catch "i need" patterns
    (
        [
            "i need $", "i need money", "need money",
            "ask my parent", "ask my parents",
            "send me money", "send money",
            "i need 10", "i need 20", "i need 30", "i need 40", "i need 50",
            "i need 100",
        ],
        Intent.REQUEST_FINANCE,
    ),
    # REQUEST_GUIDANCE
    (
        [
            "what should i do", "what can i do", "what do i have today",
            "whats my day", "what's my day", "what is my day",
            "what do i want to move forward",
            "show my timeline", "show timeline", "my timeline",
            "what did i do", "what have i done",
            "help", "what now",
        ],
        Intent.REQUEST_GUIDANCE,
    ),
]


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s']", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def keyword_match(transcript: str) -> Intent | None:
    normalized = normalize(transcript)
    logger.debug("Keyword match — normalized: %r", normalized)
    if not normalized:
        return None
    for patterns, intent in KEYWORD_PATTERNS:
        for pattern in patterns:
            if " " in pattern:
                # Multi-word patterns: substring match is safe
                if pattern in normalized:
                    logger.debug("Keyword matched: %r → %s (multi-word)", pattern, intent.value)
                    return intent
            else:
                # Single-word patterns: require word boundaries to prevent
                # false positives (e.g., "end" matching "understand")
                if re.search(r'\b' + re.escape(pattern) + r'\b', normalized):
                    logger.debug("Keyword matched: %r → %s (word-boundary)", pattern, intent.value)
                    return intent
    logger.debug("No keyword match for: %r", normalized)
    return None
