import asyncio
import random
from pathlib import Path

import pytest

from app.audio import Player
from app.brain.llm import Brain, BrainResult
from app.config import load_settings
from app.events import Event, Kind
from app.metrics import SessionMetrics, Usage
from app.pipeline import Pipeline
from app.safety import Safety


class FakeSink:
    def __init__(self):
        self.played = []

    async def play(self, mp3):
        self.played.append(mp3)

    def stop(self):
        pass


class FakeTTS:
    def __init__(self, fail=None, delay=0.0):
        self.fail, self.said, self.delay = fail, [], delay

    async def synthesize(self, text):
        self.said.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail and self.fail in text:
            raise RuntimeError("tts down")
        return text.encode()


class FakeBrain(Brain):
    def __init__(self, settings, sentences=(), result=None):
        super().__init__(None, settings.llm, "P")
        self.sentences, self.result = list(sentences), result or BrainResult(emotion="smug", action="celebrate")
        self.seen, self.max_tokens = [], []

    async def reply(self, event_text, on_sentence, max_tokens=None):
        self.max_tokens.append(max_tokens)
        for s in self.sentences:
            ok = await on_sentence(s)
            self.seen.append(ok)
            if not ok:
                break
        return self.result


@pytest.fixture
def settings(tmp_path):
    s = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    s.paths.logs_dir = tmp_path
    s.lines.deflection = ["DEFLECT"]
    s.lines.brain_lag = ["LAG"]
    return s


def make(settings, sentences=(), result=None, tts=None):
    outs = []
    sink = FakeSink()
    safety = Safety(["shit"], {"politics": ["election"], "self_harm": ["kill myself"]})
    brain = FakeBrain(settings, sentences, result)
    metrics = SessionMetrics(settings.paths.logs_dir, 0.0)
    player = Player(sink)
    pipe = Pipeline(brain, safety, tts or FakeTTS(), player, metrics, settings,
                    out=outs.append, rng=random.Random(1))
    return pipe, brain, sink, metrics, outs, player


def rows(metrics):
    return metrics.path.read_text(encoding="utf-8").splitlines() if metrics.path.exists() else []


async def test_happy_path(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["A.", "B."])
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert "thinking..." in outs[0]
    assert sink.played == [b"A.", b"B."] and rec.say == "A. B."
    assert rec.stages()["total"] is not None and rec.emotion == "smug" and rec.action == "celebrate"
    assert len(rows(metrics)) == 1 and "A. B." in outs[-1]
    await player.close()


async def test_blocklisted_sentence_replaced_and_rest_stopped(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["Fine.", "well shit.", "more."])
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="hi"))
    assert rec.say == "Fine. DEFLECT" and rec.safety == ["blocklist: well shit."]
    assert brain.seen == [True, False] and sink.played == [b"Fine.", b"DEFLECT"]
    await player.close()


async def test_unprompted_topic_replaced_prompted_kept(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["The election is rigged."])
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="hi"))
    assert rec.say == "DEFLECT" and rec.safety == ["topic:politics: The election is rigged."]
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="election?", topics=frozenset({"politics"})))
    assert rec.say == "The election is rigged." and rec.safety == []
    await player.close()


async def test_no_say_speaks_brain_lag(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, [], BrainResult(error="timeout"))
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert sink.played == [b"LAG"] and rec.error == "timeout" and rec.emotion == "neutral"
    brain.result = BrainResult()
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert rec.error == "no_say"
    await player.close()


async def test_tts_failure_still_prints_and_continues(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["Boom."], tts=FakeTTS(fail="Boom"))
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert "Boom." in outs[-1] and "tts down" in rec.error
    brain.sentences = ["Fine."]
    rec = await pipe.handle(Event(Kind.DEV, text="again"))
    assert rec.error is None and sink.played == [b"Fine."]
    await player.close()


async def test_over_budget_skips_comments_not_gifts(settings):
    settings.session_cost_cap_usd = 1e-9
    result = BrainResult(usage=Usage(0, 90, 10))
    pipe, brain, sink, metrics, outs, player = make(settings, ["Hi."], result)
    await pipe.handle(Event(Kind.DEV, text="first"))
    assert pipe.over_budget()
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="hello"))
    assert rec.error == "over_budget" and len(brain.max_tokens) == 1
    rec = await pipe.handle(Event(Kind.GIFT, viewer="v", gift="Rose"))
    assert rec.error is None and brain.max_tokens[-1] == settings.llm.max_tokens // 2
    await player.close()


async def test_self_harm_flag_printed(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["I care."])
    ev = Event(Kind.COMMENT, viewer="sad", text="i want to kill myself", topics=frozenset({"self_harm"}))
    await pipe.handle(ev)
    assert any(o.startswith("!!! SELF-HARM FLAG from sad: i want to kill myself") for o in outs)
    await player.close()


async def test_history_stores_spoken_text(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["Fine.", "well shit."])
    await pipe.handle(Event(Kind.DEV, text="hi"))
    reply = brain.history[-1][1]
    assert "DEFLECT" in reply and "shit" not in reply
    await player.close()


async def test_log_write_failure_does_not_kill_loop(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["A."])

    def broken(rec):
        raise OSError("disk full")

    metrics.add = broken
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert any("log write failed" in o for o in outs) and rec.say == "A."
    await player.close()


async def test_self_harm_bypasses_over_budget_and_dev_label(settings):
    settings.session_cost_cap_usd = 0.0
    pipe, brain, sink, metrics, outs, player = make(settings, ["I care."])
    ev = Event(Kind.COMMENT, viewer="sad", text="kill myself", topics=frozenset({"self_harm"}))
    rec = await pipe.handle(ev)
    assert any(o.startswith("!!! SELF-HARM FLAG from sad") for o in outs)
    assert rec.error is None and sink.played == [b"I care."]
    assert brain.max_tokens == [settings.llm.max_tokens // 2]
    await pipe.handle(Event(Kind.DEV, text="kill myself", topics=frozenset({"self_harm"})))
    assert any(o.startswith("!!! SELF-HARM FLAG from dev:") for o in outs)
    await player.close()


async def test_deflection_sets_first_sentence_at(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, [], BrainResult(error="timeout"))
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert rec.first_sentence_at is not None and rec.stages()["llm"] is not None
    await player.close()


async def test_over_budget_quiet_skipped_silently_cost_cap(settings):
    settings.session_cost_cap_usd = 1e-9
    pipe, brain, sink, metrics, outs, player = make(settings, ["Hi."], BrainResult(usage=Usage(0, 90, 10)))
    await pipe.handle(Event(Kind.DEV, text="first"))
    outs.clear()
    rec = await pipe.handle(Event(Kind.QUIET))
    assert rec.error == "over_budget" and outs == []
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="hello"))
    assert rec.error == "over_budget" and any("skipped" in o for o in outs)
    await player.close()


async def test_many_tokens_do_not_trip_cost_cap(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, ["Hi."], BrainResult(usage=Usage(3000, 1000, 100)))
    for _ in range(60):
        await pipe.handle(Event(Kind.DEV, text="x"))
    assert not pipe.over_budget()
    await player.close()


async def test_error_shown_and_auth_hint_printed_once(settings):
    res = BrainResult(error="api: AuthenticationError: Error code: 401 - Insufficient Balance")
    pipe, brain, sink, metrics, outs, player = make(settings, [], res)
    await pipe.handle(Event(Kind.DEV, text="hi"))
    await pipe.handle(Event(Kind.DEV, text="again"))
    hint = "!!! DeepSeek rejected the request"
    assert sum(o.startswith(hint) for o in outs) == 1
    assert any("  ! error: api: AuthenticationError" in o for o in outs)
    await player.close()


async def test_non_auth_error_has_no_hint(settings):
    pipe, brain, sink, metrics, outs, player = make(settings, [], BrainResult(error="timeout"))
    await pipe.handle(Event(Kind.DEV, text="hi"))
    assert not any("DeepSeek rejected" in o for o in outs)
    await player.close()


async def test_tts_timeout_recorded_and_loop_continues(settings):
    settings.tts.timeout_s = 0.05
    pipe, brain, sink, metrics, outs, player = make(settings, ["Slow."], tts=FakeTTS(delay=1.0))
    rec = await asyncio.wait_for(pipe.handle(Event(Kind.DEV, text="hi")), 5)
    assert rec.error and "Timeout" in rec.error
    pipe._tts.delay = 0
    brain.sentences = ["Fine."]
    rec = await pipe.handle(Event(Kind.DEV, text="again"))
    assert rec.error is None and sink.played == [b"Fine."]
    await player.close()


class RecordingAvatar:
    def __init__(self, raises=False):
        self.calls, self.raises = [], raises

    def _rec(self, name, *args):
        self.calls.append((name, *args) if args else name)
        if self.raises:
            raise RuntimeError("avatar boom")

    def thinking(self): self._rec("thinking")
    def play_started(self): self._rec("play_started")
    def play_ended(self): self._rec("play_ended")
    def muted(self): self._rec("muted")
    def audio(self, samples, sample_rate): self._rec("audio")
    def react(self, emotion, action): self._rec("react", emotion, action)
    def reply_done(self): self._rec("reply_done")
    def status(self): return "rec"
    async def start(self): pass
    async def close(self): pass
    async def self_test(self, out): pass


def make_av(settings, sentences=(), result=None, raises=False):
    pipe, brain, sink, metrics, outs, player = make(settings, sentences, result)
    av = RecordingAvatar(raises)
    pipe._avatar = av
    return pipe, av, player, metrics


async def test_avatar_sequence_happy_path(settings):
    pipe, av, player, _ = make_av(settings, ["A."], BrainResult(emotion="happy", action="celebrate"))
    await pipe.handle(Event(Kind.DEV, text="hi"))
    assert av.calls == ["thinking", ("react", "happy", "celebrate"), "reply_done"]
    await player.close()


async def test_no_react_when_sentence_blocked(settings):
    pipe, av, player, _ = make_av(settings, ["well shit."], BrainResult(emotion="happy"))
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="hi"))
    assert rec.safety and av.calls == ["thinking", "reply_done"]
    await player.close()


async def test_no_react_on_brain_lag(settings):
    pipe, av, player, _ = make_av(settings, [], BrainResult(emotion="happy"))
    await pipe.handle(Event(Kind.DEV, text="hi"))
    assert av.calls == ["thinking", "reply_done"]
    await player.close()


async def test_no_react_on_brain_error(settings):
    pipe, av, player, _ = make_av(settings, ["A."], BrainResult(error="timeout"))
    await pipe.handle(Event(Kind.DEV, text="hi"))
    assert av.calls == ["thinking", "reply_done"]
    await player.close()


async def test_no_pose_when_skipped_over_budget(settings):
    settings.session_cost_cap_usd = 1e-9
    pipe, av, player, _ = make_av(settings, ["Hi."], BrainResult(usage=Usage(0, 90, 10)))
    await pipe.handle(Event(Kind.DEV, text="first"))
    av.calls.clear()
    rec = await pipe.handle(Event(Kind.COMMENT, viewer="v", text="hello"))
    assert rec.error == "over_budget" and av.calls == []
    await player.close()


async def test_reply_done_called_even_when_handle_fails(settings):
    pipe, av, player, metrics = make_av(settings, ["A."])

    def boom(rec):
        raise ValueError("bad")
    metrics.add = boom
    with pytest.raises(ValueError):
        await pipe.handle(Event(Kind.DEV, text="hi"))
    assert av.calls[-1] == "reply_done"
    await player.close()


async def test_pipeline_speaks_when_avatar_raises(settings):
    pipe, av, player, _ = make_av(settings, ["A."], raises=True)
    rec = await pipe.handle(Event(Kind.DEV, text="hi"))
    assert rec.say == "A." and av.calls[0] == "thinking" and av.calls[-1] == "reply_done"
    await player.close()
