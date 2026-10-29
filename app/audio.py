import asyncio
import inspect
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


class AudioSink(Protocol):
    async def play(self, mp3: bytes) -> None:
        """Return when playback finished or stop() was called."""
        ...

    def stop(self) -> None: ...


class SoundDeviceSink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0

    async def play(self, mp3: bytes) -> None:
        generation = self._generation

        def _blocking() -> None:
            import miniaudio
            import numpy as np
            import sounddevice as sd

            decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16)
            samples = np.frombuffer(decoded.samples, dtype=np.int16).reshape(-1, decoded.nchannels)
            with self._lock:
                if self._generation != generation:
                    return  # stop() arrived after play() was called: do not start
                sd.play(samples, decoded.sample_rate)
            sd.wait()

        await asyncio.to_thread(_blocking)

    def stop(self) -> None:
        import sounddevice as sd

        with self._lock:
            self._generation += 1
            sd.stop()


@dataclass
class PlayTimes:
    first_audio_at: float | None
    play_start_at: float | None


class Player:
    def __init__(self, sink: AudioSink, clock: Callable[[], float] = time.perf_counter) -> None:
        self.sink = sink
        self.errors: list[str] = []
        self._clock = clock
        self._queue: asyncio.Queue[Awaitable[bytes | None]] = asyncio.Queue()
        self._pending = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._muted = False
        self._worker: asyncio.Task | None = None
        self._first_audio_at: float | None = None
        self._play_start_at: float | None = None

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    def enqueue(self, audio: Awaitable[bytes | None]) -> None:
        self._pending += 1
        self._idle.clear()
        self._queue.put_nowait(audio)

    async def finish(self) -> PlayTimes:
        await self._idle.wait()
        times = PlayTimes(self._first_audio_at, self._play_start_at)
        self._first_audio_at = None
        self._play_start_at = None
        return times

    def set_muted(self, on: bool) -> None:
        self._muted = on
        if not on:
            return
        self.sink.stop()
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if inspect.iscoroutine(item):
                item.close()
            elif isinstance(item, asyncio.Future):
                if not item.done():
                    item.cancel()
                elif not item.cancelled():
                    exc = item.exception()  # consume so it is not "never retrieved"
                    if exc is not None:
                        self.errors.append(f"{type(exc).__name__}: {exc}")
            self._done()

    async def close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.sink.stop()
        self._drain()
        self._pending = 0
        self._idle.set()

    def _done(self) -> None:
        self._pending -= 1
        if self._pending <= 0:
            self._pending = 0
            self._idle.set()

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._handle(item)
            finally:
                self._done()

    async def _handle(self, item: Awaitable[bytes | None]) -> None:
        try:
            audio = await item
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            self.errors.append("CancelledError: audio task cancelled")
            return
        except Exception as e:
            self.errors.append(f"{type(e).__name__}: {e}")
            return
        if audio is None:
            self.errors.append("TTS returned no audio")
            return
        if self._first_audio_at is None:
            self._first_audio_at = self._clock()
        if self._muted:
            return
        if self._play_start_at is None:
            self._play_start_at = self._clock()
        try:
            await self.sink.play(audio)
        except Exception as e:
            self.errors.append(f"{type(e).__name__}: {e}")
