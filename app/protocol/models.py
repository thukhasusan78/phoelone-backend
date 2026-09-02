from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtraModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AudioParams(ExtraModel):
    format: str = "opus"
    sample_rate: int = 16000
    channels: int = 1
    frame_duration: int = 60


class HelloFeatures(ExtraModel):
    mcp: bool = False
    aec: bool = False
    glyph_push: bool = False


class DeviceHello(ExtraModel):
    type: Literal["hello"]
    version: int = 1
    features: HelloFeatures = Field(default_factory=HelloFeatures)
    transport: str
    audio_params: AudioParams = Field(default_factory=AudioParams)


class ListenMessage(ExtraModel):
    type: Literal["listen"]
    session_id: str | None = None
    state: Literal["start", "stop", "detect"]
    mode: Literal["auto", "manual", "realtime"] | None = None
    text: str | None = None


class AbortMessage(ExtraModel):
    type: Literal["abort"]
    session_id: str | None = None
    reason: str | None = None


class PongMessage(ExtraModel):
    type: Literal["pong"]
    session_id: str | None = None
    ts_ms: int | None = None


class McpEnvelope(ExtraModel):
    type: Literal["mcp"]
    session_id: str | None = None
    payload: dict[str, Any]


KNOWN_EMOTIONS = frozenset(
    {
        "staticstate",
        "robot_2",
        "neutral",
        "happy",
        "sad",
        "sleepy",
        "thinking",
        "confused",
        "loving",
        "angry",
        "anger",
        "scare",
        "buxue",
        "laughing",
        "funny",
        "crying",
        "embarrassed",
        "surprised",
        "shocked",
        "winking",
        "cool",
        "relaxed",
        "delicious",
        "kissy",
        "confident",
        "silly",
        "listening",
        "speaking",
    }
)

# Slice 6 firmware table keys. GIF aliases stay in KNOWN_EMOTIONS so Gemini
# may still name them; the wire uses the canonical face.
EMOTION_ALIASES = {
    "shocked": "surprised",
    "crying": "sad",
    "funny": "laughing",
    "anger": "angry",
}


def canonical_emotion(emotion: str) -> str:
    name = (emotion or "").strip()
    mapped = EMOTION_ALIASES.get(name, name)
    return mapped if mapped in KNOWN_EMOTIONS else "neutral"
