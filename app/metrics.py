"""Token cost, per-reply records, session metrics and the console line."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import Pricing

STAGES = ("llm", "tts", "play", "total")
_DASH = "–"


@dataclass
class Usage:
    cache_hit: int = 0
    cache_miss: int = 0
    output: int = 0

    @property
    def total(self) -> int:
        return self.cache_hit + self.cache_miss + self.output

    @classmethod
    def from_api(cls, u) -> "Usage":
        hit = getattr(u, "prompt_cache_hit_tokens", None)
        miss = getattr(u, "prompt_cache_miss_tokens", None)
        out = int(getattr(u, "completion_tokens", 0) or 0)
        if hit is None or miss is None:
            return cls(0, int(getattr(u, "prompt_tokens", 0) or 0), out)
        return cls(int(hit), int(miss), out)


def is_peak(at: datetime, windows: list[tuple[int, int]]) -> bool:
    if at.weekday() >= 5:
        return False
    return any(start <= at.hour < end for start, end in windows)


def cost_usd(usage: Usage, pricing: Pricing, at: datetime) -> float:
    r = pricing.peak if is_peak(at, pricing.peak_windows_utc) else pricing.off_peak
    return (usage.cache_hit * r.cache_hit + usage.cache_miss * r.cache_miss + usage.output * r.output) / 1_000_000


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


@dataclass
class ReplyRecord:
    label: str
    kind: str
    received_at: float
    routed_at: float
    first_token_at: float | None
    first_sentence_at: float | None
    first_audio_at: float | None
    play_start_at: float | None
    done_at: float | None
    usage: Usage
    cost: float
    say: str
    emotion: str
    action: str | None
    safety: list[str] = field(default_factory=list)
    error: str | None = None

    def stages(self) -> dict[str, float | None]:
        return {
            "llm": _diff(self.first_sentence_at, self.routed_at),
            "tts": _diff(self.first_audio_at, self.first_sentence_at),
            "play": _diff(self.play_start_at, self.first_audio_at),
            "total": _diff(self.play_start_at, self.received_at),
        }


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


class SessionMetrics:
    def __init__(self, logs_dir: Path, started_at: float, clock=time.perf_counter):
        self._clock = clock
        self._started_at = started_at
        self._records: list[ReplyRecord] = []
        logs_dir = Path(logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / f"session-{datetime.now():%Y%m%d-%H%M%S}.jsonl"

    def add(self, rec: ReplyRecord) -> None:
        self._records.append(rec)
        row = asdict(rec)
        row["stages"] = rec.stages()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @property
    def total_cost(self) -> float:
        return sum(r.cost for r in self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.usage.total for r in self._records)

    def cost_per_hour(self) -> float:
        elapsed = max(self._clock() - self._started_at, 60.0)
        return self.total_cost / (elapsed / 3600.0)

    def percentiles(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for name in STAGES:
            vals = [v for r in self._records if (v := r.stages()[name]) is not None]
            if vals:
                out[name] = (_percentile(vals, 0.5), _percentile(vals, 0.9))
        return out

    def summary(self) -> str:
        lines = [f"{len(self._records)} replies · {self.total_tokens} tokens · "
                 f"${self.total_cost:.5f} · ${self.cost_per_hour():.3f}/h"]
        for name, (p50, p90) in self.percentiles().items():
            lines.append(f"  {name:<5} p50 {p50:.1f}s  p90 {p90:.1f}s")
        return "\n".join(lines)


def _sec(v: float | None) -> str:
    return _DASH if v is None else f"{v:.1f}"


def format_reply(rec: ReplyRecord, session_per_hour: float) -> str:
    st = rec.stages()
    total = _DASH if st["total"] is None else f"{st['total']:.1f}s"
    return (
        f"{rec.label}\n"
        f"  {rec.emotion:<5} | {rec.say}\n"
        f"  {total} total  (llm {_sec(st['llm'])} · tts {_sec(st['tts'])} · play {_sec(st['play'])})"
        f"  · ${rec.cost:.5f}  · session ${session_per_hour:.3f}/h"
    )
