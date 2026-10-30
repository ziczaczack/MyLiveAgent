import asyncio

from app.audio import Player


class FakeSink:
    def __init__(self, block_on: bytes | None = None):
        self.played: list[bytes] = []
        self.stop_calls = 0
        self._block_on = block_on
        self._stopped = asyncio.Event()
        self.started = asyncio.Event()

    async def play(self, mp3: bytes) -> None:
        self.played.append(mp3)
        self.started.set()
        if mp3 == self._block_on:
            await self._stopped.wait()

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()


async def delayed(data, delay):
    await asyncio.sleep(delay)
    return data


async def boom():
    raise RuntimeError("synth failed")


async def test_plays_in_order_even_if_second_ready_first():
    p = Player(FakeSink())
    p.start()
    p.enqueue(delayed(b"1", 0.05))
    p.enqueue(delayed(b"2", 0.0))
    t = await p.finish()
    assert p.sink.played == [b"1", b"2"]
    assert t.play_start_at is not None and t.first_audio_at is not None
    await p.close()


async def test_failed_item_skipped():
    p = Player(FakeSink())
    p.start()
    p.enqueue(boom())
    p.enqueue(delayed(b"2", 0))
    await p.finish()
    assert p.sink.played == [b"2"]
    assert p.errors == ["RuntimeError: synth failed"]
    await p.close()


async def test_none_result_skipped():
    p = Player(FakeSink())
    p.start()
    p.enqueue(delayed(None, 0))
    t = await p.finish()
    assert p.sink.played == [] and len(p.errors) == 1 and t.play_start_at is None
    await p.close()


async def test_mute_stops_current_and_drops_queue():
    sink = FakeSink(block_on=b"1")
    p = Player(sink)
    p.start()
    p.enqueue(delayed(b"1", 0))
    p.enqueue(delayed(b"2", 0))
    await sink.started.wait()
    p.set_muted(True)
    await asyncio.wait_for(p.finish(), 1)
    assert sink.stop_calls == 1 and sink.played == [b"1"]
    await p.close()


async def test_muted_items_not_played_but_finish_returns():
    p = Player(FakeSink())
    p.start()
    p.set_muted(True)
    p.enqueue(delayed(b"1", 0))
    t = await asyncio.wait_for(p.finish(), 1)
    assert p.sink.played == [] and t.play_start_at is None
    await p.close()


async def test_enqueue_during_playback_and_batches_reset():
    sink = FakeSink(block_on=b"1")
    p = Player(sink)
    p.start()
    p.enqueue(delayed(b"1", 0))
    await sink.started.wait()
    p.enqueue(delayed(b"2", 0))
    sink._stopped.set()
    await p.finish()
    assert sink.played == [b"1", b"2"]
    p.enqueue(delayed(b"3", 0))
    t = await p.finish()
    assert sink.played[-1] == b"3" and t.play_start_at is not None
    await p.close()


async def test_failed_task_dropped_by_mute_is_retrieved(recwarn):
    import gc

    p = Player(FakeSink(block_on=b"0"))
    p.start()
    p.enqueue(delayed(b"0", 0))
    await p.sink.started.wait()
    failing = asyncio.create_task(boom())
    p.enqueue(failing)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert failing.done()
    loop_errors = []
    asyncio.get_running_loop().set_exception_handler(lambda l, c: loop_errors.append(c))
    p.set_muted(True)
    await asyncio.wait_for(p.finish(), 1)
    del failing
    gc.collect()
    assert loop_errors == []
    assert p.errors == ["RuntimeError: synth failed"]
    await p.close()


async def test_close_unblocks_finish():
    p = Player(FakeSink())
    p.enqueue(delayed(b"1", 0))
    await p.close()
    await asyncio.wait_for(p.finish(), 1)


async def test_sound_device_sink_stop_before_playback_starts(monkeypatch):
    import sys
    import threading
    import types

    import miniaudio

    from app.audio import SoundDeviceSink

    calls = []
    fake_sd = types.SimpleNamespace(
        play=lambda *a: calls.append("play"), wait=lambda: None, stop=lambda: calls.append("stop")
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    release = threading.Event()
    entered = threading.Event()

    def fake_decode(data, output_format=None):
        entered.set()
        release.wait(2)
        return types.SimpleNamespace(samples=b"\x00\x00" * 4, nchannels=1, sample_rate=8000)

    monkeypatch.setattr(miniaudio, "decode", fake_decode)
    sink = SoundDeviceSink()
    task = asyncio.create_task(sink.play(b"x"))
    while not entered.is_set():
        await asyncio.sleep(0.01)
    sink.stop()  # mute lands after play() was called, before sd.play
    release.set()
    await task
    assert "play" not in calls


class FakeListener:
    def __init__(self, fail: bool = False):
        self.events: list[str] = []
        self._fail = fail

    def _rec(self, name: str) -> None:
        self.events.append(name)
        if self._fail:
            raise RuntimeError("listener broke")

    def play_started(self) -> None:
        self._rec("started")

    def play_ended(self) -> None:
        self._rec("ended")

    def muted(self) -> None:
        self._rec("muted")


async def test_listener_sees_start_end_per_chunk():
    lis = FakeListener()
    p = Player(FakeSink(), listener=lis)
    p.start()
    p.enqueue(delayed(b"1", 0))
    p.enqueue(delayed(b"2", 0))
    await p.finish()
    assert lis.events == ["started", "ended", "started", "ended"]
    await p.close()


async def test_listener_muted_on_mute():
    lis = FakeListener()
    p = Player(FakeSink(), listener=lis)
    p.set_muted(True)
    assert lis.events == ["muted"]


async def test_listener_errors_do_not_stop_playback():
    p = Player(FakeSink(), listener=FakeListener(fail=True))
    p.start()
    p.enqueue(delayed(b"1", 0))
    p.enqueue(delayed(b"2", 0))
    await p.finish()
    assert p.sink.played == [b"1", b"2"]
    p.set_muted(True)
    await p.close()


def _fake_audio(monkeypatch):
    import sys
    import types

    import numpy as np

    raw = np.array([1, -2, 3, -4], dtype=np.int16)
    calls: list[str] = []
    sd = types.ModuleType("sounddevice")
    sd.play = lambda samples, rate: calls.append("play")
    sd.wait = lambda: calls.append("wait")
    sd.stop = lambda: calls.append("stop")
    ma = types.ModuleType("miniaudio")
    ma.SampleFormat = types.SimpleNamespace(SIGNED16=1)
    ma.decode = lambda mp3, output_format: types.SimpleNamespace(
        samples=raw.tobytes(), nchannels=2, sample_rate=24000
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setitem(sys.modules, "miniaudio", ma)
    return raw, calls


async def test_sink_on_audio_receives_samples_and_rate(monkeypatch):
    from app.audio import SoundDeviceSink

    raw, calls = _fake_audio(monkeypatch)
    got = []
    sink = SoundDeviceSink(on_audio=lambda s, r: got.append((s, r)))
    await sink.play(b"mp3")
    assert calls == ["play", "wait"]
    assert got[0][1] == 24000
    assert got[0][0].dtype.name == "int16" and got[0][0].shape == (2, 2)
    assert got[0][0].ravel().tolist() == raw.tolist()


async def test_sink_on_audio_error_does_not_stop_playback(monkeypatch):
    from app.audio import SoundDeviceSink

    _, calls = _fake_audio(monkeypatch)

    def bad(samples, rate):
        raise RuntimeError("avatar broke")

    await SoundDeviceSink(on_audio=bad).play(b"mp3")
    assert calls == ["play", "wait"]
