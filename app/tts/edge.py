import edge_tts

from app.config import TTSSettings


class EdgeTTS:
    def __init__(self, settings: TTSSettings) -> None:
        self._settings = settings

    async def synthesize(self, text: str) -> bytes:
        comm = edge_tts.Communicate(
            text, self._settings.voice, rate=self._settings.rate, pitch=self._settings.pitch
        )
        chunks = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                chunks += chunk["data"]
        return bytes(chunks)
