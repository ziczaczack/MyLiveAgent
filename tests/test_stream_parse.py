from app.brain.stream_parse import SayExtractor, SentenceSplitter, parse_reply_json


def feed_all(chunks):
    x = SayExtractor(); return "".join(x.feed(c) for c in chunks), x.done

def test_basic():
    assert feed_all(['{"say": "Hi there.", "emotion": "smug", "action": null}']) == ("Hi there.", True)
def test_escape_split_across_chunks():
    out, done = feed_all(['{"say": "He said \\', '"lah\\', '" ok \\u20', '19s fine\\nyes"}'])
    assert out == 'He said "lah" ok ’s fine yes' and done
def test_say_inside_code_fence():
    assert feed_all(['```json\n{"sa', 'y":"Walao."}\n```'])[0] == "Walao."
def test_key_split_and_spaces():
    assert feed_all(['{"emotion":"happy", "s', 'ay"  :  "', 'A', 'B"}'])[0] == "AB"
def test_other_escapes_and_nothing_after_done():
    x = SayExtractor()
    assert x.feed('{"say":"a\\tb\\\\c\\/d\\re') == "a b\\c/d e"
    assert x.feed('"} trailing "say":"no"') == ""
    assert x.done
def test_no_say_key():
    assert feed_all(['{"emotion":"happy"}']) == ("", False)

def test_splitter_streams_sentences():
    s = SentenceSplitter()
    assert s.feed("That was a test. You all") == ["That was a test."]
    assert s.feed(" passed! Congrats") == ["You all passed!"]
    assert s.flush() == "Congrats"
def test_splitter_waits_for_whitespace_and_handles_ellipsis_and_lah():
    s = SentenceSplitter()
    assert s.feed("Aiyo... ") == ["Aiyo..."]
    assert s.feed("Can or not lah?") == []      # no whitespace after '?' yet
    assert s.flush() == "Can or not lah?"
def test_splitter_abbrev():
    s = SentenceSplitter(); assert s.feed("Ask Dr. Lok about it. ") == ["Ask Dr. Lok about it."]
def test_splitter_initial_and_runs_and_quotes():
    s = SentenceSplitter()
    assert s.feed("J. Lo is here?! Yes. He said \"go.\" Ok ") == ["J. Lo is here?!", "Yes.", 'He said "go."']
def test_splitter_merges_short_fragment_and_flush_empty():
    s = SentenceSplitter()
    assert s.feed("! Then more. ") == ["! Then more."]
    assert s.flush() is None

def test_parse_reply_json():
    assert parse_reply_json('{"say":"x","emotion":"furious","action":"dance"}') == ("x", "neutral", None)
    assert parse_reply_json('```json\n{"say":"x","emotion":"smug","action":"facepalm"}\n```') == ("x", "smug", "facepalm")
    assert parse_reply_json("not json")[0] is None

def test_parse_reply_json_unhashable_fields():
    assert parse_reply_json('{"say":"x","emotion":["happy"],"action":{}}') == ("x", "neutral", None)
    assert parse_reply_json('{"say":"x","emotion":{"a":1},"action":["celebrate"]}') == ("x", "neutral", None)

BS = chr(92)
HI, LO = BS + "ud83d", BS + "ude00"
FFFD = "\N{REPLACEMENT CHARACTER}"
def say_json(body): return '{"say":"' + body + '"}'

def test_surrogate_pair_one_chunk():
    out, done = feed_all([say_json("hi " + HI + LO + "!")])
    assert out == "hi \U0001F600!" and done
    out.encode("utf-8")

def test_surrogate_pair_split_across_chunks():
    text = say_json("a" + HI + LO + "b")
    i = text.index(HI)
    for cut in range(1, 12):
        out, done = feed_all([text[:i + cut], text[i + cut:]])
        assert out == "a\U0001F600b" and done, cut

def test_lone_surrogates_become_replacement_char():
    assert feed_all([say_json("a" + HI + "z")])[0] == "a" + FFFD + "z"
    assert feed_all([say_json("a" + HI)])[0] == "a" + FFFD
    assert feed_all([say_json("a" + LO + "z")])[0] == "a" + FFFD + "z"
    assert feed_all([say_json("a" + HI + BS + "nz")])[0] == "a" + FFFD + " z"
