from app.main import cli


def test_main_exits_cleanly_without_key(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("app.main.load_dotenv", lambda *a, **k: None)
    assert cli() == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().out
