import pytest


class _NoopHotKeys:
    """Stand-in for pynput GlobalHotKeys so tests never install a real OS-wide hook."""
    running = True
    _ready = True

    def __init__(self, mapping): pass
    def start(self): pass
    def is_alive(self): return True
    def stop(self): pass


@pytest.fixture(autouse=True)
def _no_real_hotkeys(monkeypatch):
    monkeypatch.setattr("pynput.keyboard.GlobalHotKeys", _NoopHotKeys)


@pytest.fixture(autouse=True)
def _no_real_avatar(monkeypatch):
    """Never connect to a VTube Studio that happens to be running on the dev machine."""
    from app.avatar.base import NullAvatar
    monkeypatch.setattr("app.main.make_avatar", lambda settings, out: NullAvatar())
