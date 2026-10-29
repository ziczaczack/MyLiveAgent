"""Console entry point: typed events -> safety -> brain -> TTS -> speakers."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import openai
import yaml
from dotenv import load_dotenv

from app.audio import Player, SoundDeviceSink
from app.brain.llm import Brain
from app.config import ConfigError, Settings, load_settings, render_persona
from app.events import Event
from app.hotkey import MuteController, MuteHotkey
from app.ingest.console import Command, ConsoleIngest, ParseError
from app.metrics import SessionMetrics
from app.pipeline import Pipeline
from app.router import Router
from app.safety import Safety
from app.tts.edge import EdgeTTS

log = logging.getLogger(__name__)

HINT = "Type to talk as {dev}, or hold Alt+Space to dictate. /help for commands."


async def run(settings: Settings, *, client=None, tts=None, sink=None, ingest_factory=ConsoleIngest,
              hotkeys_factory=None, hotkey_ready_timeout: float = 1.0) -> None:
    persona = render_persona(settings.paths.persona, settings.character_name, settings.dev_name)
    try:
        safety = Safety.from_files(settings.paths.blocklist, settings.paths.topics,
                                   settings.max_viewer_chars, settings.max_viewer_name_chars)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Could not load safety lists: {e}") from None
    own_client = client is None
    if own_client:
        client = openai.AsyncOpenAI(base_url=settings.llm.base_url, api_key=settings.llm.api_key)
    brain = Brain(client, settings.llm, persona)
    player = Player(sink or SoundDeviceSink())
    player.start()
    metrics = SessionMetrics(settings.paths.logs_dir, time.perf_counter())
    pipeline = Pipeline(brain, safety, tts or EdgeTTS(settings.tts), player, metrics, settings)

    router = Router(settings.router, settings.character_name)
    stop = asyncio.Event()
    mute = MuteController(player, print)

    def on_line(item) -> None:
        try:
            if isinstance(item, Event):
                ev = safety.check_input(item)
                if ev is None:
                    print("(dropped: blocked words)")
                else:
                    if not router.submit(ev):
                        print(f"(rate-limited: {ev.viewer})")
            elif isinstance(item, ParseError):
                print(item.message)
            elif isinstance(item, Command) and item.name == "quit":
                stop.set()
            elif isinstance(item, Command) and item.name in ("mute", "unmute"):
                mute.set(item.name == "mute")
            elif isinstance(item, Command):
                print(f"/{item.name}: not available yet")
        except Exception as e:  # noqa: BLE001 - one bad line must not end the session
            log.exception("input error")
            print(f"(input error: {type(e).__name__}: {e})")

    async def worker() -> None:
        while True:
            ev = await router.get()
            try:
                await pipeline.handle(ev, routed_at=time.perf_counter())
            except Exception as e:  # noqa: BLE001 - the loop must never die
                log.exception("reply failed")
                print(f"(reply failed: {type(e).__name__}: {e})")
            finally:
                router.mark_handled()

    print(f"{settings.character_name} | voice {settings.tts.voice} | model {settings.llm.model}")
    print(HINT.format(dev=settings.dev_name))
    hotkey = MuteHotkey(settings.hotkey, mute.toggle, asyncio.get_running_loop(), hotkeys_factory,
                         hotkey_ready_timeout)
    try:
        hotkey.start()
    except Exception as e:  # noqa: BLE001 - no hotkey must not stop the stream
        print(f"(hotkey unavailable: {e}; use /mute)")
    tasks = [asyncio.create_task(worker()), asyncio.create_task(ingest_factory(on_line).run())]
    tasks[1].add_done_callback(lambda _t: stop.set())
    try:
        await stop.wait()
    finally:
        try:
            hotkey.stop()
        except Exception:  # noqa: BLE001 - must not skip task cancellation / player.close
            log.exception("hotkey stop failed")
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        err = results[1]
        if isinstance(err, Exception):
            log.error("console input failed", exc_info=err)
            print(f"(console input failed: {type(err).__name__}: {err})")
        print(metrics.summary())
        await player.close()
        if own_client:
            await client.close()


def cli() -> int:
    load_dotenv()
    try:
        settings = load_settings(Path("config/settings.yaml"), os.environ)
        asyncio.run(run(settings))
    except ConfigError as e:
        print(e)
        return 1
    except KeyboardInterrupt:
        pass
    return 0
