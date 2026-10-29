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
    assert "reply failed" in out  # mock events reached the pipeline (fake client fails)
