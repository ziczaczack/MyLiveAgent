"""VTube Studio avatar: connection with background reconnect, reply state, fixed-rate injection loop.

The speech path only calls the synchronous methods, which record state or enqueue a request;
all network I/O happens in one background task, so the avatar can never delay or stop speech.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import deque
from typing import Awaitable, Callable

import numpy as np

from app.avatar.lipsync import envelope
from app.avatar.vts import Transport, VTSAuthDenied, VTSClient, VTSError, aiohttp_connect
from app.config import AvatarSettings

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10.0
RESET_TIMEOUT_S = 2.0
HEARTBEAT_S = 2.0  # idle ping so a closed VTube Studio is noticed between replies
SELF_TEST_HOLD_S = 1.0
_EXPECTED = (OSError, ConnectionError, VTSError, asyncio.TimeoutError)
DENIED_LINE = "avatar: plugin not allowed in VTube Studio; type /avatar to ask again"
_MISS_HINT = "check config or type /avatar after switching models"


def _never_raises(fn):
    @functools.wraps(fn)
    def wrapper(self, *args):
        try:
            return fn(self, *args)
        except Exception:
            log.exception("avatar %s failed", fn.__name__)
    return wrapper


def _is_disconnect(e: VTSError) -> bool:
    return str(e) == "disconnected"


class VTSAvatar:
    def __init__(
        self,
        settings: AvatarSettings,
        out: Callable[[str], None],
        connect: Callable[[str], Awaitable[Transport]] = aiohttp_connect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._s = settings
        self._out = out
        self._connect = connect
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._client: VTSClient | None = None
        self._connected = False
        self._shown: str | None = None  # last connection state printed
        self._commands: deque[tuple[str, dict]] = deque()
        self._model_expressions: set[str] = set()
        self._hotkey_names: set[str] = set()  # lower-cased
        self._hotkey_ids: set[str] = set()
        self._warned: set[str] = set()
        # The owner denied (or ignored) the Allow popup: stop asking until /avatar re-arms.
        self._parked = False
        self._rearm = asyncio.Event()
        self._allow_noticed = False  # the "click Allow" notice prints once per arm
        # reply state
        self._thinking = False
        self._accept_audio = False  # True between play_started and play_ended/muted
        self._track: list[float] | None = None
        self._track_start = 0.0
        self._mouth_zero = False  # one final MouthOpen 0 owed
        self._expression: str | None = None

    # --- speech-path API: synchronous, state only ---

    @_never_raises
    def thinking(self) -> None:
        self._thinking = True

    @_never_raises
    def play_started(self) -> None:
        self._thinking = False
        self._accept_audio = True

    @_never_raises
    def play_ended(self) -> None:
        self._accept_audio = False
        self._close_mouth()

    @_never_raises
    def muted(self) -> None:
        self.reply_done()

    @_never_raises
    def reply_done(self) -> None:
        self._expression_off()
        self._thinking = False
        self._accept_audio = False
        self._close_mouth()

    @_never_raises
    def audio(self, samples: np.ndarray, sample_rate: int) -> None:
        """Called from the playback thread right after playback starts."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        env = envelope(samples, sample_rate, self._s.fps, self._s.mouth_gain, self._s.mouth_smoothing)
        try:
            loop.call_soon_threadsafe(self._start_track, env)
        except RuntimeError:  # loop closed between the check and the call
            pass

    @_never_raises
    def react(self, emotion: str, action: str | None) -> None:
        self._expression_off()
        if not self._connected:
            return
        file = self._s.expressions.get(emotion)
        if file:
            if file in self._model_expressions:
                self._commands.append(("ExpressionActivation", {"expressionFile": file, "active": True}))
                self._expression = file
            else:
                self._warn_once(f"avatar: model has no expression '{file}' (emotion {emotion}); {_MISS_HINT}",
                                console=True)
        if action:
            self._trigger(action)

    @_never_raises
    def retry(self) -> None:
        """Ask VTube Studio again after the owner denied or ignored the Allow popup; no-op otherwise."""
        if self._parked:
            self._parked = False
            self._allow_noticed = False
            self._shown = None
            self._rearm.set()

    def status(self) -> str:
        if self._parked:
            return f"avatar: not allowed in VTube Studio ({self._s.url})"
        return f"avatar: {'connected' if self._connected else 'not connected'} ({self._s.url})"

    # --- lifecycle ---

    async def start(self) -> None:
        if self._task is None:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        client, self._client = self._client, None
        if client is None:
            return
        if self._connected:
            try:
                await asyncio.wait_for(self._reset(client), RESET_TIMEOUT_S)
            except Exception as e:
                log.debug("avatar reset on close failed: %s", e)
        self._connected = False
        try:
            await client.close()
        except Exception as e:
            log.debug("avatar close failed: %s", e)

    async def self_test(self, out: Callable[[str], None]) -> None:
        client = self._client
        if not self._connected or client is None:
            out("avatar: not connected")
            return
        try:
            await self._learn(client)  # the owner may have loaded another model
            out("avatar: thinking pose")
            self._thinking = True
            await asyncio.sleep(SELF_TEST_HOLD_S)
            self._thinking = False
            for emotion, file in self._s.expressions.items():
                if file not in self._model_expressions:
                    out(f"avatar: {emotion} (not in the loaded model)")
                    continue
                out(f"avatar: {emotion}")
                self.react(emotion, None)
                await asyncio.sleep(SELF_TEST_HOLD_S)
            self._expression_off()
            for action, hotkey in self._s.actions.items():
                if not self._has_hotkey(hotkey):
                    out(f"avatar: {action} (not in the loaded model)")
                    continue
                out(f"avatar: {action}")
                self._trigger(action)
                await asyncio.sleep(SELF_TEST_HOLD_S)
        except Exception as e:
            log.warning("avatar self-test failed: %s", e)
        finally:
            self.reply_done()
            for _ in range(int(RESET_TIMEOUT_S * 100)):
                if not self._commands or not self._connected:
                    break
                await asyncio.sleep(0.01)

    # --- internals ---

    def _start_track(self, env: list[float]) -> None:
        if not self._accept_audio:  # late audio from a chunk that was muted or already ended
            return
        self._track = env
        self._track_start = self._clock()

    def _close_mouth(self) -> None:
        if self._track is not None:
            self._track = None
            self._mouth_zero = True

    def _expression_off(self) -> None:
        file, self._expression = self._expression, None
        if file and self._connected:
            self._commands.append(("ExpressionActivation", {"expressionFile": file, "active": False}))

    def _trigger(self, action: str) -> None:
        hotkey = self._s.actions.get(action)
        if not hotkey or not self._connected:
            return
        if self._has_hotkey(hotkey):
            self._commands.append(("HotkeyTrigger", {"hotkeyID": hotkey}))
        else:
            self._warn_once(f"avatar: model has no hotkey '{hotkey}' (action {action}); {_MISS_HINT}",
                            console=True)

    def _has_hotkey(self, hotkey: str) -> bool:
        return hotkey in self._hotkey_ids or hotkey.lower() in self._hotkey_names

    def _warn_once(self, msg: str, console: bool = False) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            log.warning(msg)
            if console:
                self._out(msg)

    def _params(self) -> list[dict]:
        params = []
        if self._track is not None:
            i = max(0, int((self._clock() - self._track_start) * self._s.fps))
            params.append({"id": "MouthOpen", "value": self._track[i] if i < len(self._track) else 0.0})
        elif self._mouth_zero:
            params.append({"id": "MouthOpen", "value": 0.0})
        self._mouth_zero = False
        if self._thinking:
            params.extend({"id": k, "value": v} for k, v in self._s.thinking.items())
        return params

    async def _call(self, client: VTSClient, kind: str, data: dict | None = None) -> dict:
        """One request; an API error or timeout is logged once and skipped, a disconnect raises."""
        try:
            return await client.request(kind, data)
        except VTSError as e:
            if _is_disconnect(e):
                raise
            self._warn_once(f"avatar: {kind} failed: {e}")
            return {}

    async def _learn(self, client: VTSClient) -> list[str]:
        """Read what the loaded model has; returns the expression files currently active.

        A request that failed (timeout, API error) keeps what was known before, so one bad
        answer cannot make every mapping look "not in the model" for the rest of the session.
        """
        hk = await self._call(client, "HotkeysInCurrentModel")
        ex = await self._call(client, "ExpressionState", {"details": False})
        if "availableHotkeys" in hk:
            hotkeys = hk["availableHotkeys"] or []
            self._hotkey_names = {str(h.get("name", "")).lower() for h in hotkeys if h.get("name")}
            self._hotkey_ids = {str(h.get("hotkeyID")) for h in hotkeys if h.get("hotkeyID")}
        exprs = (ex.get("expressions") or []) if "expressions" in ex else []
        if "expressions" in ex:
            self._model_expressions = {str(e.get("file")) for e in exprs if e.get("file")}
        if "availableHotkeys" in hk and "expressions" in ex:
            self._warned.clear()  # fresh model info: report misses again
        return [str(e.get("file")) for e in exprs if e.get("active")]

    async def _reset(self, client: VTSClient) -> None:
        file, self._expression = self._expression, None
        self._thinking = self._accept_audio = self._mouth_zero = False
        self._track = None
        self._commands.clear()
        if file:
            await client.request("ExpressionActivation", {"expressionFile": file, "active": False})
        await client.request("InjectParameterData", {
            "faceFound": False, "mode": "set", "parameterValues": [{"id": "MouthOpen", "value": 0.0}]})

    def _show(self, state: str, line: str) -> None:
        if self._shown != state:
            self._shown = state
            self._out(line)

    def _allow_notice(self, line: str) -> None:
        if not self._allow_noticed:
            self._allow_noticed = True
            self._out(line)

    async def _run(self) -> None:
        while True:
            try:
                transport = await asyncio.wait_for(self._connect(self._s.url), CONNECT_TIMEOUT_S)
                self._client = VTSClient(transport, self._s.plugin_name, self._s.plugin_developer,
                                         self._s.token_file, self._allow_notice)
                await self._client.authenticate()
                self._allow_noticed = False  # a later revoke should tell the owner again
                await self._connected_session(self._client)
            except asyncio.CancelledError:
                raise  # close() resets and closes the client
            except VTSAuthDenied as e:
                # Retrying would pop the Allow/Deny dialog up again every few seconds; wait for /avatar.
                log.warning("avatar plugin not allowed: %s", e)
                await self._drop_client()
                self._rearm.clear()
                self._parked = True
                self._show("denied", DENIED_LINE)
                await self._rearm.wait()
                continue
            except Exception as e:
                first = self._shown != "down"
                if first:
                    log.warning("avatar connection failed: %s", e, exc_info=not isinstance(e, _EXPECTED))
                else:
                    log.debug("avatar connection failed: %s", e)
                await self._drop_client()
                self._show("down", "avatar: VTube Studio not reachable, retrying in the background")
            await asyncio.sleep(self._s.reconnect_s)

    async def _drop_client(self) -> None:
        self._connected = False
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.close()
            except Exception as e:
                log.debug("avatar client close failed: %s", e)

    async def _connected_session(self, client: VTSClient) -> None:
        active = await self._learn(client)
        # Reconnected: whatever the last reply left on is stale; turn our mapped expressions off.
        self._commands.clear()
        self._expression = None
        for file in active:
            if file in self._s.expressions.values():
                await self._call(client, "ExpressionActivation", {"expressionFile": file, "active": False})
        self._connected = True
        self._show("up", "avatar: connected")
        period = 1.0 / self._s.fps
        loop = asyncio.get_running_loop()
        last_sent = loop.time()
        while True:
            while self._commands:
                kind, data = self._commands.popleft()
                await self._call(client, kind, data)
            params = self._params()
            if params:
                await self._call(client, "InjectParameterData",
                                 {"faceFound": False, "mode": "set", "parameterValues": params})
                last_sent = loop.time()
            elif loop.time() - last_sent >= HEARTBEAT_S:
                await self._call(client, "APIState")
                last_sent = loop.time()
            await asyncio.sleep(period)
