from dataclasses import dataclass, field
from enum import Enum


class SessionState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    INTENT_RESOLVED = "intent_resolved"
    SESSION_ACTIVE = "session_active"
    COMPLETING = "completing"
    COMPLETED = "completed"
    SESSION_INTERRUPTED = "session_interrupted"
    ABANDONED = "abandoned"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass
class SessionRecord:
    session_id: str
    status: SessionStatus
    session_type: str = "focus"
    started_at: str = ""
    completed_at: str | None = None
    abandoned_at: str | None = None
    duration_ms: int | None = None
    intent_transcript: str = ""
    completion_transcript: str | None = None
    # Ephemeral interaction log, its not being stored.
    interactions: list[dict] = field(default_factory=list)


class InvalidTransition(Exception):
    pass


class NoActiveSession(Exception):
    pass
