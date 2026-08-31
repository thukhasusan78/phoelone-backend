from __future__ import annotations

import array
import asyncio
import struct

from app.ai.gemini import FunctionCall, TurnResult
from app.audio.text import chunk_burmese, completed_burmese_sentences, sanitize_for_tts


class FakeBrain:
    def __init__(
        self,
        *,
        input_text: str = "ရှေ့ကို လှမ်းပါ",
        output_text: str = "ကဲ လမ်းလျှောက်လိုက်မယ်နော်။",
        calls: list[FunctionCall] | None = None,
        error: str | None = None,
        transient_disconnect: bool = False,
        begin_delay_s: float = 0.0,
    ) -> None:
        self.input_text = input_text
        self.output_text = output_text
        self.calls = calls or []
        self.error = error
        self.transient_disconnect = transient_disconnect
        self.begin_delay_s = begin_delay_s
        self.pcm_bytes = 0
        self.begun = False
        self.ended = False
        self.cancelled = False
        self.configured: list[dict] = []
        self.function_results: list[dict] = []
        self._speak_q: asyncio.Queue[str | None] = asyncio.Queue()
        self._published_tts: list[str] = []
        self._speak_closed = False
        self._last_output = ""
        self.music_finished: dict | None = None
        self.pet_events: list[str] = []
        self.text_turns: list[str] = []
        self.owner_prefix = ""

    async def configure_tools(self, declarations: list[dict]) -> None:
        self.configured = declarations

    def set_owner_context(self, prefix: str) -> None:
        self.owner_prefix = (prefix or "").strip()

    async def ensure_connected(self) -> None:
        self.cancelled = False

    async def send_text_turn(self, user_text: str) -> TurnResult:
        cleaned = " ".join((user_text or "").split()).strip()
        self.text_turns.append(cleaned)
        self._speak_q = asyncio.Queue()
        self._published_tts = []
        self._speak_closed = False
        result = TurnResult(
            input_text=cleaned,
            output_text="" if self.calls else self.output_text,
            function_calls=list(self.calls),
            error=self.error,
            transient_disconnect=self.transient_disconnect,
        )
        self._last_output = result.output_text
        self._flush_speakable(final=True)
        return result

    async def begin_utterance(self) -> None:
        if self.begin_delay_s > 0:
            await asyncio.sleep(self.begin_delay_s)
        self.begun = True
        self.pcm_bytes = 0
        self.cancelled = False
        self.ended = False
        self._speak_q = asyncio.Queue()
        self._published_tts = []
        self._speak_closed = False
        self._last_output = ""

    async def push_pcm(self, pcm: bytes) -> None:
        self.pcm_bytes += len(pcm)

    async def end_utterance(self) -> TurnResult:
        self.ended = True
        if self.cancelled:
            return TurnResult(error="cancelled")
        if self.transient_disconnect:
            return TurnResult(
                input_text=self.input_text,
                transient_disconnect=True,
            )
        result = TurnResult(
            input_text=self.input_text if self.pcm_bytes else "",
            output_text="" if self.calls else self.output_text,
            function_calls=list(self.calls),
            error=self.error,
        )
        self._last_output = result.output_text
        self._flush_speakable(final=False)
        return result

    async def continue_with_functions(self, results: list[dict]) -> TurnResult:
        self.function_results = results
        if self.cancelled:
            return TurnResult(error="cancelled", input_text=self.input_text)
        result = TurnResult(
            input_text=self.input_text,
            output_text=self.output_text,
            function_calls=[],
            error=self.error,
        )
        self._last_output = result.output_text
        self._flush_speakable(final=False)
        return result

    def _flush_speakable(self, *, final: bool) -> None:
        if self._speak_closed:
            return
        cleaned = sanitize_for_tts(self._last_output)
        chunks = chunk_burmese(cleaned) if cleaned else []
        if not final:
            chunks = completed_burmese_sentences(cleaned)
        for sentence in chunks:
            if sentence in self._published_tts:
                continue
            self._published_tts.append(sentence)
            self._speak_q.put_nowait(sentence)

    async def iter_speakable(self):
        while True:
            item = await self._speak_q.get()
            if item is None:
                return
            yield item

    async def enqueue_speakable(self, text: str) -> None:
        cleaned = sanitize_for_tts(text)
        if not cleaned or self._speak_closed:
            return
        if cleaned not in self._published_tts:
            self._published_tts.append(cleaned)
            await self._speak_q.put(cleaned)

    async def notify_music_finished(
        self,
        *,
        track: str,
        artist: str = "",
        status: str = "completed",
    ) -> TurnResult:
        self.music_finished = {"track": track, "artist": artist, "status": status}
        wrap = "သီချင်း ပြီးသွားပါပြီနော်။" if status == "completed" else ""
        self._speak_q = asyncio.Queue()
        self._published_tts = []
        self._speak_closed = False
        self._last_output = wrap
        self._flush_speakable(final=True)
        if not self._speak_closed:
            self._speak_closed = True
            await self._speak_q.put(None)
        return TurnResult(output_text=wrap)

    async def notify_pet(self) -> TurnResult:
        from app.ai.gemini import PET_INTERNAL_EVENT

        self.pet_events.append(PET_INTERNAL_EVENT)
        return await self.send_text_turn(PET_INTERNAL_EVENT)

    async def finish_speakable(self) -> None:
        self._flush_speakable(final=True)
        if not self._speak_closed:
            self._speak_closed = True
            await self._speak_q.put(None)

    async def cancel(self) -> None:
        self.cancelled = True
        if not self._speak_closed:
            self._speak_closed = True
            await self._speak_q.put(None)

    async def close(self) -> None:
        self.cancelled = True


def _tone_pcm(samples: int = 960, amplitude: int = 4000, freq_bins: int = 17) -> bytes:
    """Synthetic int16 mono PCM with enough energy to open the energy-backend test VAD."""
    out = array.array("h")
    for i in range(samples):
        # Square-ish wave so RMS stays high and deterministic without needing math.sin.
        value = amplitude if (i // max(1, freq_bins)) % 2 == 0 else -amplitude
        out.append(value)
    return out.tobytes()


def _silence_pcm(samples: int = 960) -> bytes:
    return b"\x00\x00" * samples


def _static_pcm(samples: int = 960, amplitude: int = 1200) -> bytes:
    """Steady mid-level energy similar to captured ESP32 static (median RMS ~1163)."""
    samples_list = [amplitude if i % 2 == 0 else -amplitude for i in range(samples)]
    return struct.pack("<" + "h" * samples, *samples_list)


class SilentCodec:
    """Decodes any uplink packet into high-energy PCM for the energy-backend test VAD."""

    def __init__(self, pcm: bytes | None = None) -> None:
        self._pcm = pcm if pcm is not None else _tone_pcm()

    def decode_uplink(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        return self._pcm

    def encode_downlink(self, pcm: bytes) -> bytes:
        return b"\x00\x01"

    def reset(self) -> None:
        return None


class QuietCodec:
    """Decodes uplink into near-silence that energy VAD should drop."""

    def decode_uplink(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        return _static_pcm(amplitude=80)

    def encode_downlink(self, pcm: bytes) -> bytes:
        return b"\x00\x01"

    def reset(self) -> None:
        return None


class SpeechThenQuietCodec:
    """Loud speech frames, then near-silence so hangover can expire."""

    def __init__(self, speech_frames: int = 2) -> None:
        self.speech_frames = speech_frames
        self._n = 0

    def decode_uplink(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        self._n += 1
        if self._n <= self.speech_frames:
            return _tone_pcm()
        return _static_pcm(amplitude=80)

    def encode_downlink(self, pcm: bytes) -> bytes:
        return b"\x00\x01"

    def reset(self) -> None:
        self._n = 0
