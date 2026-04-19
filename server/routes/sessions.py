import logging

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.get("/sessions")
async def get_sessions(request: Request):
    store = request.app.state.event_store
    sessions = await store.get_sessions()
    logger.debug("GET /api/sessions -> %d sessions", len(sessions))
    return sessions


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    store = request.app.state.event_store
    session = await store.get_session(session_id)
    if session is None:
        logger.debug("GET /api/sessions/%s -> 404", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
    logger.debug("GET /api/sessions/%s -> found", session_id)
    return session


@router.get("/state")
async def get_state(request: Request):
    sm = request.app.state.session_manager
    payload = {
        "session_state": sm.state.value,
        "active_session_id": (
            sm.active_session.session_id if sm.active_session else None
        ),
    }
    logger.debug("GET /api/state -> %s", payload)
    return payload
