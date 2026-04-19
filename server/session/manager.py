import asyncio
import logging
import uuid
from datetime import datetime, timezone

from server import config
from server.session.models import (
    InvalidTransition,
    NoActiveSession,
    SessionRecord,
    SessionState,
    SessionStatus,
)

logger = logging.getLogger(__name__)


class SessionManager:
    TRANSITIONS: dict[SessionState, set[SessionState]] = {
        # Voice-loop states (per-utterance processing cycle)
        SessionState.IDLE: {SessionState.LISTENING},
        SessionState.LISTENING: {SessionState.PROCESSING, SessionState.IDLE},
        SessionState.PROCESSING: {SessionState.INTENT_RESOLVED},
        SessionState.INTENT_RESOLVED: {
            SessionState.SESSION_ACTIVE,   
            SessionState.IDLE,           
            SessionState.LISTENING,       
        },
        # Session-level states
        SessionState.SESSION_ACTIVE: {
            SessionState.LISTENING,       
            SessionState.COMPLETING,
            SessionState.SESSION_INTERRUPTED,
        },
        SessionState.SESSION_INTERRUPTED: {
            SessionState.SESSION_ACTIVE,
            SessionState.ABANDONED,
        },
        SessionState.COMPLETING: {SessionState.COMPLETED},
        SessionState.COMPLETED: {SessionState.IDLE},
        SessionState.ABANDONED: {SessionState.IDLE},
    }

    def __init__(self, event_store):
        self.state: SessionState = SessionState.IDLE
        self.active_session: SessionRecord | None = None
        self.event_store = event_store
        self._interrupt_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _transition(self, to_state: SessionState):
        allowed = self.TRANSITIONS.get(self.state, set())
        if to_state not in allowed:
            raise InvalidTransition(
                f"Cannot transition from {self.state.value} to {to_state.value}"
            )
        logger.info("Session state: %s -> %s", self.state.value, to_state.value)
        self.state = to_state

    def transition(self, to_state: SessionState):
        """Public transition for voice-loop states driven by the handler."""
        self._transition(to_state)

    async def start_session(self, transcript: str) -> SessionRecord:
        async with self._lock:
            logger.debug("start_session: acquiring lock (state=%s)", self.state.value)
            now = datetime.now(timezone.utc).isoformat()
            session = SessionRecord(
                session_id=str(uuid.uuid4()),
                status=SessionStatus.ACTIVE,
                started_at=now,
                intent_transcript=transcript,
            )

            # Transition to SESSION_ACTIVE (caller must have driven state to INTENT_RESOLVED)
            self._transition(SessionState.SESSION_ACTIVE)
            self.active_session = session

            await self.event_store.insert_session(session)
            await self.event_store.append_event(
                event_type="session.created",
                session_id=session.session_id,
                payload={"intent_transcript": transcript, "started_at": now},
            )
            logger.debug("start_session: created %s, transcript=%r",
                         session.session_id, transcript[:80])
            return session

    async def end_session(self, transcript: str) -> SessionRecord:
        async with self._lock:
            logger.debug("end_session: acquiring lock (state=%s)", self.state.value)
            if not self.active_session:
                raise NoActiveSession("No active session to end")

            now = datetime.now(timezone.utc).isoformat()
            started = datetime.fromisoformat(self.active_session.started_at)
            duration_ms = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000
            )

            self.active_session.status = SessionStatus.COMPLETED
            self.active_session.completed_at = now
            self.active_session.duration_ms = duration_ms
            self.active_session.completion_transcript = transcript

            self._transition(SessionState.COMPLETING)

            await self.event_store.update_session(self.active_session)
            await self.event_store.append_event(
                event_type="session.completed",
                session_id=self.active_session.session_id,
                payload={
                    "duration_ms": duration_ms,
                    "completed_at": now,
                    "completion_transcript": transcript,
                },
            )

            completed = self.active_session
            self._transition(SessionState.COMPLETED)
            self.active_session = None
            self._transition(SessionState.IDLE)
            logger.debug("end_session: completed %s, duration=%dms",
                         completed.session_id, completed.duration_ms or 0)
            return completed

    async def handle_disconnect(self):
        async with self._lock:
            logger.debug("handle_disconnect: state=%s, session=%s",
                         self.state.value,
                         self.active_session.session_id if self.active_session else None)
            if self.state == SessionState.SESSION_ACTIVE:
                self._transition(SessionState.SESSION_INTERRUPTED)
                await self.event_store.append_event(
                    event_type="session.interrupted",
                    session_id=self.active_session.session_id if self.active_session else None,
                    payload={"reason": "websocket_disconnect"},
                )
                self._interrupt_task = asyncio.create_task(self._abandon_after_timeout())

    async def handle_reconnect(self):
        async with self._lock:
            if self.state == SessionState.SESSION_INTERRUPTED:
                if self._interrupt_task:
                    self._interrupt_task.cancel()
                    self._interrupt_task = None
                self._transition(SessionState.SESSION_ACTIVE)
                if self.active_session:
                    await self.event_store.append_event(
                        event_type="session.resumed",
                        session_id=self.active_session.session_id,
                        payload={},
                    )

    async def _abandon_after_timeout(self):
        try:
            await asyncio.sleep(config.SESSION_INTERRUPT_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        async with self._lock:
            if self.state == SessionState.SESSION_INTERRUPTED and self.active_session:
                now = datetime.now(timezone.utc).isoformat()
                started = datetime.fromisoformat(self.active_session.started_at)
                partial_ms = int(
                    (datetime.now(timezone.utc) - started).total_seconds() * 1000
                )

                self.active_session.status = SessionStatus.ABANDONED
                self.active_session.abandoned_at = now

                self._transition(SessionState.ABANDONED)

                await self.event_store.update_session(self.active_session)
                await self.event_store.append_event(
                    event_type="session.abandoned",
                    session_id=self.active_session.session_id,
                    payload={
                        "reason": "timeout",
                        "abandoned_at": now,
                        "partial_duration_ms": partial_ms,
                    },
                )

                self.active_session = None
                self._transition(SessionState.IDLE)
                logger.info("Session abandoned after timeout")

    async def shutdown(self):
        """Cancel any pending background tasks for clean server shutdown."""
        if self._interrupt_task and not self._interrupt_task.done():
            self._interrupt_task.cancel()
            try:
                await self._interrupt_task
            except asyncio.CancelledError:
                pass
            self._interrupt_task = None
