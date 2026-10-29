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
