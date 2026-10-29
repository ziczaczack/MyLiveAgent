"""Priority router: orders events, drops stale chatter, rate-limits viewers, emits QUIET."""
from __future__ import annotations

import asyncio
import time
from collections import deque

from app.config import RouterSettings
from app.events import Event, Kind, is_question, mentions_name

ORDINARY = 4


class Router:
    def __init__(self, settings: RouterSettings, character_name: str, clock=time.perf_counter) -> None:
        self._s = settings
        self._name = character_name
        self._clock = clock
        self._queues: list[deque[Event]] = [deque() for _ in range(6)]
        self._last_comment: dict[str, float] = {}
        self._start = clock()
        self._last_handled = self._start
        self._quiet_streak = 0
        self._wake = asyncio.Event()

    def priority(self, ev: Event) -> int:
        if ev.kind == Kind.GIFT:
            return 0 if ev.big else 2
        if ev.kind == Kind.FOLLOW:
            return 2
        if ev.kind == Kind.DEV:
            return 3
        if ev.kind == Kind.QUIET:
            return 5
        if "self_harm" in ev.topics:
            return 1
        if mentions_name(ev.text, self._name) or is_question(ev.text):
            return 3
        return ORDINARY

    def submit(self, ev: Event) -> bool:
        prio = self.priority(ev)
        if ev.kind == Kind.COMMENT and prio != 1:
            now = self._clock()
            last = self._last_comment.get(ev.viewer)
            if last is not None and now - last < self._s.viewer_cooldown_s:
                return False
            self._last_comment[ev.viewer] = now
        q = self._queues[prio]
        q.append(ev)
        if prio == ORDINARY:
            while len(q) > self._s.max_ordinary_queued:
                q.popleft()
        self.wake()
        return True

    def next_ready(self) -> Event | None:
        for prio, q in enumerate(self._queues):
            while q:
                ev = q.popleft()
                if self._is_stale(prio, ev):
                    continue
                return ev
        return None

    def _is_stale(self, prio: int, ev: Event) -> bool:
        age = self._clock() - ev.received_at
        if prio == ORDINARY:
            return age > self._s.stale_after_s
        # Gifts and dev speech are always answered; a late question or welcome sounds broken.
        if ev.kind == Kind.FOLLOW or (ev.kind == Kind.COMMENT and prio == 3):
            return age > self._s.priority_stale_after_s
        return False

    def _quiet_wait(self) -> float:
        # Each unanswered quiet line doubles the wait, so a dead chat isn't nagged every 20 s.
        return min(self._s.quiet_after_s * 2 ** self._quiet_streak, self._s.quiet_max_s)

    def mark_handled(self) -> None:
        self._last_handled = self._clock()

    def wake(self) -> None:
        self._wake.set()

    async def get(self) -> Event:
        while True:
            self._wake.clear()
            ev = self.next_ready()
            if ev is not None:
                self._quiet_streak = 0
                return ev
            remaining = max(self._start, self._last_handled) + self._quiet_wait() - self._clock()
            if remaining <= 0:
                self._last_handled = self._clock()  # next QUIET only after another full quiet period
                self._quiet_streak += 1
                return Event(Kind.QUIET, received_at=self._clock())
            try:
                await asyncio.wait_for(self._wake.wait(), remaining)
            except asyncio.TimeoutError:
                pass
