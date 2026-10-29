from typing import Protocol


class TTS(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Return MP3 bytes for the text."""
        ...
