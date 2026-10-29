from pathlib import Path

from app.events import Event, Kind
from app.safety import OutputVerdict, Safety

S = Safety(
    blocklist=["fuck", "shit", "ass"],
    topics={"politics": ["election", "prime minister"], "self_harm": ["kill myself", "suicide"], "race": ["racist"]},
)
C = lambda text, viewer="v": Event(Kind.COMMENT, viewer=viewer, text=text)


def test_blocked_comment_drops(): assert S.check_input(C("you are sh1t")) is None
def test_spaced_letters_blocked(): assert S.check_input(C("f u c k this")) is None
def test_no_scunthorpe(): assert S.check_input(C("first class assets")) is not None
def test_blocked_viewer_name_drops(): assert S.check_input(C("hi", viewer="f.u.c.k")) is None


def test_dev_never_dropped_but_tagged():
    e = S.check_input(Event(Kind.DEV, text="shit, who won the election"))
    assert e is not None and e.topics == {"politics"}


def test_topic_tagged_and_passed(): assert S.check_input(C("thoughts on the Prime Minister?")).topics == {"politics"}
def test_truncates(): assert len(Safety([], {}, max_viewer_chars=10).check_input(C("x" * 50)).text) == 10


def test_truncates_name_and_returns_copy():
    ev = C("hi", viewer="n" * 50)
    out = Safety([], {}, max_name_chars=8).check_input(ev)
    assert out is not ev and out.viewer == "n" * 8 and ev.viewer == "n" * 50


def test_dev_text_not_truncated():
    assert len(Safety([], {}, max_viewer_chars=10).check_input(Event(Kind.DEV, text="x" * 50)).text) == 50


def test_gift_without_text_passes():
    assert S.check_input(Event(Kind.GIFT, viewer="v", gift="Rose")) is not None


def test_output_blocklist_always(): assert S.check_output("well shit", frozenset({"politics"})) == OutputVerdict(False, "blocklist")
def test_output_topic_same_category_ok(): assert S.check_output("No election talk, above my pay grade.", frozenset({"politics"})).ok
def test_output_topic_unprompted_replaced(): assert S.check_output("Who won the election anyway", frozenset()) == OutputVerdict(False, "topic:politics")
def test_output_self_harm_never_replaced(): assert S.check_output("Please call a suicide helpline or someone you trust.", frozenset()).ok


def test_shipped_files_load():
    s = Safety.from_files(Path("config/blocklist.txt"), Path("config/topics.yaml"), 200, 32)
    assert set(s.topics) == {"politics", "religion", "race", "sexual", "minors", "self_harm"}
    assert s.check_input(C("i want to kill myself")).topics == {"self_harm"}


def test_shipped_blocklist_spares_roast_words_and_numbers():
    s = Safety.from_files(Path("config/blocklist.txt"), Path("config/topics.yaml"), 200, 32)
    for text in ["damn that sucks", "what the hell, so stupid", "score 455 to 300", "gg I won the race", "he got a headshot, killed"]:
        assert s.check_input(C(text)) is not None, text
    assert s.check_input(C("what a b1tch")) is None


REAL = Safety.from_files(Path("config/blocklist.txt"), Path("config/topics.yaml"), 200, 32)


def test_obfuscations_blocked():
    for t in ["f*ck", "f**k", "sh!t", "b!tch", "fuuuck", "fuckin", "fck", "fuk", "phuck", "fvck",
              "shithole", "ｆｕｃｋ", "fu​ck", "shït", "kys", "kill yourself"]:
        assert REAL.check_input(C(t)) is None, t
    assert S.check_input(C("fuuuck")) is None


def test_clean_text_still_passes():
    for t in ["first class assets", "455", "killed", "headshot", "damn", "best sial", "wow!", "great * game"]:
        assert REAL.check_input(C(t)) is not None, t


def test_viewer_name_excluded_from_topic_check():
    assert REAL.check_output("Thanks Christian for the rose!", frozenset(), viewer="Christian").ok
    assert not REAL.check_output("Thanks Christian for the rose!", frozenset()).ok


def test_ambiguous_topic_terms_removed():
    for t in ["vote for your favourite skin", "jesus that was close", "chicken breasts", "he is naked snake"]:
        assert REAL.check_output(t, frozenset()).ok, t


def test_self_harm_shorthand_tagged():
    assert REAL.check_input(C("i wanna kms")).topics == {"self_harm"}
    assert REAL.check_input(C("donald trump")).topics == {"politics"}
