import asyncio
import threading

from app.hotkey import MuteController, MuteHotkey, combo_is_held


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
        _ready = True

        def is_alive(self):
            return True

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

    hk = MuteHotkey("<ctrl>+<alt>+m", on_press, loop, hotkeys_factory=FakeHotKeys,
                    keys_down=lambda combo: True)
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
        _ready = False

        def __init__(self, mapping): pass
        def start(self): pass
        def is_alive(self): return True
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
                               ingest_factory=Ingest, hotkeys_factory=DeadHook,
                               hotkey_ready_timeout=0.05), 5)
    assert "(hotkey unavailable: keyboard hook could not be installed; use /mute)" in capsys.readouterr().out


async def test_start_raises_within_deadline_when_never_ready():
    import time

    class Never:
        running = False
        _ready = False
        stopped = False

        def __init__(self, mapping): pass
        def start(self): pass
        def stop(self): Never.stopped = True

    hk = MuteHotkey("x", lambda: None, asyncio.get_running_loop(), hotkeys_factory=Never, ready_timeout=0.05)
    t0 = time.monotonic()
    try:
        hk.start()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "could not be installed" in str(e)
    assert time.monotonic() - t0 < 1.0
    assert Never.stopped


async def test_start_succeeds_when_listener_becomes_ready():
    class Slow:
        running = False
        _ready = False

        def __init__(self, mapping): pass

        def start(self):
            import threading
            def go():
                import time
                time.sleep(0.05)
                self.running = True
                self._ready = True
            threading.Thread(target=go).start()

        def stop(self): pass

    hk = MuteHotkey("x", lambda: None, asyncio.get_running_loop(), hotkeys_factory=Slow, ready_timeout=2.0)
    hk.start()
    assert hk._listener is not None


async def test_start_gives_up_early_if_thread_dead():
    class Dead:
        running = False
        _ready = False

        def __init__(self, mapping): pass
        def start(self): pass
        def is_alive(self): return False
        def stop(self): raise OSError("stop failed")

    hk = MuteHotkey("x", lambda: None, asyncio.get_running_loop(), hotkeys_factory=Dead, ready_timeout=30)
    try:
        hk.start()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "could not be installed" in str(e)


def _fire_once_factory(fired):
    class FireOnce:
        running = True
        _ready = True

        def __init__(self, mapping):
            self._cb = list(mapping.values())[0]

        def is_alive(self):
            return True

        def start(self):
            self._cb()
            fired.set()

        def stop(self):
            pass

    return FireOnce


async def test_hotkey_ignores_activation_when_keys_not_really_held():
    # pynput can miss a key release and then fire on Ctrl alone; the physical check stops that.
    calls = []
    fired = threading.Event()
    hk = MuteHotkey("<ctrl>+<alt>+m", lambda: calls.append(1), asyncio.get_running_loop(),
                    hotkeys_factory=_fire_once_factory(fired), keys_down=lambda combo: False)
    hk.start()
    assert fired.is_set()
    await asyncio.sleep(0.02)
    assert calls == []


def test_combo_is_held_checks_every_key():
    down = {0x11, 0x12, ord("M")}
    assert combo_is_held("<ctrl>+<alt>+m", key_state=lambda vk: vk in down)
    down.discard(ord("M"))
    assert not combo_is_held("<ctrl>+<alt>+m", key_state=lambda vk: vk in down)


def test_combo_is_held_allows_unknown_keys():
    # A key we can't map must not make the hotkey dead.
    assert combo_is_held("<ctrl>+<f13>", key_state=lambda vk: vk == 0x11)
