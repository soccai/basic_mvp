import asyncio
import logging
import uuid

from fastapi import WebSocket

from server import config

logger = logging.getLogger(__name__)


class ConnectionGate:
    """Enforces at most one WebSocket connection at a time.

    A connection token (UUID) is issued to the first client that connects.
    Subsequent connections are rejected unless they present the same token
    (reconnect from the same tab).  After disconnect, the token remains
    valid for a short grace period so the same tab can reconnect after a
    page refresh or transient network drop.
    """

    def __init__(self):
        self._active_token: str | None = None
        self._websocket: WebSocket | None = None
        self._grace_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def active_token(self) -> str | None:
        return self._active_token

    async def try_acquire(
        self, token: str | None, websocket: WebSocket
    ) -> tuple[bool, str, str]:
        """Attempt to acquire the connection slot.

        Returns (accepted, token, rejection_reason).
        """
        async with self._lock:
            # If grace period is running, the previous tab is gone
            in_grace = self._grace_task and not self._grace_task.done()
            if in_grace:
                self._grace_task.cancel()
                self._grace_task = None

            # No active connection — accept
            if self._active_token is None:
                new_token = token if token else str(uuid.uuid4())
                self._active_token = new_token
                self._websocket = websocket
                logger.info("Connection accepted — token %s", new_token[:8])
                return True, new_token, ""

            # Same token — reconnect from same tab
            if token and token == self._active_token:
                self._websocket = websocket
                logger.info("Reconnect accepted — token %s", token[:8])
                return True, token, ""

            # Grace period was active — previous tab disconnected, accept new client
            if in_grace:
                new_token = token if token else str(uuid.uuid4())
                self._active_token = new_token
                self._websocket = websocket
                logger.info("Connection accepted (previous tab gone) — token %s", new_token[:8])
                return True, new_token, ""

            # Active connection still live — reject
            logger.info(
                "Connection rejected — active token %s, offered %s",
                self._active_token[:8],
                token[:8] if token else "(none)",
            )
            return False, "", "Another tab is already connected to LifeOS"

    async def release(self):
        """Called on WebSocket disconnect.  Starts a grace period before
        clearing the active token so the same tab can reconnect."""
        async with self._lock:
            self._websocket = None
            if self._active_token:
                logger.info(
                    "Connection released — grace period %ds for token %s",
                    config.CONNECTION_GRACE_PERIOD_SECONDS,
                    self._active_token[:8],
                )
                self._grace_task = asyncio.create_task(self._expire_after_grace())

    async def _expire_after_grace(self):
        try:
            await asyncio.sleep(config.CONNECTION_GRACE_PERIOD_SECONDS)
            async with self._lock:
                logger.info("Grace period expired — clearing token %s",
                            self._active_token[:8] if self._active_token else "(none)")
                self._active_token = None
                self._websocket = None
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
            try:
                await self._grace_task
            except asyncio.CancelledError:
                pass
        self._active_token = None
        self._websocket = None
