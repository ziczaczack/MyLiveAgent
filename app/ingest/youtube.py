"""YouTube live chat via the unofficial pytchat library. Read only: never posts anything."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterable

from app.config import YouTubeSettings
from app.events import Event, Kind

log = logging.getLogger(__name__)

# Library errors that a retry can't fix; matched by name so tests don't need the library.
_PERMANENT = {"InvalidURL", "InvalidVideoIdException"}
_PAID = {"paid_message": "Super Chat", "paid_sticker": "Super Sticker"}
_BACKLOG_GRACE_US = 5_000_000  # YouTube replays recent chat on (re)connect; older than this is skipped


def to_event(msg: dict, big_amounts: dict[str, float]) -> Event | None:
    """Map one chat message dict to an Event, or None for types we don't react to."""
    kind = msg.get("message_type")
    viewer = (msg.get("author") or {}).get("name") or ""
    text = msg.get("message") or ""
    if kind == "text_message":
        return Event(Kind.COMMENT, viewer=viewer, text=text) if text.strip() else None
    if kind in _PAID:
        money = msg.get("money") or {}
        threshold = big_amounts.get(str(money.get("currency") or "").upper())
        big = threshold is not None and float(money.get("amount") or 0) >= threshold
        label = f"{_PAID[kind]} {money.get('text') or ''}".strip()
        return Event(Kind.GIFT, viewer=viewer, gift=label, big=big, text=text)
    if kind == "membership_item":
        return Event(Kind.FOLLOW, viewer=viewer)
    return None


_PYTCHAT_TYPES = {"textMessage": "text_message", "superChat": "paid_message",
                  "superSticker": "paid_sticker", "newSponsor": "membership_item"}


def from_pytchat(item) -> dict:
    """Convert a pytchat chat item to the plain message dict `to_event` reads."""
    return {
        "message_type": _PYTCHAT_TYPES.get(item.type, item.type),
        "author": {"name": item.author.name},
        "message": item.message,
        "money": {"amount": item.amountValue, "currency": item.currency, "text": item.amountString},
        "timestamp": int(item.timestamp) * 1000,  # ms -> µs
    }


def _open_chat(url: str) -> Iterable[dict]:
    import pytchat

    chat = pytchat.create(video_id=url, interruptable=False)  # accepts a link or a video id

    def messages():
        try:
            while chat.is_alive():
                for item in chat.get().items:
                    yield from_pytchat(item)
            try:
                chat.raise_for_status()
            except Exception as e:  # noqa: BLE001 - "stream finished" is a normal end
                if type(e).__name__ != "ChatDataFinished":
                    raise
        finally:
            chat.terminate()

    return messages()


class YouTubeChat:
    """Reads one live stream's chat. The library blocks, so it runs on a daemon thread."""

    def __init__(self, url: str, settings: YouTubeSettings, out: Callable[[str], None],
                 open_chat: Callable[[str], Iterable[dict]] = _open_chat, sleep=asyncio.sleep) -> None:
        self._url = url
        self._s = settings
        self._out = out
        self._open = open_chat
        self._sleep = sleep
        self._last_error: str | None = None

    async def run(self, emit: Callable[[Event], None]) -> None:
        while True:
            outcome, error = await self._read_once(emit)
            if outcome == "end":
                self._out("youtube: chat ended")
                return
            reason = str(error) or type(error).__name__
            if type(error).__name__ in _PERMANENT:
                self._out(f"youtube: {reason}. Check the link and run /yt again.")
                return
            log.warning("youtube chat failed: %s", reason)
            if reason != self._last_error:
                self._out(f"youtube: {reason}; retrying every {self._s.retry_s:g} s")
                self._last_error = reason
            await self._sleep(self._s.retry_s)

    async def _read_once(self, emit) -> tuple[str, BaseException | None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def put(item) -> None:
            if stop.is_set():
                return
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:  # loop already closed at shutdown
                pass

        def pump() -> None:
            try:
                chat = self._open(self._url)
                put(("open", None))
                for msg in chat:
                    if stop.is_set():
                        return
                    put(("msg", msg))
                put(("end", None))
            except Exception as e:  # noqa: BLE001 - reported on the loop
                put(("error", e))

        threading.Thread(target=pump, name="youtube-chat", daemon=True).start()
        opened_us = int(time.time() * 1_000_000)
        try:
            while True:
                kind, item = await queue.get()
                if kind == "open":
                    opened_us = int(time.time() * 1_000_000)
                    self._last_error = None
                    self._out("youtube: reading chat")
                elif kind == "msg":
                    ts = item.get("timestamp")
                    if isinstance(ts, (int, float)) and ts < opened_us - _BACKLOG_GRACE_US:
                        continue
                    ev = to_event(item, self._s.big_superchat)
                    if ev is not None:
                        emit(ev)
                elif kind == "end":
                    return "end", None
                else:
                    return "error", item
        finally:
            stop.set()  # the thread may stay blocked inside the library; it is a daemon and emits nothing more
