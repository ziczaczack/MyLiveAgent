from app.events import Event, Kind, is_question, mentions_name


def test_mentions_name():
    assert mentions_name("hey MOCHI you there", "Mochi")
    assert mentions_name("@mochi lol", "Mochi")
    assert not mentions_name("mochiko", "Mochi")


def test_is_question():
    assert is_question("u ok?")
    assert not is_question("nice")


def test_event_defaults():
    e = Event(Kind.COMMENT, viewer="a", text="hi")
    assert e.topics == frozenset() and not e.big and e.received_at > 0
