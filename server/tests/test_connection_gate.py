import asyncio

import pytest

from server.ws.connection_gate import ConnectionGate


@pytest.mark.asyncio
async def test_first_connection_accepted():
    gate = ConnectionGate()
    accepted, token, reason = await gate.try_acquire(None, None)
    assert accepted is True
    assert token  # non-empty UUID
    assert reason == ""
    assert gate.active_token == token
    await gate.shutdown()


@pytest.mark.asyncio
async def test_second_connection_rejected():
    gate = ConnectionGate()
    accepted, token_a, _ = await gate.try_acquire(None, None)
    assert accepted is True

    accepted, _, reason = await gate.try_acquire(None, None)
    assert accepted is False
    assert "Another tab" in reason
    await gate.shutdown()


@pytest.mark.asyncio
async def test_second_connection_with_different_token_rejected():
    gate = ConnectionGate()
    accepted, _, _ = await gate.try_acquire(None, None)
    assert accepted is True

    accepted, _, reason = await gate.try_acquire("different-token", None)
    assert accepted is False
    assert reason != ""
    await gate.shutdown()


@pytest.mark.asyncio
async def test_reconnect_with_same_token():
    gate = ConnectionGate()
    accepted, token, _ = await gate.try_acquire(None, None)
    assert accepted is True

    await gate.release()
    # Within grace period, reconnect with same token
    accepted, returned_token, reason = await gate.try_acquire(token, None)
    assert accepted is True
    assert returned_token == token
    assert reason == ""
    await gate.shutdown()


@pytest.mark.asyncio
async def test_new_connection_during_grace_accepted():
    """After a tab disconnects, a new client (no token) should be accepted
    during the grace period — the previous tab is gone."""
    gate = ConnectionGate()
    accepted, token1, _ = await gate.try_acquire(None, None)
    assert accepted is True

    await gate.release()
    # During grace period, new connection without token is accepted
    accepted, token2, _ = await gate.try_acquire(None, None)
    assert accepted is True
    assert token2 != token1  # new token issued
    await gate.shutdown()


@pytest.mark.asyncio
async def test_grace_period_expires(monkeypatch):
    import server.config as cfg
    monkeypatch.setattr(cfg, "CONNECTION_GRACE_PERIOD_SECONDS", 0.1)

    gate = ConnectionGate()
    accepted, _, _ = await gate.try_acquire(None, None)
    assert accepted is True

    await gate.release()
    # Wait for grace period to expire
    await asyncio.sleep(0.2)

    assert gate.active_token is None

    # New connection should now be accepted
    accepted, token, reason = await gate.try_acquire(None, None)
    assert accepted is True
    assert token
    assert reason == ""
    await gate.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_grace_task():
    gate = ConnectionGate()
    accepted, token, _ = await gate.try_acquire(None, None)
    assert accepted is True

    await gate.release()
    # Grace task is now pending
    assert gate._grace_task is not None
    assert not gate._grace_task.done()

    await gate.shutdown()
    assert gate.active_token is None


@pytest.mark.asyncio
async def test_acquire_with_provided_token():
    gate = ConnectionGate()
    accepted, token, _ = await gate.try_acquire("my-custom-token", None)
    assert accepted is True
    assert token == "my-custom-token"
    assert gate.active_token == "my-custom-token"
    await gate.shutdown()
