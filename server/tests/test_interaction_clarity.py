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


def test_guidance_prompt_response():
    router = IntentRouter()
    assert router._get_response(Intent.REQUEST_GUIDANCE, "idle") == "What do you want to move forward right now?"


def test_end_session_response():
    router = IntentRouter()
    assert router._get_response(Intent.END_SESSION, "session_active") == "Session complete. You moved something forward."


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
        == "I didn't catch that. You can say 'start session' or tell me what you want to move forward."
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
