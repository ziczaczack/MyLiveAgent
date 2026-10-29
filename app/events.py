"""Event types flowing through the router and brain."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum


class Kind(StrEnum):
    DEV = "dev"
    COMMENT = "comment"
    GIFT = "gift"
    FOLLOW = "follow"
    QUIET = "quiet"


@dataclass
class Event:
    kind: Kind
    viewer: str = ""
    text: str = ""
    gift: str = ""
    big: bool = False
    topics: frozenset[str] = frozenset()
    received_at: float = field(default_factory=time.perf_counter)


def mentions_name(text: str, name: str) -> bool:
    """True if `name` appears as a whole word (case-insensitive, '@' allowed)."""
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def is_question(text: str) -> bool:
    return "?" in text or "？" in text
