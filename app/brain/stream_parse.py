"""Pure helpers for the streamed LLM reply: pull the `say` text out of partial
JSON, split it into sentences for TTS, and parse the final JSON."""
import json
import re

EMOTIONS = {"smug", "happy", "shocked", "annoyed", "sulking", "confused", "neutral"}
ACTIONS = {"celebrate", "facepalm"}

_SAY_OPEN = re.compile(r'"say"\s*:\s*"')
_SIMPLE_ESCAPES = {"n": " ", "t": " ", "r": " ", "b": " ", "f": " ",
                   '"': '"', "\\": "\\", "/": "/"}
_HEX = set("0123456789abcdefABCDEF")
_LOW_ESCAPE = re.compile(r"\\u[dD][c-fC-F][0-9a-fA-F]{2}")
_REPLACEMENT = "\N{REPLACEMENT CHARACTER}"


def _could_be_u_escape(s: str) -> bool:
    """True if s (shorter than 6 chars) is a prefix of some backslash-u-XXXX escape."""
    return all(
        (c == "\\" if k == 0 else c == "u" if k == 1 else c in _HEX)
        for k, c in enumerate(s)
    )


class SayExtractor:
    """Incrementally decodes the string value of the `say` key."""

    def __init__(self) -> None:
        self.done = False
        self._in_string = False
        self._buf = ""  # unconsumed input (key search, or an incomplete escape)

    def feed(self, chunk: str) -> str:
        if self.done:
            return ""
        self._buf += chunk
        if not self._in_string:
            m = _SAY_OPEN.search(self._buf)
            if not m:
                return ""
            self._in_string = True
            self._buf = self._buf[m.end():]
        return self._decode()

    def _decode(self) -> str:
        out: list[str] = []
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch == '"':
                self.done = True
                i = len(buf)
                break
            if ch != "\\":
                out.append(ch)
                i += 1
                continue
            if i + 1 >= len(buf):
                break  # lone backslash at end of chunk; resume next feed
            esc = buf[i + 1]
            if esc == "u":
                hex4 = buf[i + 2:i + 6]
                if len(hex4) < 4:
                    break  # partial \uXXXX; resume next feed
                if all(c in _HEX for c in hex4):
                    cp = int(hex4, 16)
                    if 0xD800 <= cp <= 0xDBFF:  # high surrogate: needs a low one
                        nxt = buf[i + 6:i + 12]
                        if len(nxt) < 6 and _could_be_u_escape(nxt):
                            break  # pair may be split across chunks; resume next feed
                        if _LOW_ESCAPE.fullmatch(nxt):
                            low = int(nxt[2:], 16)
                            out.append(chr(0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)))
                            i += 12
                        else:
                            out.append(_REPLACEMENT)
                            i += 6
                        continue
                    out.append(_REPLACEMENT if 0xDC00 <= cp <= 0xDFFF else chr(cp))
                    i += 6
                else:
                    i += 2  # malformed escape: drop the "\u", keep going
                continue
            out.append(_SIMPLE_ESCAPES.get(esc, esc))
            i += 2
        self._buf = "" if self.done else buf[i:]
        return "".join(out)


_BOUNDARY = re.compile(r'[.!?…]+["\'”’)\]]*(?=\s)')
_ABBREVS = {"mr.", "mrs.", "dr.", "vs.", "e.g.", "i.e."}
_INITIAL = re.compile(r'^\W*[A-Z]\.$')


class SentenceSplitter:
    """Emits complete sentences; holds back a boundary until whitespace follows."""

    def __init__(self) -> None:
        self._buf = ""
        self._carry = ""  # too-short fragment waiting to merge into the next sentence

    def feed(self, text: str) -> list[str]:
        self._buf += text
        sentences: list[str] = []
        start = 0
        for m in _BOUNDARY.finditer(self._buf):
            if self._is_abbrev(self._buf[start:m.end()], m.group()):
                continue
            sentence = self._carry + self._buf[start:m.end()].strip()
            start = m.end()
            if len(sentence) < 2:
                self._carry = sentence + " "
                continue
            self._carry = ""
            sentences.append(sentence)
        self._buf = self._buf[start:]
        return sentences

    @staticmethod
    def _is_abbrev(segment: str, terminator: str) -> bool:
        if terminator != ".":
            return False
        tokens = segment.split()
        if not tokens:
            return False
        token = tokens[-1]
        return token.lower() in _ABBREVS or bool(_INITIAL.match(token))

    def flush(self) -> str | None:
        rest = (self._carry + self._buf).strip()
        self._buf = self._carry = ""
        return rest or None


def parse_reply_json(full_text: str) -> tuple[str | None, str, str | None]:
    """Return (say, emotion, action); say is None if the text is unparseable."""
    text = full_text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, "neutral", None
    if not isinstance(data, dict):
        return None, "neutral", None
    say = data.get("say")
    say = say.strip() if isinstance(say, str) and say.strip() else None
    emotion = data.get("emotion")
    if not isinstance(emotion, str) or emotion not in EMOTIONS:
        emotion = "neutral"
    action = data.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        action = None
    return say, emotion, action
