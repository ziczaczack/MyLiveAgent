"""Scripted viewer traffic for development: comments, gifts and follows at random intervals."""
from __future__ import annotations

import asyncio
import random
from typing import Callable

from app.events import Event, Kind

KINDS = ("comment", "mention", "question", "small_gift", "big_gift", "follow")
WEIGHTS = (50, 15, 15, 12, 3, 5)


class MockFeed:
    def __init__(self, chat: dict, rng: random.Random, interval_s: tuple[float, float] = (2.0, 6.0),
                 sleep=asyncio.sleep, character_name: str = "Chibi") -> None:
        self._chat = chat
        self._rng = rng
        self._interval = interval_s
        self._sleep = sleep
        self._name = character_name

    def next_event(self) -> Event:
        rng, chat = self._rng, self._chat
        viewer = rng.choice(chat["viewers"])
        kind = rng.choices(KINDS, WEIGHTS)[0]
        if kind == "comment":
            return Event(Kind.COMMENT, viewer=viewer, text=rng.choice(chat["comments"]))
        if kind == "mention":
            text = rng.choice(chat["comments"])
            text = f"{self._name} {text}" if rng.random() < 0.5 else f"{text} @{self._name}"
            return Event(Kind.COMMENT, viewer=viewer, text=text)
        if kind == "question":
            return Event(Kind.COMMENT, viewer=viewer, text=rng.choice(chat["questions"]))
        if kind == "small_gift":
            return Event(Kind.GIFT, viewer=viewer, gift=rng.choice(chat["small_gifts"]))
        if kind == "big_gift":
            return Event(Kind.GIFT, viewer=viewer, gift=rng.choice(chat["big_gifts"]), big=True)
        return Event(Kind.FOLLOW, viewer=viewer)

    async def run(self, emit: Callable[[Event], None]) -> None:
        while True:
            await self._sleep(self._rng.uniform(*self._interval))
            emit(self.next_event())
