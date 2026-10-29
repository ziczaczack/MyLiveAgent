from pathlib import Path

import pytest

from app.config import ConfigError, load_settings, render_persona


def test_loads_shipped_settings():
    s = load_settings(Path("config/settings.yaml"), {"DEEPSEEK_API_KEY": "sk-x"})
    assert s.llm.model == "deepseek-flash"
    assert s.llm.extra_body == {"thinking": {"type": "disabled"}}
    assert s.pricing.peak.output == 1.20 and s.pricing.off_peak.cache_hit == 0.003
    assert s.pricing.peak_windows_utc == [(1, 4), (6, 10)]
    assert s.tts.voice == "en-SG-LunaNeural" and s.llm.api_key == "sk-x"
    assert s.router.max_ordinary_queued == 5 and s.hotkey == "<ctrl>+<alt>+m"
    assert len(s.lines.brain_lag) >= 3 and len(s.lines.deflection) >= 3
    assert s.paths.logs_dir == Path("logs")


@pytest.mark.parametrize("env", [{}, {"DEEPSEEK_API_KEY": "  "}])
def test_missing_key_message(env):
    with pytest.raises(ConfigError) as e:
        load_settings(Path("config/settings.yaml"), env)
    assert "DEEPSEEK_API_KEY" in str(e.value) and ".env" in str(e.value)


def test_missing_settings_file():
    with pytest.raises(ConfigError):
        load_settings(Path("config/nope.yaml"), {"DEEPSEEK_API_KEY": "k"})


def test_missing_keys(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("character_name: x\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(p, {"DEEPSEEK_API_KEY": "k"})


def test_render_persona_fills_and_strips_comments():
    text = render_persona(Path("config/persona.md"), "Mochi", "Lok")
    assert "{{" not in text and "<!--" not in text
    assert text.startswith("# Persona: Mochi")
    assert "co-hosts a live stream on TikTok with your developer, Lok" in text


def test_render_persona_missing_file():
    with pytest.raises(ConfigError):
        render_persona(Path("config/nope.md"), "a", "b")
