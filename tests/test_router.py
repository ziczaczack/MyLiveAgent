import asyncio

from app.config import RouterSettings
from app.events import Event, Kind
from app.router import Router


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    s = RouterSettings(stale_after_s=kw.get("stale", 15.0), quiet_after_s=kw.get("quiet", 20.0),
                       viewer_cooldown_s=kw.get("cool", 10.0), max_ordinary_queued=kw.get("cap", 5))
    return Router(s, "Mimi", clock=clock), clock


def comment(text, viewer="v", t=0.0, **kw):
    return Event(Kind.COMMENT, viewer=viewer, text=text, received_at=t, **kw)


def test_priority_order():
    r, _ = make()
    r.submit(comment("hello", "a"))
    r.submit(Event(Kind.FOLLOW, viewer="b", received_at=0))
    r.submit(Event(Kind.DEV, text="hi", received_at=0))
    r.submit(Event(Kind.GIFT, viewer="c", gift="Lion", big=True, received_at=0))
    r.submit(comment("sad", "d", topics=frozenset({"self_harm"})))
    kinds = []
    while (ev := r.next_ready()) is not None:
        kinds.append((ev.kind, ev.viewer))
    assert kinds == [(Kind.GIFT, "c"), (Kind.COMMENT, "d"), (Kind.FOLLOW, "b"), (Kind.DEV, ""), (Kind.COMMENT, "a")]


def test_mention_and_question_rank_above_ordinary():
    r, _ = make()
    r.submit(comment("plain", "a"))
    r.submit(comment("hey Mimi", "b"))
    r.submit(comment("why?", "c"))
    assert [r.next_ready().viewer for _ in range(3)] == ["b", "c", "a"]


def test_fifo_within_priority():
    r, _ = make()
    for v in "abc":
        r.submit(Event(Kind.FOLLOW, viewer=v, received_at=0))
    assert [r.next_ready().viewer for _ in range(3)] == ["a", "b", "c"]


def test_stale_ordinary_dropped():
    r, c = make()
    r.submit(comment("plain", "a", t=0))
    r.submit(comment("hi Mimi", "b", t=0))
    r.submit(comment("sad", "d", t=0, topics=frozenset({"self_harm"})))
    c.t = 16
    assert r.next_ready().viewer == "d"
    assert r.next_ready().viewer == "b"
    assert r.next_ready() is None


def test_ordinary_cap_keeps_newest():
    r, _ = make(cap=2, cool=0)
    for i in range(4):
        r.submit(comment(f"c{i}", f"v{i}"))
    assert [r.next_ready().text for _ in range(2)] == ["c2", "c3"]
    assert r.next_ready() is None


def test_rate_limit_comments_not_gifts():
    r, c = make()
    assert r.submit(comment("a", "v"))
    c.t = 5
    assert not r.submit(comment("b", "v"))
    assert r.submit(Event(Kind.GIFT, viewer="v", gift="Rose", received_at=5))
    c.t = 10.1
    assert r.submit(comment("c", "v"))


def test_self_harm_exempt_from_rate_limit():
    r, _ = make()
    assert r.submit(comment("a", "v"))
    assert r.submit(comment("sad", "v", topics=frozenset({"self_harm"})))


async def test_quiet_after_idle():
    s = RouterSettings(stale_after_s=15, quiet_after_s=0.05, viewer_cooldown_s=1, max_ordinary_queued=5)
    r = Router(s, "Mimi")
    ev = await asyncio.wait_for(r.get(), 2)
    assert ev.kind == Kind.QUIET


async def test_quiet_not_repeated_immediately():
    import time
    s = RouterSettings(stale_after_s=15, quiet_after_s=0.1, viewer_cooldown_s=1, max_ordinary_queued=5)
    r = Router(s, "Mimi")
    t0 = time.perf_counter()
    assert (await asyncio.wait_for(r.get(), 2)).kind == Kind.QUIET
    assert (await asyncio.wait_for(r.get(), 2)).kind == Kind.QUIET
    assert time.perf_counter() - t0 >= 0.19


async def test_get_wakes_on_submit():
    import time
    s = RouterSettings(stale_after_s=15, quiet_after_s=60, viewer_cooldown_s=1, max_ordinary_queued=5)
    r = Router(s, "Mimi")
    task = asyncio.create_task(r.get())
    await asyncio.sleep(0.01)
    r.submit(Event(Kind.FOLLOW, viewer="x"))
    ev = await asyncio.wait_for(task, 1)
    assert ev.viewer == "x"


async def test_mark_handled_defers_quiet():
    import time
    s = RouterSettings(stale_after_s=15, quiet_after_s=0.15, viewer_cooldown_s=1, max_ordinary_queued=5)
    r = Router(s, "Mimi")
    await asyncio.sleep(0.1)
    r.mark_handled()
    t0 = time.perf_counter()
    assert (await asyncio.wait_for(r.get(), 2)).kind == Kind.QUIET
    assert time.perf_counter() - t0 >= 0.14
