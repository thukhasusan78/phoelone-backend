from __future__ import annotations

from typing import Any

import orjson

from app.protocol.models import KNOWN_EMOTIONS


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


def stt(session_id: str, text: str) -> str:
    return dumps({"session_id": session_id, "type": "stt", "text": text})


def llm_emotion(session_id: str, emotion: str, text: str | None = None) -> str:
    if emotion not in KNOWN_EMOTIONS:
        emotion = "neutral"
    body: dict[str, Any] = {"session_id": session_id, "type": "llm", "emotion": emotion}
    if text:
        body["text"] = text
    return dumps(body)


def mcp(session_id: str, payload: dict[str, Any]) -> str:
    return dumps({"session_id": session_id, "type": "mcp", "payload": payload})


def keepalive(session_id: str) -> str:
    return dumps({"session_id": session_id, "type": "ping"})


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
