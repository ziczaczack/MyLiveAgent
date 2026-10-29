"""Settings loading and persona rendering."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
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
        )
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"Settings file {path} is missing or has a bad value: {e}") from None


def render_persona(path: Path, name: str, dev: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ConfigError(f"Persona file not found: {path}") from None
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = text.replace("{{NAME}}", name).replace("{{DEV}}", dev)
    return text.lstrip("\r\n \t")
