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
    assert s.session_cost_cap_usd == 3.0 and not hasattr(s, "session_token_cap")
    assert s.llm.request_timeout_s == 10 and s.llm.total_reply_timeout_s == 15 and s.tts.timeout_s == 10


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


def _with_avatar(tmp_path, section: str) -> Path:
    text = Path("config/settings.yaml").read_text(encoding="utf-8")
    text = text[: text.index("\navatar:")] + "\n" + section
    p = tmp_path / "s.yaml"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize("section, field", [
    ("avatar: [1, 2]\n", "avatar"),
    ("avatar: yes\n", "avatar"),
    ("avatar:\n  expressions:\n    - EyesLove.exp3.json\n", "expressions"),
    ("avatar:\n  actions: wave\n", "actions"),
    ("avatar:\n  thinking: [FaceAngleZ]\n", "thinking"),
    ("avatar:\n  thinking:\n    FaceAngleZ: lots\n", "thinking"),
    ("avatar:\n  fps: 0\n", "fps"),
    ("avatar:\n  fps: -5\n", "fps"),
    ("avatar:\n  reconnect_s: 0\n", "reconnect_s"),
    ("avatar:\n  mouth_gain: 0\n", "mouth_gain"),
    ("avatar:\n  mouth_smoothing: 1\n", "mouth_smoothing"),
    ("avatar:\n  mouth_smoothing: -0.1\n", "mouth_smoothing"),
    ("avatar:\n  enabled: \"false\"\n", "enabled"),
    ("avatar:\n  enabled: 1\n", "enabled"),
])
def test_bad_avatar_shapes_are_config_errors(tmp_path, section, field):
    p = _with_avatar(tmp_path, section)
    with pytest.raises(ConfigError) as e:
        load_settings(p, {"DEEPSEEK_API_KEY": "k"})
    assert field in str(e.value)


def test_avatar_enabled_false_and_empty_maps_ok(tmp_path):
    p = _with_avatar(tmp_path, "avatar:\n  enabled: false\n  expressions:\n  mouth_smoothing: 0\n")
    s = load_settings(p, {"DEEPSEEK_API_KEY": "k"})
    assert s.avatar.enabled is False and s.avatar.expressions == {} and s.avatar.mouth_smoothing == 0


def test_attribute_error_becomes_config_error(tmp_path):
    text = Path("config/settings.yaml").read_text(encoding="utf-8")
    p = tmp_path / "s.yaml"
    p.write_text(text.replace("\npaths:", "\npaths_old:", 1) + "\npaths: [a, b]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(p, {"DEEPSEEK_API_KEY": "k"})
