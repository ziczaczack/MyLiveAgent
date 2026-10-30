"""Console entry point: typed events -> safety -> brain -> TTS -> speakers."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from pathlib import Path

import openai
import yaml
from dotenv import load_dotenv

from app.audio import Player, SoundDeviceSink
from app.avatar.base import Avatar, NullAvatar
from app.avatar.controller import VTSAvatar
from app.brain.llm import Brain
from app.config import ConfigError, Settings, load_settings, render_persona
from app.events import Event
from app.hotkey import MuteController, MuteHotkey
from app.ingest.console import Command, ConsoleIngest, ParseError
from app.ingest.mock import MockFeed
from app.metrics import SessionMetrics
from app.pipeline import Pipeline
from app.router import Router
from app.safety import Safety
from app.supervise import supervise
from app.tts.edge import EdgeTTS

log = logging.getLogger(__name__)

HELP = """Commands:
  <text>            talk as the dev
  /c <name> <text>  viewer comment
  /g <name> <gift> [big]  gift
  /f <name>         new follower
  /quiet            trigger idle self-talk
  /auto on|off      start or stop the mock viewer feed
  /mute, /unmute    silence or restore the voice
  /stats            latency percentiles, cost, tokens
  /avatar           avatar status and a quick expression test
  /help             this list
  /quit             end the session"""

HINT = "Type to talk as {dev}, or hold Alt+Space to dictate. /help for commands."


def make_avatar(settings: Settings, out) -> Avatar:
    return VTSAvatar(settings.avatar, out) if settings.avatar.enabled else NullAvatar()


async def run(settings: Settings, *, client=None, tts=None, sink=None, ingest_factory=ConsoleIngest,
              hotkeys_factory=None, hotkey_ready_timeout: float = 1.0) -> None:
    persona = render_persona(settings.paths.persona, settings.character_name, settings.dev_name)
    try:
        safety = Safety.from_files(settings.paths.blocklist, settings.paths.topics,
                                   settings.max_viewer_chars, settings.max_viewer_name_chars)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Could not load safety lists: {e}") from None
    try:
        metrics = SessionMetrics(settings.paths.logs_dir, time.perf_counter())
        log_handler = logging.FileHandler(Path(settings.paths.logs_dir) / "app.log", encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"(cannot write logs to {settings.paths.logs_dir}: {e})") from None
    log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(log_handler)
    root.setLevel(logging.INFO)
    try:
        await _run(settings, persona, safety, metrics, client, tts, sink, ingest_factory,
                   hotkeys_factory, hotkey_ready_timeout)
    finally:
        root.removeHandler(log_handler)
        log_handler.close()


async def _run(settings, persona, safety, metrics, client, tts, sink, ingest_factory,
               hotkeys_factory, hotkey_ready_timeout) -> None:
    own_client = client is None
    if own_client:
        client = openai.AsyncOpenAI(base_url=settings.llm.base_url, api_key=settings.llm.api_key,
                                     timeout=settings.llm.request_timeout_s, max_retries=1)
    brain = Brain(client, settings.llm, persona)
    avatar = make_avatar(settings, print)
    player = Player(sink or SoundDeviceSink(on_audio=avatar.audio), listener=avatar)
    player.start()
    pipeline = Pipeline(brain, safety, tts or EdgeTTS(settings.tts), player, metrics, settings,
                        avatar=avatar)

    router = Router(settings.router, settings.character_name)
    stop = asyncio.Event()
    mute = MuteController(player, print)
    mock_task: asyncio.Task | None = None
    test_task: asyncio.Task | None = None

    def auto(arg: str) -> None:
        nonlocal mock_task
        want = arg.strip().lower()
        if want not in ("on", "off"):
            print("usage: /auto on|off")
            return
        running = mock_task is not None and not mock_task.done()
        if want == "on" and running:
            print("(auto is already on)")
        elif want == "off" and not running:
            print("(auto is already off)")
        elif want == "on":
            try:
                chat = yaml.safe_load(settings.paths.mock_chat.read_text(encoding="utf-8"))
                feed = MockFeed(chat, random.Random(), settings.mock_interval_s,
                                character_name=settings.character_name)
            except (OSError, yaml.YAMLError, KeyError, TypeError) as e:
                print(f"(auto unavailable: {type(e).__name__}: {e})")
                return
            mock_task = asyncio.create_task(
                supervise("mock feed", lambda: feed.run(on_line), print, stop=stop))
            print("(auto on)")
        else:
            mock_task.cancel()
            mock_task = None
            print("(auto off)")

    def avatar_test() -> None:
        nonlocal test_task
        print(avatar.status())
        if test_task is not None and not test_task.done():
            return
        test_task = asyncio.create_task(avatar.self_test(print))

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
            elif isinstance(item, Command) and item.name == "help":
                print(HELP)
            elif isinstance(item, Command) and item.name == "stats":
                print(metrics.summary())
            elif isinstance(item, Command) and item.name == "avatar":
                avatar_test()
            elif isinstance(item, Command) and item.name == "auto":
                auto(item.arg)
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
    async def console() -> None:
        # A dead console ends the session (no stdin to restart from); report it and stop.
        try:
            await ingest_factory(on_line).run()
        except Exception as e:  # noqa: BLE001
            log.error("console input failed", exc_info=True)
            print(f"(console input failed: {type(e).__name__}: {e})")
        finally:
            stop.set()

    await avatar.start()
    tasks = [asyncio.create_task(supervise("worker", worker, print, stop=stop)),
             asyncio.create_task(supervise("console", console, print, stop=stop))]
    try:
        await stop.wait()
    finally:
        try:
            hotkey.stop()
        except Exception:  # noqa: BLE001 - must not skip task cancellation / player.close
            log.exception("hotkey stop failed")
        if mock_task is not None:
            tasks.append(mock_task)
        if test_task is not None:
            tasks.append(test_task)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print(metrics.summary())
        try:
            await avatar.close()
        except Exception:  # noqa: BLE001 - must not skip player.close
            log.exception("avatar close failed")
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
