"""Ingest source interface."""
from __future__ import annotations

from typing import Callable, Protocol

from app.events import Event


class IngestSource(Protocol):
    async def run(self, emit: Callable[[Event], None]) -> None: ...
