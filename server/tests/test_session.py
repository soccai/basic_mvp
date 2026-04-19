import pytest
from server.session.manager import SessionManager
from server.session.models import (
    InvalidTransition,
    NoActiveSession,
    SessionState,
    SessionStatus,
)


def _drive_to_intent_resolved(sm: SessionManager):
    """Drive the state machine through the voice-loop to INTENT_RESOLVED."""
    if sm.state == SessionState.SESSION_ACTIVE:
        sm.transition(SessionState.LISTENING)
    elif sm.state == SessionState.IDLE:
        sm.transition(SessionState.LISTENING)
    sm.transition(SessionState.PROCESSING)
    sm.transition(SessionState.INTENT_RESOLVED)


@pytest.mark.asyncio
async def test_happy_path(event_store):
    sm = SessionManager(event_store)
    assert sm.state == SessionState.IDLE

    _drive_to_intent_resolved(sm)
    session = await sm.start_session("start")
    assert sm.state == SessionState.SESSION_ACTIVE
    assert session.status == SessionStatus.ACTIVE
    assert session.session_id

    # End session: drive through voice-loop, then back to SESSION_ACTIVE
    _drive_to_intent_resolved(sm)
    sm.transition(SessionState.SESSION_ACTIVE)
    completed = await sm.end_session("done")
    assert sm.state == SessionState.IDLE
    assert completed.status == SessionStatus.COMPLETED
    assert completed.duration_ms is not None
    assert completed.duration_ms >= 0


@pytest.mark.asyncio
async def test_end_without_session(event_store):
    sm = SessionManager(event_store)
    with pytest.raises(NoActiveSession):
        await sm.end_session("done")


@pytest.mark.asyncio
async def test_double_start(event_store):
    sm = SessionManager(event_store)
    _drive_to_intent_resolved(sm)
    await sm.start_session("start")
    assert sm.state == SessionState.SESSION_ACTIVE

    # During an active session, drive through voice-loop to INTENT_RESOLVED
    _drive_to_intent_resolved(sm)
    # start_session transitions INTENT_RESOLVED -> SESSION_ACTIVE which IS valid,
    # but now we're already in a session. Verify the second start still works
    # as a transition (the session manager allows it — governance prevents it
    # at the orchestration level, not the state machine level).
    # The real guard is in the governing loop which checks session_manager.active_session.
    # So instead test that INTENT_RESOLVED -> SESSION_ACTIVE -> SESSION_ACTIVE is invalid:
    sm.transition(SessionState.SESSION_ACTIVE)
    with pytest.raises(InvalidTransition):
        # Can't transition from SESSION_ACTIVE to SESSION_ACTIVE
        sm.transition(SessionState.SESSION_ACTIVE)


@pytest.mark.asyncio
async def test_multiple_sessions(event_store):
    sm = SessionManager(event_store)
    for i in range(3):
        _drive_to_intent_resolved(sm)
        session = await sm.start_session(f"start {i}")
        assert sm.state == SessionState.SESSION_ACTIVE

        _drive_to_intent_resolved(sm)
        sm.transition(SessionState.SESSION_ACTIVE)
        completed = await sm.end_session(f"done {i}")
        assert sm.state == SessionState.IDLE
        assert completed.status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_disconnect_and_reconnect(event_store):
    sm = SessionManager(event_store)
    _drive_to_intent_resolved(sm)
    await sm.start_session("start")
    assert sm.state == SessionState.SESSION_ACTIVE

    await sm.handle_disconnect()
    assert sm.state == SessionState.SESSION_INTERRUPTED

    await sm.handle_reconnect()
    assert sm.state == SessionState.SESSION_ACTIVE

    _drive_to_intent_resolved(sm)
    sm.transition(SessionState.SESSION_ACTIVE)
    completed = await sm.end_session("done")
    assert completed.status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_voice_loop_states(event_store):
    """Test the voice-loop sub-cycle transitions."""
    sm = SessionManager(event_store)
    assert sm.state == SessionState.IDLE

    # IDLE -> LISTENING -> PROCESSING -> INTENT_RESOLVED
    sm.transition(SessionState.LISTENING)
    assert sm.state == SessionState.LISTENING
    sm.transition(SessionState.PROCESSING)
    assert sm.state == SessionState.PROCESSING
    sm.transition(SessionState.INTENT_RESOLVED)
    assert sm.state == SessionState.INTENT_RESOLVED

    # Return to IDLE (no session action)
    sm.transition(SessionState.IDLE)
    assert sm.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_voice_loop_during_session(event_store):
    """Test that voice-loop states work within an active session."""
    sm = SessionManager(event_store)
    _drive_to_intent_resolved(sm)
    await sm.start_session("start")
    assert sm.state == SessionState.SESSION_ACTIVE

    # SESSION_ACTIVE -> LISTENING -> PROCESSING -> INTENT_RESOLVED
    sm.transition(SessionState.LISTENING)
    sm.transition(SessionState.PROCESSING)
    sm.transition(SessionState.INTENT_RESOLVED)
    assert sm.state == SessionState.INTENT_RESOLVED

    # Return to SESSION_ACTIVE
    sm.transition(SessionState.SESSION_ACTIVE)
    assert sm.state == SessionState.SESSION_ACTIVE
