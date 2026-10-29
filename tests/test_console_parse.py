from unittest.mock import ANY

from app.events import Event, Kind
from app.ingest.console import Command, ParseError, parse_line


def test_plain_text_is_dev():
    assert parse_line("  you play like a bot  ") == Event(Kind.DEV, text="you play like a bot", received_at=ANY)


def test_comment():
    e = parse_line("/c kaito_77 is the AI even real?")
    assert (e.kind, e.viewer, e.text) == (Kind.COMMENT, "kaito_77", "is the AI even real?")


def test_gift_multiword_and_big():
    e = parse_line("/g mira.s Galaxy Rocket big")
    assert (e.kind, e.viewer, e.gift, e.big) == (Kind.GIFT, "mira.s", "Galaxy Rocket", True)
    assert parse_line("/g mira.s Rose").big is False


def test_follow_and_quiet():
    assert parse_line("/f nightowl").kind == Kind.FOLLOW
    assert parse_line("/quiet").kind == Kind.QUIET


def test_commands():
    assert parse_line("/auto on") == Command("auto", "on")
    assert parse_line("/mute") == Command("mute")
    assert parse_line("/help") == Command("help")


def test_errors_and_blank():
    assert isinstance(parse_line("/c kaito"), ParseError)
    assert isinstance(parse_line("/g kaito"), ParseError)
    assert isinstance(parse_line("/zzz"), ParseError)
    assert parse_line("   ") is None
