"""Mute state and the global mute hotkey."""
from __future__ import annotations

import asyncio
from typing import Callable


class MuteController:
    """Single owner of the muted flag; /mute, /unmute and the hotkey all go through it."""

    def __init__(self, player, out: Callable[[str], None]) -> None:
        self._player = player
        self._out = out
        self.muted = False

    def set(self, on: bool) -> None:
        if on == self.muted:
            return
        self.muted = on
        self._player.set_muted(on)
        self._out("[MUTED]" if on else "[LIVE]")

    def toggle(self) -> None:
        self.set(not self.muted)


class MuteHotkey:
    """Global hotkey. The pynput thread only schedules on_press onto the event loop."""

    def __init__(self, combo: str, on_press: Callable[[], None],
                 loop: asyncio.AbstractEventLoop, hotkeys_factory=None) -> None:
        self._combo = combo
        self._on_press = on_press
        self._loop = loop
        self._factory = hotkeys_factory
        self._listener = None

    def start(self) -> None:
        factory = self._factory
        if factory is None:
            from pynput.keyboard import GlobalHotKeys
            factory = GlobalHotKeys
        listener = factory({self._combo: lambda: self._loop.call_soon_threadsafe(self._on_press)})
        listener.start()
        self._listener = listener

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
