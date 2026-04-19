import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from server.intent.keywords import Intent
from server.intent.router import IntentResult, IntentRouter
from server.session.manager import SessionManager
from server.session.models import InvalidTransition, SessionState
from server.stubs.presence import resolve_presence
from server.stubs.identity import verify_identity
from server.stubs.policy import check_policy
from server.events.store import EventStore

logger = logging.getLogger(__name__)


@dataclass
class LoopContext:
    """Carries data through the 12 governing loop steps."""
    transcript: str
    force_intent: str | None = None
    presence: dict = field(default_factory=dict)
    identity: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)
    intent_result: IntentResult | None = None
    response_text: str = ""
    session_event: dict | None = None  # session lifecycle event for the handler


async def execute_governing_loop(
    transcript: str,
    session_manager: SessionManager,
    intent_router: IntentRouter,
    event_store: EventStore,
    llm_responder=None,
    force_intent: str | None = None,
) -> LoopContext:
    """
    Execute the 12-step governing loop for a single utterance.

    Steps:
      1. Input received
      2. Presence resolved
      3. Identity validated
      4. Memory retrieved
      5. Context evaluated
      6. Policy checked
      7. Orchestration decision (intent classification)
      8. LLM call (optional response generation)
      9. Engine execution (session start/end)
     10. Event logged
     11. Memory written
     12. Response ready
    """
    ctx = LoopContext(transcript=transcript, force_intent=force_intent)

    # Step 1: Input received
    logger.info("Loop [1/12] Input: %r", transcript[:80])

    # Step 2: Presence resolved
    ctx.presence = await resolve_presence()
    logger.debug("Loop [2/12] Presence: %s", ctx.presence)

    # Step 3: Identity validated
    ctx.identity = await verify_identity()
    logger.debug("Loop [3/12] Identity: %s", ctx.identity)

    # Step 4: Memory retrieved (stub)
    ctx.memory = {}
    logger.debug("Loop [4/12] Memory: (stub, empty)")

    # Step 5: Context evaluated
    ctx.context = {
        "session_state": session_manager.state.value,
        "has_active_session": session_manager.active_session is not None,
        "session_id": (
            session_manager.active_session.session_id
            if session_manager.active_session else None
        ),
        "presence": ctx.presence,
    }
    logger.debug("Loop [5/12] Context: %s", ctx.context)

    # Step 6: Policy checked
    ctx.policy = await check_policy(action="voice_interaction")
    logger.debug("Loop [6/12] Policy: %s", ctx.policy)
    if not ctx.policy.get("allowed", True):
        ctx.response_text = "Action not permitted."
        logger.warning("Loop [6/12] Policy denied")
        return ctx

    # Step 7: Orchestration decision (intent classification)
    if force_intent:
        ctx.intent_result = IntentResult(
            intent=Intent(force_intent),
            transcript=transcript,
            method="tap",
            response_text="",
        )
    else:
        ctx.intent_result = await intent_router.classify(
            transcript, session_manager.state.value
        )
    logger.info("Loop [7/12] Intent: %s (%s)",
                ctx.intent_result.intent.value, ctx.intent_result.method)

    # Collect ephemeral interaction during active session (in-memory only)
    if session_manager.active_session and ctx.intent_result.intent != Intent.END_SESSION:
        session_manager.active_session.interactions.append({
            "transcript": transcript,
            "intent": ctx.intent_result.intent.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.debug("Loop: ephemeral interaction appended (%d total)",
                     len(session_manager.active_session.interactions))

    # Step 8: LLM response generation.
    # Outside a session: keep idle guidance deterministic (canned responses).
    # Inside a session: route all non-lifecycle intents through the LLM so
    # the user can have natural conversation, not just canned replies.
    _lifecycle_intents = {Intent.START_SESSION, Intent.END_SESSION}
    if (
        llm_responder
        and session_manager.active_session
        and ctx.intent_result.intent not in _lifecycle_intents
    ):
        logger.debug("Loop [8/12] Requesting LLM response (intent=%s, state=%s)",
                     ctx.intent_result.intent.value, session_manager.state.value)
        generated = await llm_responder.generate_response(
            transcript=transcript,
            intent=ctx.intent_result.intent.value,
            session_state=session_manager.state.value,
            context=ctx.context,
        )
        if generated:
            ctx.response_text = generated
            logger.info("Loop [8/12] LLM response generated (%d chars)", len(generated))
        else:
            logger.debug("Loop [8/12] LLM returned nothing — will use canned")
    else:
        logger.debug("Loop [8/12] Skipping LLM (no responder=%s, active_session=%s, lifecycle=%s)",
                     llm_responder is not None,
                     session_manager.active_session is not None,
                     ctx.intent_result.intent in _lifecycle_intents)

    # Fallback to canned response
    if not ctx.response_text:
        response_state = (
            SessionState.SESSION_ACTIVE.value
            if session_manager.active_session
            else session_manager.state.value
        )
        ctx.response_text = intent_router._get_response(
            ctx.intent_result.intent, response_state
        )
        logger.info("Loop [8/12] Using canned response")

    # Step 9: Engine execution (session lifecycle)
    if ctx.intent_result.intent == Intent.START_SESSION:
        if session_manager.state == SessionState.INTENT_RESOLVED:
            logger.debug("Loop [9/12] Starting session (transcript=%r)", transcript[:80])
            session = await session_manager.start_session(transcript)
            logger.debug("Loop [9/12] Session started: id=%s", session.session_id)
            ctx.session_event = {
                "type": "session_started",
                "session_id": session.session_id,
                "started_at": session.started_at,
                "intent_transcript": transcript,
            }
    elif ctx.intent_result.intent == Intent.END_SESSION:
        if session_manager.active_session:
            try:
                # Capture session info before ending (for summary generation)
                active = session_manager.active_session
                session_type = active.session_type
                intent_transcript = active.intent_transcript
                # Grab ephemeral interactions before session record is cleared
                interactions = list(active.interactions)
                logger.debug("Loop [9/12] Ending session %s (%d interactions captured)",
                             active.session_id, len(interactions))

                # Return to SESSION_ACTIVE before ending
                if session_manager.state == SessionState.INTENT_RESOLVED:
                    session_manager.transition(SessionState.SESSION_ACTIVE)
                completed = await session_manager.end_session(transcript)
                logger.debug("Loop [9/12] Session ended: id=%s, duration=%dms",
                             completed.session_id, completed.duration_ms or 0)
                ctx.session_event = {
                    "type": "session_completed",
                    "session_id": completed.session_id,
                    "duration_ms": completed.duration_ms,
                }

                # Step 11: Memory written — generate validated session summary
                # Raw interactions are ephemeral; only the structured summary is stored.
                summary = None
                if llm_responder:
                    logger.debug("Loop [11/12] Requesting LLM session summary")
                    summary = await llm_responder.generate_session_summary(
                        session_type=session_type,
                        duration_ms=completed.duration_ms,
                        intent_transcript=intent_transcript,
                        completion_transcript=completed.completion_transcript,
                        interactions=interactions,
                    )
                if not summary:
                    # Fallback: structured but not LLM-generated
                    duration_s = (completed.duration_ms or 0) // 1000
                    interaction_count = len(interactions)
                    summary = f"Session ({session_type}, {duration_s}s, {interaction_count} interactions). Started: {intent_transcript or 'unknown'}."
                    logger.debug("Loop [11/12] Using fallback summary (%d chars)", len(summary))
                else:
                    logger.debug("Loop [11/12] LLM summary generated (%d chars)", len(summary))
                await event_store.update_session_summary(completed.session_id, summary)
                ctx.session_event["summary"] = summary
                logger.info("Loop [11/12] Session summary stored (%d interactions distilled)", len(interactions))
            except InvalidTransition as e:
                logger.warning("Could not end session: %s", e)
                ctx.response_text = "Unable to end the session right now. Try again."

    # Step 10: Event logged
    # Session lifecycle events (created/completed/abandoned) are persisted by
    # session_manager.start_session() and end_session(). Individual utterances
    # are ephemeral by design.

    # Step 12: Response ready
    logger.info("Loop [12/12] Response: %r", ctx.response_text[:80])
    return ctx
