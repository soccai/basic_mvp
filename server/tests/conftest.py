import pytest
import pytest_asyncio

from server.events.store import EventStore


@pytest_asyncio.fixture
async def event_store():
    store = EventStore(":memory:")
    await store.initialize()
    yield store
    await store.close()
