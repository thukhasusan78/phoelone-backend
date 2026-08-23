from __future__ import annotations

from app.audio.edge_tts import TtsError


class FakeTts:
    async def synthesize(self, text: str) -> bytes:
        if not text:
            raise TtsError("empty")
        return b"ID3fake-mp3"
