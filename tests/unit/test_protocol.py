from __future__ import annotations

from app.protocol.messages import abort_speaking, keepalive, llm_emotion, server_hello, tts
from app.protocol.models import DeviceHello, ListenMessage, PongMessage


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


def test_server_abort_speaking() -> None:
    import json

    body = json.loads(abort_speaking("abc", "conversation_ended"))
    assert body == {
        "session_id": "abc",
        "type": "abort",
        "reason": "conversation_ended",
    }
    bare = json.loads(abort_speaking("abc"))
    assert bare == {"session_id": "abc", "type": "abort"}


def test_tts_and_emotion() -> None:
    assert '"state":"start"' in tts("s", "start")
    assert '"emotion":"neutral"' in llm_emotion("s", "unknown")
    assert '"emotion":"happy"' in llm_emotion("s", "happy")


def test_pong_parse() -> None:
    msg = PongMessage.model_validate(
        {"session_id": "abc", "type": "pong", "ts_ms": 1710000000000, "extra": True}
    )
    assert msg.type == "pong"
    assert msg.ts_ms == 1710000000000
    assert msg.session_id == "abc"


def test_keepalive_includes_ts_ms() -> None:
    import json
    import time

    body = json.loads(keepalive("abc", ts_ms=1710000000000))
    assert body == {"session_id": "abc", "type": "ping", "ts_ms": 1710000000000}
    live = json.loads(keepalive("abc"))
    assert live["type"] == "ping"
    assert isinstance(live["ts_ms"], int)
    assert abs(live["ts_ms"] - int(time.time() * 1000)) < 5_000
