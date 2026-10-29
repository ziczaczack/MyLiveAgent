import random
from pathlib import Path

import yaml

from app.events import Kind
from app.ingest.mock import MockFeed

CHAT = yaml.safe_load(Path("config/mock_chat.yaml").read_text(encoding="utf-8"))


def test_chat_file_sizes():
    assert len(CHAT["viewers"]) >= 15 and len(CHAT["comments"]) >= 30 and len(CHAT["questions"]) >= 10
    assert len(CHAT["small_gifts"]) >= 5 and len(CHAT["big_gifts"]) >= 3


async def test_mock_emits_all_kinds():
    class Stop(Exception):
        pass

    n = 0

    async def sleep(_s):
        nonlocal n
        n += 1
        if n > 300:
            raise Stop

    got = []
    feed = MockFeed(CHAT, random.Random(1), (0.0, 0.0), sleep, character_name="Mimi")
    try:
        await feed.run(got.append)
    except Stop:
        pass
    assert len(got) == 300
    kinds = {e.kind for e in got}
    assert {Kind.COMMENT, Kind.GIFT, Kind.FOLLOW} <= kinds
    gifts = [e for e in got if e.kind is Kind.GIFT]
    assert any(g.big for g in gifts) and any(not g.big for g in gifts)
    assert all(e.viewer in CHAT["viewers"] for e in got)
    assert any("Mimi" in e.text for e in got if e.kind is Kind.COMMENT)
