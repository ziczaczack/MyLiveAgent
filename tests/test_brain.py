import asyncio
from types import SimpleNamespace

import pytest

from app.brain.llm import Brain
from app.config import LLMSettings
from app.metrics import Usage


@pytest.fixture
def settings_llm():
    return LLMSettings(base_url="http://x", model="m", api_key="k", max_tokens=150,
                       first_token_timeout_s=6.0, history_turns=16,
                       extra_body={"thinking": {"type": "disabled"}}, json_mode=True)


async def _append(lst, s):
    lst.append(s)
    return True


def _chunk(piece):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))], usage=None)


class FakeStream:
    def __init__(self, pieces, delay=0.0):
        self.pieces, self.delay, self.closed = pieces, delay, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for p in self.pieces:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield _chunk(p)
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(
            prompt_cache_hit_tokens=1200, prompt_cache_miss_tokens=300, completion_tokens=80))

    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, pieces=(), delay=0.0, error=None):
        self.pieces, self.delay, self.error = list(pieces), delay, error
        self.last_kwargs = None
        self.stream = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.last_kwargs = kw
        if self.error:
            raise self.error
        self.stream = FakeStream(self.pieces, self.delay)
        return self.stream


FULL = ['{"say": "That was a test. You all', ' passed.", "emotion": "smug", "action": null}']


async def test_streams_sentences_and_sends_config(settings_llm):
    c = FakeClient(FULL)
    got = []
    b = Brain(c, settings_llm, "P")
    r = await b.reply("ev", lambda s: _append(got, s))
    assert got == ["That was a test.", "You all passed."] and r.emotion == "smug" and r.usage == Usage(1200, 300, 80)
    assert r.say == "That was a test. You all passed." and r.error is None and r.first_token_at is not None
    kw = c.last_kwargs
    assert kw["stream"] is True and kw["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kw["response_format"] == {"type": "json_object"} and kw["stream_options"] == {"include_usage": True}
    assert kw["messages"][0] == {"role": "system", "content": "P"}
    assert kw["messages"][-1] == {"role": "user", "content": "ev"}
    assert kw["max_tokens"] == 150 and kw["model"] == "m"


async def test_max_tokens_override_and_no_json_mode(settings_llm):
    settings_llm.json_mode = False
    c = FakeClient(FULL)
    await Brain(c, settings_llm, "P").reply("ev", lambda s: _append([], s), max_tokens=40)
    assert c.last_kwargs["max_tokens"] == 40 and "response_format" not in c.last_kwargs


async def test_stop_after_false(settings_llm):
    c = FakeClient(FULL)
    got = []

    async def cb(s):
        got.append(s)
        return False

    r = await Brain(c, settings_llm, "P").reply("ev", cb)
    assert got == ["That was a test."] and c.stream.closed and r.error is None


async def test_first_token_timeout(settings_llm):
    settings_llm.first_token_timeout_s = 0.05
    c = FakeClient(FULL, delay=0.5)
    got = []
    r = await Brain(c, settings_llm, "P").reply("ev", lambda s: _append(got, s))
    assert r.error == "timeout" and got == []


async def test_api_error(settings_llm):
    c = FakeClient(error=RuntimeError("boom"))
    r = await Brain(c, settings_llm, "P").reply("ev", lambda s: _append([], s))
    assert r.error.startswith("api:") and "RuntimeError: boom" in r.error


async def test_cancel_propagates(settings_llm):
    c = FakeClient(FULL, delay=0.5)
    t = asyncio.create_task(Brain(c, settings_llm, "P").reply("ev", lambda s: _append([], s)))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


async def test_unparseable_json_keeps_streamed_say(settings_llm):
    c = FakeClient(['{"say": "Hello there. Bye now.", "emo'])
    got = []
    r = await Brain(c, settings_llm, "P").reply("ev", lambda s: _append(got, s))
    assert got == ["Hello there.", "Bye now."] and r.say == "Hello there. Bye now." and r.emotion == "neutral"


def test_history_capped(settings_llm):
    settings_llm.history_turns = 2
    b = Brain(None, settings_llm, "P")
    for i in range(3):
        b.remember(f"e{i}", f"s{i}", "happy", None)
    assert len(b.history) == 2 and b.history[0][0] == "e1"
    assert b.history[-1][1] == '{"say": "s2", "emotion": "happy", "action": null}'


async def test_history_goes_into_messages(settings_llm):
    c = FakeClient(FULL)
    b = Brain(c, settings_llm, "P")
    b.remember("e0", "s0", "happy", "celebrate")
    await b.reply("ev", lambda s: _append([], s))
    roles = [m["role"] for m in c.last_kwargs["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
