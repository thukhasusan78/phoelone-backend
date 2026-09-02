from __future__ import annotations

import time
from typing import Any

import orjson

from app.protocol.models import canonical_emotion


def dumps(payload: dict[str, Any]) -> str:
    return orjson.dumps(payload).decode("utf-8")


def server_hello(session_id: str) -> str:
    return dumps(
        {
            "type": "hello",
            "transport": "websocket",
            "session_id": session_id,
            "audio_params": {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
    )


def tts(session_id: str, state: str, text: str | None = None) -> str:
    body: dict[str, Any] = {"session_id": session_id, "type": "tts", "state": state}
    if text is not None:
        body["text"] = text
    return dumps(body)


def abort_speaking(session_id: str, reason: str | None = None) -> str:
    """Server → device: leave auto-listen and return to Idle (mic off, idle face)."""
    body: dict[str, Any] = {"session_id": session_id, "type": "abort"}
    if reason:
        body["reason"] = reason
    return dumps(body)


def stt(session_id: str, text: str) -> str:
    return dumps({"session_id": session_id, "type": "stt", "text": text})


def llm_emotion(session_id: str, emotion: str, text: str | None = None) -> str:
    emotion = canonical_emotion(emotion)
    body: dict[str, Any] = {"session_id": session_id, "type": "llm", "emotion": emotion}
    if text:
        body["text"] = text
    return dumps(body)


def mcp(session_id: str, payload: dict[str, Any]) -> str:
    return dumps({"session_id": session_id, "type": "mcp", "payload": payload})


def keepalive(session_id: str, ts_ms: int | None = None) -> str:
    return dumps(
        {
            "session_id": session_id,
            "type": "ping",
            "ts_ms": int(time.time() * 1000) if ts_ms is None else ts_ms,
        }
    )


def alert(session_id: str, status: str, message: str, emotion: str) -> str:
    return dumps(
        {
            "session_id": session_id,
            "type": "alert",
            "status": status,
            "message": message,
            "emotion": emotion,
        }
    )


def system(session_id: str, command: str) -> str:
    return dumps({"session_id": session_id, "type": "system", "command": command})
