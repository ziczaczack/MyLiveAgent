"""Console input: parse typed lines into events or commands."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.events import Event, Kind

COMMANDS = {"auto", "mute", "unmute", "stats", "quit", "help", "avatar"}

_USAGE = {
    "c": "usage: /c <viewer> <comment text>",
    "g": "usage: /g <viewer> <gift name> [big]",
    "f": "usage: /f <viewer>",
}


@dataclass
class Command:
    name: str
    arg: str = ""


@dataclass
class ParseError:
    message: str


def parse_line(line: str) -> Event | Command | ParseError | None:
    line = line.strip()
    if not line:
        return None
    if not line.startswith("/"):
        return Event(Kind.DEV, text=line)

    head, _, rest = line[1:].partition(" ")
    head, rest = head.lower(), rest.strip()

    if head == "c":
        viewer, _, text = rest.partition(" ")
        text = text.strip()
        if not viewer or not text:
            return ParseError(_USAGE["c"])
        return Event(Kind.COMMENT, viewer=viewer, text=text)
    if head == "g":
        viewer, _, gift = rest.partition(" ")
        words = gift.split()
        big = len(words) > 1 and words[-1].lower() == "big"
        if big:
            words = words[:-1]
        if not viewer or not words:
            return ParseError(_USAGE["g"])
        return Event(Kind.GIFT, viewer=viewer, gift=" ".join(words), big=big)
    if head == "f":
        if not rest or " " in rest:
            return ParseError(_USAGE["f"])
        return Event(Kind.FOLLOW, viewer=rest)
    if head == "quiet":
        return Event(Kind.QUIET)
    if head in COMMANDS:
        return Command(head, rest)
    return ParseError(f"unknown command /{head}. Type /help for the list.")


class ConsoleIngest:
    """Reads typed lines (and OS dictation typed into the console) until quit/EOF."""

    def __init__(self, on_line: Callable[[Event | Command | ParseError], None]) -> None:
        self._on_line = on_line

    async def run(self) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout

        session = PromptSession()
        with patch_stdout():
            while True:
                try:
                    line = await session.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    self._on_line(Command("quit"))
                    return
                parsed = parse_line(line)
                if parsed is None:
                    continue
                self._on_line(parsed)
                if isinstance(parsed, Command) and parsed.name == "quit":
                    return
