"""Streaming brain: sends the prompt, speaks sentences as they complete."""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.brain.prompt import build_messages
from app.brain.stream_parse import SayExtractor, SentenceSplitter, parse_reply_json
from app.config import LLMSettings
from app.metrics import Usage


@dataclass
class BrainResult:
    say: str = ""
    emotion: str = "neutral"
    action: str | None = None
    usage: Usage = field(default_factory=Usage)
    first_token_at: float | None = None
    error: str | None = None


class Brain:
    def __init__(self, client, settings: LLMSettings, persona: str, clock=time.perf_counter):
        self._client = client
        self._s = settings
        self._persona = persona
        self._clock = clock
        self.history: list[tuple[str, str]] = []

    def remember(self, event_text: str, spoken: str, emotion: str, action: str | None) -> None:
        reply = json.dumps({"say": spoken, "emotion": emotion, "action": action}, ensure_ascii=False)
        self.history.append((event_text, reply))
        del self.history[:-self._s.history_turns]

    async def reply(self, event_text: str, on_sentence: Callable[[str], Awaitable[bool]],
                    max_tokens: int | None = None) -> BrainResult:
        res = BrainResult()
        kwargs = dict(
            model=self._s.model,
            messages=build_messages(self._persona, self.history, event_text),
            stream=True,
            stream_options={"include_usage": True},
            max_tokens=max_tokens or self._s.max_tokens,
            extra_body=self._s.extra_body,
        )
        if self._s.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        stream = None
        try:
            try:
                async with asyncio.timeout(self._s.first_token_timeout_s):
                    stream = await self._client.chat.completions.create(**kwargs)
                    it = stream.__aiter__()
                    first = await self._first_content(it, res)
            except TimeoutError:
                res.error = "timeout"
                return res
            await self._consume(it, first, res, on_sentence)
        except Exception as e:  # noqa: BLE001 - nothing may escape reply()
            res.error = f"api: {type(e).__name__}: {e}"
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass
        return res

    @staticmethod
    def _content(chunk) -> str | None:
        if not chunk.choices:
            return None
        return getattr(chunk.choices[0].delta, "content", None) or None

    async def _first_content(self, it, res: BrainResult) -> str | None:
        """Advance until the first content delta (recording usage chunks on the way)."""
        async for chunk in it:
            if chunk.usage is not None:
                res.usage = Usage.from_api(chunk.usage)
            piece = self._content(chunk)
            if piece:
                res.first_token_at = self._clock()
                return piece
        return None

    async def _consume(self, it, first: str | None, res: BrainResult,
                       on_sentence: Callable[[str], Awaitable[bool]]) -> None:
        extractor, splitter = SayExtractor(), SentenceSplitter()
        full: list[str] = []
        streamed: list[str] = []

        async def push(piece: str) -> bool:
            full.append(piece)
            text = extractor.feed(piece)
            streamed.append(text)
            for sentence in splitter.feed(text):
                if not await on_sentence(sentence):
                    return False
            return True

        keep_going = first is None or await push(first)
        if keep_going:
            async for chunk in it:
                if chunk.usage is not None:
                    res.usage = Usage.from_api(chunk.usage)
                piece = self._content(chunk)
                if piece and not await push(piece):
                    keep_going = False
                    break
        if keep_going:
            rest = splitter.flush()
            if rest:
                await on_sentence(rest)
        say, res.emotion, res.action = parse_reply_json("".join(full))
        res.say = say if say is not None else "".join(streamed).strip()
