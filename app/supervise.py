"""Keep a task alive: rerun it after a crash until the session stops."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


async def supervise(name: str, factory: Callable[[], Awaitable[None]], out, *, stop: asyncio.Event,
                    backoff_s: float = 1.0) -> None:
    while not stop.is_set():
        try:
            await factory()
            return  # finished normally: done, don't spin
        except Exception as e:  # noqa: BLE001 - CancelledError is BaseException and propagates
            out(f"({name} crashed: {type(e).__name__}: {e}; restarting)")
        try:
            await asyncio.wait_for(stop.wait(), backoff_s)
        except asyncio.TimeoutError:
            pass
