"""Mute state and the global mute hotkey."""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Callable

_VK = {"<ctrl>": 0x11, "<alt>": 0x12, "<shift>": 0x10, "<cmd>": 0x5B}


def _win_key_state(vk: int) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def combo_is_held(combo: str, key_state: Callable[[int], bool] | None = None) -> bool:
    """True if every key of a pynput combo is physically down. Keys we can't map are assumed down."""
    if key_state is None:
        if sys.platform != "win32":
            return True
        key_state = _win_key_state
    for part in combo.lower().split("+"):
        vk = _VK.get(part)
        if vk is None and len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        if vk is not None and not key_state(vk):
            return False
    return True


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
                 loop: asyncio.AbstractEventLoop, hotkeys_factory=None,
                 ready_timeout: float = 1.0,
                 keys_down: Callable[[str], bool] = combo_is_held) -> None:
        self._combo = combo
        self._keys_down = keys_down
        self._on_press = on_press
        self._loop = loop
        self._factory = hotkeys_factory
        self._ready_timeout = ready_timeout
        self._listener = None

    def start(self) -> None:
        factory = self._factory
        if factory is None:
            from pynput.keyboard import GlobalHotKeys
            factory = GlobalHotKeys
        listener = factory({self._combo: self._activated})
        listener.start()
        # start() only spawns a thread and pynput's wait() has no timeout, so poll briefly.
        deadline = time.monotonic() + self._ready_timeout
        while not (getattr(listener, "_ready", False) and listener.running):
            alive = getattr(listener, "is_alive", lambda: True)()
            if not alive or time.monotonic() >= deadline:
                try:
                    listener.stop()
                except Exception:  # noqa: BLE001 - don't mask the real error
                    pass
                raise RuntimeError("keyboard hook could not be installed")
            time.sleep(0.02)
        self._listener = listener

    def _activated(self) -> None:
        # pynput can miss a key release (Ctrl+M arrives as a different key code on Windows) and then
        # fire on Ctrl alone. Runs on the pynput thread, while the keys are still down.
        if self._keys_down(self._combo):
            self._loop.call_soon_threadsafe(self._on_press)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
