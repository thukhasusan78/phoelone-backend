from __future__ import annotations

import pytest

from app.protocol.messages import llm_emotion, server_hello, tts
from app.protocol.models import DeviceHello, ListenMessage


def test_device_hello_parse() -> None:
    raw = {
        "type": "hello",
        "version": 1,
        "features": {"mcp": True},
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
    hello = DeviceHello.model_validate(raw)
    assert hello.features.mcp is True


def test_listen_parse() -> None:
    msg = ListenMessage.model_validate(
        {"session_id": "x", "type": "listen", "state": "start", "mode": "auto"}
    )
    assert msg.mode == "auto"


def test_server_hello_transport() -> None:
    body = server_hello("abc")
    assert '"transport":"websocket"' in body
    assert '"sample_rate":24000' in body


def test_tts_and_emotion() -> None:
    assert '"state":"start"' in tts("s", "start")
    assert '"emotion":"neutral"' in llm_emotion("s", "unknown")
    assert '"emotion":"happy"' in llm_emotion("s", "happy")
