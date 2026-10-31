"""Settings loading and persona rendering."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """A one-line, user-facing configuration problem."""


@dataclass
class LLMSettings:
    base_url: str
    model: str
    api_key: str
    max_tokens: int
    first_token_timeout_s: float
    request_timeout_s: float
    total_reply_timeout_s: float
    history_turns: int
    extra_body: dict
    json_mode: bool


@dataclass
class Rates:
    cache_hit: float
    cache_miss: float
    output: float


@dataclass
class Pricing:
    off_peak: Rates
    peak: Rates
    peak_windows_utc: list[tuple[int, int]]


@dataclass
class RouterSettings:
    stale_after_s: float
    quiet_after_s: float
    viewer_cooldown_s: float
    max_ordinary_queued: int
    priority_stale_after_s: float = 30.0  # questions/mentions and follows
    quiet_max_s: float = 180.0  # cap for the quiet-chat backoff


@dataclass
class TTSSettings:
    voice: str
    rate: str
    pitch: str
    timeout_s: float


@dataclass
class Lines:
    brain_lag: list[str]
    deflection: list[str]


@dataclass
class Paths:
    persona: Path
    blocklist: Path
    topics: Path
    mock_chat: Path
    logs_dir: Path


@dataclass
class AvatarSettings:
    enabled: bool = False
    url: str = "ws://localhost:8001"
    plugin_name: str = "MyLiveAgent"
    plugin_developer: str = "Lok"
    token_file: Path = Path("config/.vts_token")
    reconnect_s: float = 5.0
    fps: float = 30.0
    mouth_gain: float = 4.0
    mouth_smoothing: float = 0.4
    expressions: dict[str, str] = field(default_factory=dict)  # emotion -> expression file
    actions: dict[str, str] = field(default_factory=dict)  # action -> hotkey name or ID
    thinking: dict[str, float] = field(default_factory=dict)  # parameter -> value


@dataclass
class YouTubeSettings:
    big_superchat: dict[str, float] = field(default_factory=dict)  # currency -> amount counted as big
    retry_s: float = 5.0


def _youtube(raw) -> YouTubeSettings:
    if raw is None:
        return YouTubeSettings()
    if not isinstance(raw, dict):
        raise ConfigError(f"youtube must be a mapping of settings, got {type(raw).__name__}")
    big = raw.get("big_superchat") or {}
    if not isinstance(big, dict):
        raise ConfigError(f"youtube.big_superchat must be a mapping of currency: amount, got {type(big).__name__}")
    try:
        amounts = {str(k).upper(): float(v) for k, v in big.items()}
        retry_s = float(raw.get("retry_s", YouTubeSettings.retry_s))
    except (TypeError, ValueError) as e:
        raise ConfigError(f"youtube settings must be numbers: {e}") from None
    if retry_s <= 0:
        raise ConfigError(f"youtube.retry_s must be greater than 0, got {retry_s!r}")
    return YouTubeSettings(big_superchat=amounts, retry_s=retry_s)


def _avatar_map(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"avatar.{key} must be a mapping of name: value, got {type(value).__name__}")
    return value


def _avatar_number(raw: dict, key: str, default: float, ok, rule: str) -> float:
    value = raw.get(key, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"avatar.{key} must be a number, got {value!r}") from None
    if not ok(number):
        raise ConfigError(f"avatar.{key} must be {rule}, got {value!r}")
    return number


def _avatar(raw) -> AvatarSettings:
    if raw is None:
        return AvatarSettings()
    if not isinstance(raw, dict):
        raise ConfigError(f"avatar must be a mapping of settings, got {type(raw).__name__}")
    d = AvatarSettings()
    enabled = raw.get("enabled", d.enabled)
    if not isinstance(enabled, bool):  # "false" as a string would otherwise turn the avatar on
        raise ConfigError(f"avatar.enabled must be true or false, got {enabled!r}")
    thinking = {}
    for k, v in _avatar_map(raw, "thinking").items():
        try:
            thinking[str(k)] = float(v)
        except (TypeError, ValueError):
            raise ConfigError(f"avatar.thinking.{k} must be a number, got {v!r}") from None
    return AvatarSettings(
        enabled=enabled,
        url=str(raw.get("url", d.url)),
        plugin_name=str(raw.get("plugin_name", d.plugin_name)),
        plugin_developer=str(raw.get("plugin_developer", d.plugin_developer)),
        token_file=Path(raw.get("token_file", d.token_file)),
        reconnect_s=_avatar_number(raw, "reconnect_s", d.reconnect_s, lambda x: x > 0, "greater than 0"),
        fps=_avatar_number(raw, "fps", d.fps, lambda x: x > 0, "greater than 0"),
        mouth_gain=_avatar_number(raw, "mouth_gain", d.mouth_gain, lambda x: x > 0, "greater than 0"),
        mouth_smoothing=_avatar_number(raw, "mouth_smoothing", d.mouth_smoothing,
                                       lambda x: 0 <= x < 1, "at least 0 and less than 1"),
        expressions={str(k): str(v) for k, v in _avatar_map(raw, "expressions").items()},
        actions={str(k): str(v) for k, v in _avatar_map(raw, "actions").items()},
        thinking=thinking,
    )


@dataclass
class Settings:
    character_name: str
    dev_name: str
    session_cost_cap_usd: float
    max_viewer_chars: int
    max_viewer_name_chars: int
    hotkey: str
    llm: LLMSettings
    pricing: Pricing
    router: RouterSettings
    tts: TTSSettings
    lines: Lines
    paths: Paths
    mock_interval_s: tuple[float, float]
    avatar: AvatarSettings = field(default_factory=AvatarSettings)
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)


def load_settings(path: Path, env: Mapping[str, str]) -> Settings:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Settings file not found: {path}") from None
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Could not read settings file {path}: {e}") from None
    try:
        llm = raw["llm"]
        key_env = llm["api_key_env"]
        api_key = (env.get(key_env) or "").strip()
        if not api_key:
            raise ConfigError(
                f"{key_env} is not set. Copy .env.example to .env and paste your DeepSeek key."
            )
        pr = raw["pricing"]
        return Settings(
            character_name=raw["character_name"],
            dev_name=raw["dev_name"],
            session_cost_cap_usd=float(raw["session_cost_cap_usd"]),
            max_viewer_chars=int(raw["max_viewer_chars"]),
            max_viewer_name_chars=int(raw["max_viewer_name_chars"]),
            hotkey=raw["hotkey"],
            llm=LLMSettings(
                base_url=llm["base_url"],
                model=llm["model"],
                api_key=api_key,
                max_tokens=int(llm["max_tokens"]),
                first_token_timeout_s=float(llm["first_token_timeout_s"]),
                request_timeout_s=float(llm["request_timeout_s"]),
                total_reply_timeout_s=float(llm["total_reply_timeout_s"]),
                history_turns=int(llm["history_turns"]),
                extra_body=dict(llm.get("extra_body") or {}),
                json_mode=bool(llm["json_mode"]),
            ),
            pricing=Pricing(
                off_peak=Rates(**pr["off_peak"]),
                peak=Rates(**pr["peak"]),
                peak_windows_utc=[(int(a), int(b)) for a, b in pr["peak_windows_utc"]],
            ),
            router=RouterSettings(**raw["router"]),
            tts=TTSSettings(**{**raw["tts"], "timeout_s": float(raw["tts"]["timeout_s"])}),
            lines=Lines(
                brain_lag=list(raw["lines"]["brain_lag"]),
                deflection=list(raw["lines"]["deflection"]),
            ),
            paths=Paths(**{k: Path(v) for k, v in raw["paths"].items()}),
            mock_interval_s=tuple(float(x) for x in raw["mock"]["interval_s"]),
            avatar=_avatar(raw.get("avatar")),
            youtube=_youtube(raw.get("youtube")),
        )
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ConfigError(f"Settings file {path} is missing or has a bad value: {e}") from None


def render_persona(path: Path, name: str, dev: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ConfigError(f"Persona file not found: {path}") from None
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = text.replace("{{NAME}}", name).replace("{{DEV}}", dev)
    return text.lstrip("\r\n \t")
