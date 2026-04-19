import pytest
from server.events.store import EventStore
from server.session.models import SessionRecord, SessionStatus


@pytest.mark.asyncio
async def test_append_and_read_timeline(event_store):
    await event_store.append_event(
        "session.completed", "test-session-1", {"duration_ms": 5000}
    )
    timeline = await event_store.get_timeline()
    assert len(timeline) == 1
    assert timeline[0]["text"] == "Completed 1 session"
    assert timeline[0]["type"] == "completed"


@pytest.mark.asyncio
async def test_abandoned_in_timeline(event_store):
    await event_store.append_event(
        "session.abandoned", "test-session-2", {"reason": "timeout"}
    )
    timeline = await event_store.get_timeline()
    assert len(timeline) == 1
    assert timeline[0]["text"] == "Session abandoned"
    assert timeline[0]["type"] == "abandoned"


@pytest.mark.asyncio
async def test_non_timeline_events_excluded(event_store):
    await event_store.append_event(
        "session.created", "test-session-3", {"started_at": "now"}
    )
    timeline = await event_store.get_timeline()
    assert len(timeline) == 0  # session.created is not in the timeline view


@pytest.mark.asyncio
async def test_session_crud(event_store):
    session = SessionRecord(
        session_id="s1",
        status=SessionStatus.ACTIVE,
        started_at="2024-01-01T00:00:00Z",
        intent_transcript="start",
    )
    await event_store.insert_session(session)

    sessions = await event_store.get_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
    assert sessions[0]["status"] == "active"

    session.status = SessionStatus.COMPLETED
    session.completed_at = "2024-01-01T00:05:00Z"
    session.duration_ms = 300000
    await event_store.update_session(session)

    detail = await event_store.get_session("s1")
    assert detail["status"] == "completed"
    assert detail["duration_ms"] == 300000


@pytest.mark.asyncio
async def test_get_nonexistent_session(event_store):
    result = await event_store.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_multiple_timeline_entries_ordered(event_store):
    import asyncio

    await event_store.append_event(
        "session.completed", "s1", {"duration_ms": 1000}
    )
    await asyncio.sleep(0.01)  # Ensure different timestamps
    await event_store.append_event(
        "session.completed", "s2", {"duration_ms": 2000}
    )
    timeline = await event_store.get_timeline()
    assert len(timeline) == 2
    # Newest first
    assert timeline[0]["session_id"] == "s2"
    assert timeline[0]["text"] == "Completed 2 sessions"
    assert timeline[1]["text"] == "Completed 1 session"
