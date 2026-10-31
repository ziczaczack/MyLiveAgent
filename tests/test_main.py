from app.main import cli


def test_main_exits_cleanly_without_key(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("app.main.load_dotenv", lambda *a, **k: None)
    assert cli() == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().out


async def test_ingest_failure_reported_and_bad_line_survives(tmp_path, capsys):
    import asyncio
    from pathlib import Path

    from app.config import load_settings
    from app.events import Event, Kind
    from app.main import run

    class Boom:
        def __init__(self, on_line):
            self.on = on_line

        async def run(self):
            self.on(Event(Kind.COMMENT, viewer='v', text=None))  # bad line must not kill the session
            raise RuntimeError("ingest died")

    class Sink:
        async def play(self, b): pass
        def stop(self): pass

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = tmp_path
    await asyncio.wait_for(run(st, client=object(), tts=object(), sink=Sink(), ingest_factory=Boom), 5)
    out = capsys.readouterr().out
    assert "(input error:" in out
    assert "(console input failed: RuntimeError: ingest died)" in out


async def test_auto_stats_help_commands(tmp_path, capsys):
    import asyncio
    from pathlib import Path

    from app.config import load_settings
    from app.ingest.console import Command
    from app.main import run

    class Script:
        def __init__(self, on_line):
            self.on = on_line

        async def run(self):
            self.on(Command("help"))
            self.on(Command("auto", "off"))
            self.on(Command("auto", "on"))
            self.on(Command("auto", "on"))
            await asyncio.sleep(0.4)
            self.on(Command("stats"))
            self.on(Command("auto", "off"))
            self.on(Command("quit"))

    class Sink:
        async def play(self, b): pass
        def stop(self): pass

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = tmp_path
    st.mock_interval_s = (0.01, 0.02)
    await asyncio.wait_for(run(st, client=object(), tts=object(), sink=Sink(), ingest_factory=Script), 5)
    out = capsys.readouterr().out
    assert "/auto on|off" in out and "/stats" in out and "/quit" in out
    assert "(auto is already off)" in out and "(auto on)" in out and "(auto is already on)" in out
    assert "(auto off)" in out
    assert out.count("replies") >= 2  # /stats plus the exit summary
    assert "! error: api: AttributeError" in out  # mock events reached the pipeline (fake client fails; error now shown)


async def test_logging_goes_to_app_log_not_stderr(tmp_path, capsys):
    import asyncio
    import logging
    from pathlib import Path

    from app.config import load_settings
    from app.main import run

    class Boom:
        def __init__(self, on_line): pass

        async def run(self):
            raise RuntimeError("ingest died")

    class Sink:
        async def play(self, b): pass
        def stop(self): pass

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = tmp_path / "logs"
    before = list(logging.getLogger().handlers)
    await asyncio.wait_for(run(st, client=object(), tts=object(), sink=Sink(), ingest_factory=Boom), 5)
    assert "ingest died" in (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "Traceback" not in capsys.readouterr().err
    assert logging.getLogger().handlers == before


async def test_unwritable_logs_dir_is_a_one_line_error(tmp_path):
    from pathlib import Path

    import pytest

    from app.config import ConfigError, load_settings
    from app.main import run

    blocker = tmp_path / "file"
    blocker.write_text("x")
    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = blocker / "logs"
    with pytest.raises(ConfigError) as e:
        await run(st, client=object(), tts=object(), sink=object())
    assert str(e.value).startswith("(cannot write logs to ")


async def test_yt_command_starts_and_stops_reader(tmp_path, capsys, monkeypatch):
    import asyncio
    from pathlib import Path

    from app.config import load_settings
    from app.events import Event, Kind
    from app.ingest.console import Command
    from app.main import run

    started = []

    class FakeYT:
        def __init__(self, url, settings, out):
            started.append(url)

        async def run(self, emit):
            emit(Event(Kind.COMMENT, viewer="yt_viewer", text="hello from youtube"))
            await asyncio.Event().wait()

    monkeypatch.setattr("app.main.YouTubeChat", FakeYT)

    class Script:
        def __init__(self, on_line):
            self.on = on_line

        async def run(self):
            self.on(Command("yt", ""))
            self.on(Command("yt", "off"))
            self.on(Command("yt", "https://youtu.be/abc"))
            self.on(Command("yt", "https://youtu.be/abc"))
            await asyncio.sleep(0.1)
            self.on(Command("yt", "off"))
            self.on(Command("quit"))

    class Sink:
        async def play(self, b): pass
        def stop(self): pass

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    st.paths.logs_dir = tmp_path
    await asyncio.wait_for(run(st, client=object(), tts=object(), sink=Sink(), ingest_factory=Script), 5)
    out = capsys.readouterr().out
    assert "usage: /yt <live link> | /yt off" in out
    assert "(youtube is already off)" in out
    assert "(youtube on)" in out
    assert "(youtube is already on; /yt off first)" in out
    assert "(youtube off)" in out
    assert started == ["https://youtu.be/abc"]


def test_youtube_settings_loaded():
    from pathlib import Path

    from app.config import load_settings

    st = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "k"})
    assert st.youtube.big_superchat["USD"] == 5
    assert st.youtube.retry_s == 5
