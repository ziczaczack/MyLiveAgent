import asyncio
import threading
import time

from app.config import YouTubeSettings
from app.events import Kind
from app.ingest.youtube import YouTubeChat, to_event

BIG = {"USD": 5.0, "MYR": 20.0}


def msg(kind="text_message", name="amy", text="hello", **kw):
    m = {"message_type": kind, "author": {"name": name}, "message": text}
    m.update(kw)
    return m


def money(amount, currency, text=None):
    return {"amount": amount, "currency": currency, "text": text or f"{currency}{amount}"}


def test_text_message_is_comment():
    ev = to_event(msg(text="hi Chibi?"), BIG)
    assert (ev.kind, ev.viewer, ev.text) == (Kind.COMMENT, "amy", "hi Chibi?")


def test_empty_text_message_ignored():
    assert to_event(msg(text=""), BIG) is None
    assert to_event(msg(text=None), BIG) is None


def test_super_chat_over_threshold_is_big_gift_with_message():
    ev = to_event(msg("paid_message", text="love the stream", money=money(5.0, "USD", "$5.00")), BIG)
    assert (ev.kind, ev.big, ev.gift, ev.text) == (Kind.GIFT, True, "Super Chat $5.00", "love the stream")


def test_super_chat_under_threshold_is_small():
    ev = to_event(msg("paid_message", money=money(19.0, "MYR", "RM19.00")), BIG)
    assert ev.kind == Kind.GIFT and not ev.big


def test_unknown_currency_is_small():
    ev = to_event(msg("paid_message", money=money(10000, "JPY")), BIG)
    assert ev.kind == Kind.GIFT and not ev.big


def test_super_sticker_without_message():
    ev = to_event(msg("paid_sticker", text=None, money=money(2.0, "USD", "$2.00")), BIG)
    assert (ev.kind, ev.gift, ev.text) == (Kind.GIFT, "Super Sticker $2.00", "")


def test_membership_is_follow():
    ev = to_event(msg("membership_item", name="newbie", text=None), BIG)
    assert (ev.kind, ev.viewer) == (Kind.FOLLOW, "newbie")


def test_unknown_type_ignored():
    assert to_event(msg("viewer_engagement_message"), BIG) is None


def test_missing_author_becomes_empty_viewer():
    ev = to_event({"message_type": "text_message", "message": "yo"}, BIG)
    assert ev.viewer == ""


# --- run(): the library is replaced by fake chat iterators -----------------------------------

SETTINGS = YouTubeSettings(big_superchat=BIG, retry_s=5.0)


def now_us():
    return int(time.time() * 1_000_000)


async def no_sleep(_s):
    await asyncio.sleep(0)


async def test_run_emits_events_then_reports_end():
    out = []
    chats = iter([[msg(text="one", timestamp=now_us()), msg(text="two", timestamp=now_us())]])
    yt = YouTubeChat("url", SETTINGS, out.append, open_chat=lambda url, stop: next(chats), sleep=no_sleep)
    events = []
    await asyncio.wait_for(yt.run(events.append), 2)
    assert [e.text for e in events] == ["one", "two"]
    assert out == ["youtube: reading chat", "youtube: chat ended"]


async def test_backlog_from_before_connect_is_skipped():
    out = []
    old = now_us() - 60_000_000
    chats = iter([[msg(text="old", timestamp=old), msg(text="new", timestamp=now_us()), msg(text="no ts")]])
    yt = YouTubeChat("url", SETTINGS, out.append, open_chat=lambda url, stop: next(chats), sleep=no_sleep)
    events = []
    await asyncio.wait_for(yt.run(events.append), 2)
    assert [e.text for e in events] == ["new", "no ts"]


async def test_transient_errors_retry_and_print_once():
    out, sleeps = [], []
    attempts = iter([ConnectionError("net down"), ConnectionError("net down"), [msg(text="back")]])

    def open_chat(url, stop):
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(s):
        sleeps.append(s)

    yt = YouTubeChat("url", SETTINGS, out.append, open_chat=open_chat, sleep=sleep)
    events = []
    await asyncio.wait_for(yt.run(events.append), 2)
    assert [e.text for e in events] == ["back"]
    assert sleeps == [5.0, 5.0]
    assert out == ["youtube: net down; retrying every 5 s", "youtube: reading chat", "youtube: chat ended"]


async def test_error_mid_stream_retries():
    out = []

    def broken():
        yield msg(text="a", timestamp=now_us())
        raise RuntimeError("parse failed")

    chats = iter([broken(), [msg(text="b", timestamp=now_us())]])
    yt = YouTubeChat("url", SETTINGS, out.append, open_chat=lambda url, stop: next(chats), sleep=no_sleep)
    events = []
    await asyncio.wait_for(yt.run(events.append), 2)
    assert [e.text for e in events] == ["a", "b"]
    assert "youtube: parse failed; retrying every 5 s" in out


async def test_bad_link_stops_without_retry():
    class InvalidURL(Exception):
        pass

    out, calls = [], []

    def open_chat(url, stop):
        calls.append(url)
        raise InvalidURL("not a youtube link")

    yt = YouTubeChat("nope", SETTINGS, out.append, open_chat=open_chat, sleep=no_sleep)
    await asyncio.wait_for(yt.run(lambda e: None), 2)
    assert calls == ["nope"]
    assert out == ["youtube: not a youtube link. Check the link and run /yt again."]


async def test_cancel_while_library_blocks_returns_promptly():
    release = threading.Event()

    def blocking():
        yield msg(text="first", timestamp=now_us())
        release.wait(5)
        yield msg(text="late", timestamp=now_us())

    yt = YouTubeChat("url", SETTINGS, lambda s: None, open_chat=lambda url, stop: blocking(), sleep=no_sleep)
    events = []
    task = asyncio.create_task(yt.run(events.append))
    for _ in range(100):
        if events:
            break
        await asyncio.sleep(0.01)
    t0 = time.perf_counter()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert time.perf_counter() - t0 < 0.5
    release.set()
    await asyncio.sleep(0.05)
    assert [e.text for e in events] == ["first"]  # nothing emitted after stop


# --- pytchat item -> chat dict -----------------------------------------------------------------

class _Author:
    def __init__(self, name):
        self.name = name


class _Item:
    def __init__(self, type, name="amy", message="", amountValue=0.0, amountString="", currency="", timestamp=0):
        self.type, self.author, self.message = type, _Author(name), message
        self.amountValue, self.amountString, self.currency, self.timestamp = amountValue, amountString, currency, timestamp


def test_pytchat_text_item_converted():
    from app.ingest.youtube import from_pytchat

    m = from_pytchat(_Item("textMessage", message="hi", timestamp=1_700_000_000_123))
    assert m["message_type"] == "text_message"
    assert m["timestamp"] == 1_700_000_000_123_000  # ms -> µs
    assert to_event(m, BIG).text == "hi"


def test_pytchat_super_chat_converted():
    from app.ingest.youtube import from_pytchat

    m = from_pytchat(_Item("superChat", message="gg", amountValue=20.0, amountString="RM20.00", currency="MYR"))
    ev = to_event(m, BIG)
    assert (ev.kind, ev.big, ev.gift, ev.text) == (Kind.GIFT, True, "Super Chat RM20.00", "gg")


def test_pytchat_sticker_and_member_converted():
    from app.ingest.youtube import from_pytchat

    assert to_event(from_pytchat(_Item("superSticker", amountValue=1.0, currency="USD")), BIG).kind == Kind.GIFT
    assert to_event(from_pytchat(_Item("newSponsor", name="newbie")), BIG).kind == Kind.FOLLOW
    assert to_event(from_pytchat(_Item("donation")), BIG) is None


def test_real_currency_shapes_count_as_big():
    # pytchat leaves unmapped currencies as the raw prefix, e.g. "MYR "; RM is the local symbol
    assert to_event(msg("paid_message", money=money(20.0, "MYR ")), BIG).big
    assert to_event(msg("paid_message", money=money(25.0, "RM")), BIG).big
    assert to_event(msg("paid_message", money=money(5.0, "$")), BIG).big


async def test_idle_chat_is_paced_and_stops_on_cancel(monkeypatch):
    import sys
    import types

    from app.ingest import youtube

    calls = {"get": 0, "terminated": 0}

    class Data:
        items = []
        interval = 0.05

    class FakeChat:
        def is_alive(self):
            return True

        def get(self):
            calls["get"] += 1
            return Data()

        def raise_for_status(self):
            pass

        def terminate(self):
            calls["terminated"] += 1

    monkeypatch.setitem(sys.modules, "pytchat", types.SimpleNamespace(create=lambda **kw: FakeChat()))
    monkeypatch.setattr(youtube, "_MIN_POLL_S", 0.05)
    yt = YouTubeChat("url", SETTINGS, lambda s: None, sleep=no_sleep)
    task = asyncio.create_task(yt.run(lambda e: None))
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert 2 <= calls["get"] <= 15  # paced by the chat's interval, not a busy loop
    for _ in range(50):
        if calls["terminated"]:
            break
        await asyncio.sleep(0.01)
    assert calls["terminated"] == 1  # the polling thread noticed the stop and closed the chat


def test_bad_youtube_thresholds_rejected():
    import pytest

    from app.config import ConfigError, _youtube

    for bad in ({"big_superchat": {"USD": 0}}, {"big_superchat": {"USD": -1}},
                {"big_superchat": {"USD": float("nan")}}, {"retry_s": 0}):
        with pytest.raises(ConfigError):
            _youtube(bad)
