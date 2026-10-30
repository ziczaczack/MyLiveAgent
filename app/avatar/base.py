"""Avatar interface used by the speech path, and a no-op avatar for when it is disabled."""
from __future__ import annotations

from typing import Callable, Protocol

import numpy as np


class Avatar(Protocol):
    """Every non-async method is synchronous, non-blocking and never raises.

    audio() may be called from the playback thread; everything else runs on the event loop.
    """

    def thinking(self) -> None: ...
    def play_started(self) -> None: ...
    def play_ended(self) -> None: ...
    def muted(self) -> None: ...
    def audio(self, samples: np.ndarray, sample_rate: int) -> None: ...
    def react(self, emotion: str, action: str | None) -> None: ...
    def reply_done(self) -> None: ...
    def retry(self) -> None: ...  # ask VTube Studio again after the owner denied the plugin
    def status(self) -> str: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def self_test(self, out: Callable[[str], None]) -> None: ...


class NullAvatar:
    def thinking(self) -> None:
        pass

    def play_started(self) -> None:
        pass

    def play_ended(self) -> None:
        pass

    def muted(self) -> None:
        pass

    def audio(self, samples: np.ndarray, sample_rate: int) -> None:
        pass

    def react(self, emotion: str, action: str | None) -> None:
        pass

    def reply_done(self) -> None:
        pass

    def retry(self) -> None:
        pass

    def status(self) -> str:
        return "avatar off"

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def self_test(self, out: Callable[[str], None]) -> None:
        pass
