from dataclasses import dataclass


@dataclass
class Event:
    event_id: str
    event_type: str
    session_id: str | None
    timestamp: str
    payload: dict
    idempotency_key: str
