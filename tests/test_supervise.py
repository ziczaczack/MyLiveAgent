import asyncio

import pytest

from app.supervise import supervise


async def test_supervise_restarts_then_stops():
    stop = asyncio.Event()
    calls = 0
    lines = []

    async def factory():
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ValueError("boom")
        await stop.wait()

    t = asyncio.create_task(supervise("worker", factory, lines.append, stop=stop, backoff_s=0.01))
    await asyncio.sleep(0.2)
    assert calls == 3 and not t.done()
    stop.set()
    await asyncio.wait_for(t, 1)
    assert lines == ["(worker crashed: ValueError: boom; restarting)"] * 2


async def test_supervise_propagates_cancel():
    async def factory():
        await asyncio.sleep(10)

    t = asyncio.create_task(supervise("w", factory, print, stop=asyncio.Event()))
    await asyncio.sleep(0.01)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


async def test_supervise_returns_on_normal_finish_and_backoff_interruptible():
    async def ok():
        return

    await asyncio.wait_for(supervise("w", ok, print, stop=asyncio.Event()), 1)

    stop = asyncio.Event()

    async def bad():
        raise RuntimeError("x")

    t = asyncio.create_task(supervise("w", bad, lambda s: None, stop=stop, backoff_s=60))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(t, 1)
