from __future__ import annotations

from app.audio.edge_tts import EdgeTtsClient, TtsError
from app.audio.opus import (
    DOWNLINK_FRAME_SAMPLES,
    DOWNLINK_RATE,
    FRAME_MS,
    UPLINK_FRAME_SAMPLES,
    UPLINK_RATE,
    CodecError,
    LibOpusCodec,
    create_codec,
    iter_pcm_frames,
    media_to_pcm24k,
    mp3_to_pcm24k,
)
from app.audio.pacer import pace_opus_frames
from app.audio.speech_gate import SpeechGate, create_speech_gate, pcm16le_rms
from app.audio.text import FALLBACK_BURMESE, cap_text, chunk_burmese, sanitize_for_tts

__all__ = [
    "CodecError",
    "DOWNLINK_FRAME_SAMPLES",
    "DOWNLINK_RATE",
    "EdgeTtsClient",
    "FALLBACK_BURMESE",
    "FRAME_MS",
    "LibOpusCodec",
    "SpeechGate",
    "TtsError",
    "UPLINK_FRAME_SAMPLES",
    "UPLINK_RATE",
    "cap_text",
    "chunk_burmese",
    "create_speech_gate",
    "pace_opus_frames",
    "pcm16le_rms",
    "sanitize_for_tts",
    "create_codec",
    "iter_pcm_frames",
    "media_to_pcm24k",
    "mp3_to_pcm24k",
]
