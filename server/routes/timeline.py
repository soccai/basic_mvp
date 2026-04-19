import logging

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.get("/timeline")
async def get_timeline(request: Request):
    store = request.app.state.event_store
    timeline = await store.get_timeline()
    logger.debug("GET /api/timeline -> %d entries", len(timeline))
    return timeline
