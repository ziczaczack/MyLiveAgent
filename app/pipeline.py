"""One event in, one spoken and logged reply out."""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone

from app.audio import Player
from app.brain.llm import Brain
from app.brain.prompt import describe_event
from app.config import Settings
from app.events import Event, Kind
from app.metrics import ReplyRecord, SessionMetrics, Usage, cost_usd, format_reply
from app.safety import Safety
from app.tts.base import TTS


_AUTH_MARKERS = ("authenticationerror", "permissiondenied", "401", "402", "insufficient balance")
_AUTH_HINT = "!!! DeepSeek rejected the request — check DEEPSEEK_API_KEY in .env and your account balance."


def event_label(ev: Event) -> str:
    if ev.kind is Kind.DEV:
        return f"[dev] {ev.text}"
    if ev.kind is Kind.COMMENT:
        return f"[comment] {ev.viewer}: {ev.text}"
    if ev.kind is Kind.GIFT:
        return f"[gift] {ev.viewer}: {ev.gift}{' (big)' if ev.big else ''}"
    if ev.kind is Kind.FOLLOW:
        return f"[follow] {ev.viewer}"
    return "[quiet]"


class Pipeline:
    def __init__(self, brain: Brain, safety: Safety, tts: TTS, player: Player,
                 metrics: SessionMetrics, settings: Settings,
                 out: Callable[[str], None] = print,
                 rng: random.Random | None = None, clock=time.perf_counter) -> None:
        self._brain, self._safety, self._tts, self._player = brain, safety, tts, player
        self._metrics, self._s, self._out = metrics, settings, out
        self._rng = rng or random.Random()
        self._clock = clock
        self._auth_hint_shown = False

    def over_budget(self) -> bool:
        return self._metrics.total_cost >= self._s.session_cost_cap_usd

    async def _synthesize(self, text: str) -> bytes:
        return await asyncio.wait_for(self._tts.synthesize(text), self._s.tts.timeout_s)

    def _note_error(self, error: str | None) -> None:
        if error and not self._auth_hint_shown and any(m in error.lower() for m in _AUTH_MARKERS):
            self._auth_hint_shown = True
            self._out(_AUTH_HINT)

    def _speak(self, text: str, rec: ReplyRecord) -> None:
        if rec.first_sentence_at is None:
            rec.first_sentence_at = self._clock()
        self._player.enqueue(asyncio.create_task(self._synthesize(text)))

    async def handle(self, ev: Event, routed_at: float | None = None) -> ReplyRecord:
        routed_at = self._clock() if routed_at is None else routed_at
        label = event_label(ev)
        over = self.over_budget()
        flagged = "self_harm" in ev.topics
        silent_skip = over and not flagged and ev.kind is Kind.QUIET
        if not silent_skip:
            self._out(f"{label}\n  thinking...")
        rec = ReplyRecord(label=label, kind=str(ev.kind), received_at=ev.received_at, routed_at=routed_at,
                          first_token_at=None, first_sentence_at=None, first_audio_at=None,
                          play_start_at=None, done_at=None, usage=Usage(), cost=0.0,
                          say="", emotion="neutral", action=None)

        if flagged:
            self._out(f"!!! SELF-HARM FLAG from {ev.viewer or 'dev'}: {ev.text}")
        if over and not flagged and ev.kind not in (Kind.GIFT, Kind.DEV):
            rec.error = "over_budget"
            rec.done_at = self._clock()
            if not silent_skip:
                self._out("  (skipped: session cost cap reached)")
            return rec
        event_text = describe_event(ev, self._s.character_name, self._s.dev_name,
                                    self._s.max_viewer_name_chars)

        self._player.start()
        spoken: list[str] = []

        async def on_sentence(s: str) -> bool:
            verdict = self._safety.check_output(s, ev.topics, ev.viewer)
            if not verdict.ok:
                rec.safety.append(f"{verdict.reason or 'blocked'}: {s}")
                line = self._rng.choice(self._s.lines.deflection)
                self._speak(line, rec)
                spoken.append(line)
                return False
            self._speak(s, rec)
            spoken.append(s)
            return True

        result = await self._brain.reply(
            event_text, on_sentence, max_tokens=self._s.llm.max_tokens // 2 if over else None)
        rec.first_token_at, rec.usage, rec.error = result.first_token_at, result.usage, result.error
        if result.error is None:
            rec.emotion, rec.action = result.emotion, result.action
        if not spoken:
            line = self._rng.choice(self._s.lines.brain_lag)
            self._speak(line, rec)
            spoken.append(line)
            rec.error = rec.error or "no_say"

        times = await self._player.finish()
        rec.first_audio_at, rec.play_start_at = times.first_audio_at, times.play_start_at
        errors = list(self._player.errors)
        self._player.errors.clear()
        if errors:
            rec.error = "; ".join(filter(None, [rec.error, *errors]))

        rec.say = " ".join(spoken)
        rec.done_at = self._clock()
        self._brain.remember(event_text, rec.say, rec.emotion, rec.action)
        rec.cost = cost_usd(rec.usage, self._s.pricing, datetime.now(timezone.utc))
        try:
            self._metrics.add(rec)
        except OSError as e:
            self._out(f"(log write failed: {e})")
        self._note_error(rec.error)
        self._out(format_reply(rec, self._metrics.cost_per_hour()))
        return rec
