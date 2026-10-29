"""Two-list safety: blocklist (profanity/slurs) and sensitive-topic tagging."""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.events import Event, Kind

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_SINGLE_RUN = re.compile(r"(?<![a-z0-9])[a-z](?: [a-z])+(?![a-z0-9])")


@dataclass
class OutputVerdict:
    ok: bool
    reason: str | None = None


def _clean(s: str, stars: str) -> str:
    """Strip invisible chars, fold width/accents, resolve in-word ! | and *."""
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"(?<=[a-z0-9])[!|](?=[a-z0-9])", "i", s)
    if stars == "drop":
        s = re.sub(r"(?<=[a-z0-9])\*+(?=[a-z0-9])", "", s)
    return s


def _spaced(s: str, stars: str = "space") -> str:
    s = _clean(s, stars).translate(_LEET)
    s = re.sub(r"[\W_]+", " ", s)
    return " ".join(s.split())


def _join_singles(s: str) -> str:
    return " ".join(_SINGLE_RUN.sub(lambda m: m.group().replace(" ", ""), s).split())


def normalize(s: str) -> str:
    """Lowercase, de-leet, strip punctuation, and join runs of single letters."""
    return _join_singles(_spaced(s))


def _forms(s: str) -> set[str]:
    """Every normalized reading of `s` that a term is matched against."""
    out: set[str] = set()
    for stars in ("space", "drop"):
        spaced = _spaced(s, stars)
        for f in (spaced, _join_singles(spaced)):
            out.add(f)
            out.add(re.sub(r"(.)\1{2,}", r"\1", f))
            out.add(re.sub(r"(.)\1{2,}", r"\1\1", f))
    return out


def _compile(term: str) -> re.Pattern[str] | None:
    t = normalize(term)
    return re.compile(rf"\b{re.escape(t)}\b") if t else None


class Safety:
    def __init__(
        self,
        blocklist: list[str],
        topics: dict[str, list[str]],
        max_viewer_chars: int = 200,
        max_name_chars: int = 32,
    ) -> None:
        self.topics = topics
        self.max_viewer_chars = max_viewer_chars
        self.max_name_chars = max_name_chars
        self._block = [p for p in map(_compile, blocklist) if p]
        self._topic = {c: [p for p in map(_compile, ts) if p] for c, ts in topics.items()}

    @classmethod
    def from_files(
        cls, blocklist_path: Path, topics_path: Path, max_viewer_chars: int = 200, max_name_chars: int = 32
    ) -> "Safety":
        lines = Path(blocklist_path).read_text(encoding="utf-8").splitlines()
        block = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        topics = yaml.safe_load(Path(topics_path).read_text(encoding="utf-8")) or {}
        return cls(block, {c: list(ts or []) for c, ts in topics.items()}, max_viewer_chars, max_name_chars)

    def _blocked(self, s: str) -> bool:
        return any(p.search(f) for f in _forms(s) for p in self._block)

    def _topics_in(self, s: str, viewer: str = "") -> frozenset[str]:
        forms = _forms(s)
        names = [re.compile(rf"\b{re.escape(t)}\b") for t in _spaced(viewer).split()]
        if names:
            cleaned = set()
            for f in forms:
                for n in names:
                    f = n.sub(" ", f)
                cleaned.add(f)
            forms = cleaned
        return frozenset(c for c, ps in self._topic.items() if any(p.search(f) for p in ps for f in forms))

    def check_input(self, ev: Event) -> Event | None:
        topics = self._topics_in(ev.text)
        if ev.kind is Kind.DEV:
            return dataclasses.replace(ev, topics=topics)
        if self._blocked(ev.viewer) or self._blocked(ev.text):
            return None
        return dataclasses.replace(
            ev,
            topics=topics,
            text=ev.text[: self.max_viewer_chars],
            viewer=ev.viewer[: self.max_name_chars],
        )

    def check_output(self, sentence: str, input_topics: frozenset[str], viewer: str = "") -> OutputVerdict:
        if self._blocked(sentence):
            return OutputVerdict(False, "blocklist")
        for cat in sorted(self._topics_in(sentence, viewer)):
            if cat != "self_harm" and cat not in input_topics:
                return OutputVerdict(False, f"topic:{cat}")
        return OutputVerdict(True, None)
