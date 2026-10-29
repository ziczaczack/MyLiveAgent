import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import Pricing, Rates
from app.metrics import ReplyRecord, SessionMetrics, Usage, cost_usd, format_reply, is_peak

P = Pricing(off_peak=Rates(0.003, 0.15, 0.60), peak=Rates(0.006, 0.30, 1.20), peak_windows_utc=[(1, 4), (6, 10)])
U = Usage(cache_hit=1200, cache_miss=300, output=80)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def make_rec(cost=4e-5, **over):
    kw = dict(label="[gift] kaito_77 sent Rose", kind="gift", received_at=0.0, routed_at=0.0,
              first_token_at=0.5, first_sentence_at=0.9, first_audio_at=1.6, play_start_at=1.7, done_at=4.0,
              usage=U, cost=cost, say="A rose? From kaito?", emotion="smug", action=None, safety=[], error=None)
    kw.update(over)
    return ReplyRecord(**kw)


def test_off_peak_cost():
    assert cost_usd(U, P, utc(2026, 11, 2, 12)) == pytest.approx(9.66e-5)


def test_peak_cost():
    assert cost_usd(U, P, utc(2026, 11, 2, 1)) == pytest.approx(1.932e-4)


@pytest.mark.parametrize("at,peak", [(utc(2026, 11, 2, 1, 0), True), (utc(2026, 11, 2, 3, 59), True),
                                     (utc(2026, 11, 2, 4, 0), False), (utc(2026, 11, 2, 5, 30), False),
                                     (utc(2026, 11, 2, 9, 59), True), (utc(2026, 11, 1, 2, 0), False)])
def test_is_peak(at, peak):
    assert is_peak(at, P.peak_windows_utc) is peak


def test_usage_without_cache_fields():
    assert Usage.from_api(SimpleNamespace(prompt_tokens=1500, completion_tokens=80)) == Usage(0, 1500, 80)


def test_usage_with_cache_fields():
    u = SimpleNamespace(prompt_cache_hit_tokens=1200, prompt_cache_miss_tokens=300, prompt_tokens=1500,
                        completion_tokens=80)
    assert Usage.from_api(u) == U and U.total == 1580


def test_format_reply():
    out = format_reply(make_rec(), 0.031)
    assert "[gift] kaito_77 sent Rose" in out and "smug" in out and "A rose? From kaito?" in out
    assert "1.7s total" in out and "llm 0.9" in out and "tts 0.7" in out and "play 0.1" in out
    assert "$0.031/h" in out and "$0.00004" in out
    assert len(out.splitlines()) == 3


def test_format_reply_missing_stage():
    out = format_reply(make_rec(first_audio_at=None, play_start_at=None), 0.0)
    assert "tts –" in out and "play –" in out and "– total" in out


def test_session_jsonl_and_rate(tmp_path):
    m = SessionMetrics(tmp_path, started_at=0.0, clock=lambda: 1800.0)
    m.add(make_rec(cost=0.01))
    lines = list(tmp_path.glob("session-*.jsonl"))[0].read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["usage"] == {"cache_hit": 1200, "cache_miss": 300, "output": 80}
    assert m.cost_per_hour() == pytest.approx(0.02)
    assert m.total_tokens == 1580


def test_elapsed_clamped(tmp_path):
    m = SessionMetrics(tmp_path, started_at=0.0, clock=lambda: 1.0)
    m.add(make_rec(cost=0.01))
    assert m.cost_per_hour() == pytest.approx(0.01 * 60)


def test_percentiles_ignore_none(tmp_path):
    m = SessionMetrics(tmp_path, started_at=0.0, clock=lambda: 100.0)
    m.add(make_rec())
    m.add(make_rec(first_audio_at=None, play_start_at=None))
    p = m.percentiles()
    assert p["llm"] == (pytest.approx(0.9), pytest.approx(0.9))
    assert p["tts"] == (pytest.approx(0.7), pytest.approx(0.7))
    assert "p50" in m.summary() or "llm" in m.summary()


def test_is_peak_normalises_to_utc():
    tz8 = timezone(timedelta(hours=8))
    assert is_peak(datetime(2026, 11, 2, 9, tzinfo=tz8), P.peak_windows_utc) is True    # 01:00 UTC Mon
    assert is_peak(datetime(2026, 11, 2, 8, tzinfo=tz8), P.peak_windows_utc) is False   # Sun 00:00 UTC
