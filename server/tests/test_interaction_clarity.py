import pytest

from server.governing_loop import execute_governing_loop
from server.intent.keywords import Intent
from server.intent.router import IntentRouter
from server.session.manager import SessionManager
from server.session.models import SessionState


class StubLLMResponder:
    async def generate_response(self, **kwargs):
        return "This should not replace the unclear fallback."


class SessionLLMResponder:
    async def generate_response(self, **kwargs):
        return "Let's keep moving on that."


class FallbackSummaryResponder:
    async def generate_response(self, **kwargs):
        return "Let's keep moving on that."

    async def generate_session_summary(self, **kwargs):
        return None


def test_guidance_prompt_response():
    router = IntentRouter()
    assert router._get_response(Intent.REQUEST_GUIDANCE, "idle") == "Start a session and we'll get into it."


def test_end_session_response():
    router = IntentRouter()
    assert router._get_response(Intent.END_SESSION, "session_active") == "Done. Take a beat."


@pytest.mark.asyncio
async def test_unclear_fallback_stays_deterministic(event_store):
    session_manager = SessionManager(event_store)
    intent_router = IntentRouter()
    llm_responder = StubLLMResponder()

    session_manager.transition(SessionState.LISTENING)
    session_manager.transition(SessionState.PROCESSING)
    session_manager.transition(SessionState.INTENT_RESOLVED)

    ctx = await execute_governing_loop(
        transcript="banana",
        session_manager=session_manager,
        intent_router=intent_router,
        event_store=event_store,
        llm_responder=llm_responder,
    )

    assert (
        ctx.response_text
        == "Didn't catch that. Say 'start' or just talk to me."
    )


@pytest.mark.asyncio
async def test_active_session_uses_llm_response(event_store):
    session_manager = SessionManager(event_store)
    intent_router = IntentRouter()
    llm_responder = SessionLLMResponder()

    session_manager.transition(SessionState.LISTENING)
    session_manager.transition(SessionState.PROCESSING)
    session_manager.transition(SessionState.INTENT_RESOLVED)
    await session_manager.start_session("start")

    session_manager.transition(SessionState.LISTENING)
    session_manager.transition(SessionState.PROCESSING)
    session_manager.transition(SessionState.INTENT_RESOLVED)

    ctx = await execute_governing_loop(
        transcript="banana",
        session_manager=session_manager,
        intent_router=intent_router,
        event_store=event_store,
        llm_responder=llm_responder,
    )

    assert ctx.response_text == "Let's keep moving on that."


@pytest.mark.asyncio
async def test_end_session_stores_structured_fallback_summary(event_store):
    session_manager = SessionManager(event_store)
    intent_router = IntentRouter()
    llm_responder = FallbackSummaryResponder()

    session_manager.transition(SessionState.LISTENING)
    session_manager.transition(SessionState.PROCESSING)
    session_manager.transition(SessionState.INTENT_RESOLVED)
    await session_manager.start_session("start session to fix sleep")

    session_manager.active_session.interactions.append({
        "transcript": "I keep staying up too late.",
        "intent": Intent.REQUEST_GUIDANCE.value,
        "response": "Pick one shutdown time tonight and stick to it.",
    })

    session_manager.transition(SessionState.LISTENING)
    session_manager.transition(SessionState.PROCESSING)
    session_manager.transition(SessionState.INTENT_RESOLVED)

    await execute_governing_loop(
        transcript="terminate the session",
        session_manager=session_manager,
        intent_router=intent_router,
        event_store=event_store,
        llm_responder=llm_responder,
    )

    sessions = await event_store.get_sessions()
    assert len(sessions) == 1
    summary = sessions[0]["summary"]
    assert "GOAL:" in summary
    assert "QUESTIONS:" in summary
    assert "ACTIONS:" in summary
    assert "FOLLOW_UP:" in summary
