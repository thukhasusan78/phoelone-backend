from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import edge_tts

from app.audio.text import sanitize_for_tts
from app.config import Settings
from app.observability.logging import get_logger
from app.observability.metrics import UPSTREAM_ERRORS

log = get_logger(__name__)


class TtsError(Exception):
    pass


class EdgeTtsClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(self, text: str) -> bytes:
        chunks = [chunk async for chunk in self.iter_mp3(text)]
        if not chunks:
            raise TtsError("edge-tts returned no audio")
        return b"".join(chunks)

    async def iter_mp3(self, text: str) -> AsyncIterator[bytes]:
        """Yield MP3 fragments as Edge TTS produces them (do not wait for the full file)."""
        text = sanitize_for_tts(text)
        if not text:
            raise TtsError("empty text after sanitization")
        log.info("tts.synthesize", tts_string=text, voice=self.settings.tts_voice)
        voices = [self.settings.tts_voice, self.settings.tts_fallback_voice]
        last_error: Exception | None = None
        for voice in voices:
            try:
                async for chunk in self._iter_voice(text, voice):
                    yield chunk
                return
            except TtsError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("tts.voice_failed", voice=voice, error=str(exc))
                UPSTREAM_ERRORS.labels(service="edge_tts").inc()
        raise TtsError(f"edge-tts failed: {last_error}")

    async def _iter_voice(self, text: str, voice: str) -> AsyncIterator[bytes]:
        communicate = edge_tts.Communicate(
            text,
            voice=voice,
            rate=self.settings.tts_rate,
            pitch=self.settings.tts_pitch,
            volume=self.settings.tts_volume,
        )
        stream = communicate.stream().__aiter__()
        yielded = False
        deadline = time.monotonic() + self.settings.tts_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TtsError("edge-tts timeout")
            try:
                message = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                raise TtsError("edge-tts timeout") from exc
            if message["type"] == "audio" and message.get("data"):
                yielded = True
                yield message["data"]
        if not yielded:
            raise TtsError("edge-tts returned no audio")
