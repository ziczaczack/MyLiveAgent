import asyncio
import threading

from app.hotkey import MuteController, MuteHotkey


class FakePlayer:
    def __init__(self):
        self.calls = []

    def set_muted(self, on):
        self.calls.append(on)


def test_toggle_twice_returns_to_live(capsys):
    p = FakePlayer()
    m = MuteController(p, print)
    m.toggle()
    assert m.muted is True
    m.toggle()
    assert m.muted is False
    assert p.calls == [True, False]
    assert capsys.readouterr().out.split() == ["[MUTED]", "[LIVE]"]


def test_set_true_mutes_player_and_prints(capsys):
    p = FakePlayer()
    m = MuteController(p, print)
    m.set(True)
    assert p.calls == [True]
    assert capsys.readouterr().out.strip() == "[MUTED]"


def test_set_same_state_is_noop(capsys):
    p = FakePlayer()
    m = MuteController(p, print)
    m.set(False)
    m.set(True)
    m.set(True)
    assert p.calls == [True]
    assert capsys.readouterr().out.split() == ["[MUTED]"]


async def test_hotkey_invokes_on_press_on_loop_thread():
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    seen = asyncio.Future()
    created = {}

    class FakeHotKeys:
        def __init__(self, mapping):
            created["mapping"] = mapping
            self.started = self.stopped = False

        running = True

        def wait(self):
            pass

        def start(self):
            self.started = True
            t = threading.Thread(target=lambda: list(self.mapping_values())[0]())
            created["thread"] = t
            t.start()

        def mapping_values(self):
            return created["mapping"].values()

        def stop(self):
            self.stopped = True

    def on_press():
        seen.set_result(threading.get_ident())

    hk = MuteHotkey("<ctrl>+<alt>+m", on_press, loop, hotkeys_factory=FakeHotKeys)
    hk.start()
    tid = await asyncio.wait_for(seen, 2)
    created["thread"].join()
    assert tid == loop_thread
    assert "<ctrl>+<alt>+m" in created["mapping"]
    listener = hk._listener
    hk.stop()
    assert listener.stopped


async def test_run_warns_when_hotkey_fails_and_mute_command_works(capsys):
    from pathlib import Path

    from app.config import load_settings
    from app.ingest.console import Command
    from app.main import run

    class Ingest:
        def __init__(self, on_line):
            self.on = on_line

        async def run(self):
            self.on(Command("mute"))
            self.on(Command("unmute"))

    class Sink:
        async def play(self, b): pass
        def stop(self): pass

    def bad_factory(mapping):
        raise OSError("no display")

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = Path(__import__("tempfile").mkdtemp())
    await asyncio.wait_for(run(st, client=object(), tts=object(), sink=Sink(),
                               ingest_factory=Ingest, hotkeys_factory=bad_factory), 5)
    out = capsys.readouterr().out
    assert "(hotkey unavailable: no display; use /mute)" in out
    assert out.index("[MUTED]") < out.index("[LIVE]")


async def test_run_warns_when_hook_not_running_after_start(capsys):
    from pathlib import Path

    from app.config import load_settings
    from app.main import run

    class DeadHook:
        running = False

        def __init__(self, mapping): pass
        def start(self): pass
        def wait(self): pass
        def stop(self): pass

    class Ingest:
        def __init__(self, on_line): pass
        async def run(self): pass

    class Sink:
        async def play(self, b): pass
        def stop(self): pass

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = Path(__import__("tempfile").mkdtemp())
    await asyncio.wait_for(run(st, client=object(), tts=object(), sink=Sink(),
                               ingest_factory=Ingest, hotkeys_factory=DeadHook), 5)
    assert "(hotkey unavailable: keyboard hook could not be installed; use /mute)" in capsys.readouterr().out
