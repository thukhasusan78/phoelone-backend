from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.config import Settings
from app.sessions.session import DeviceSession


def _session() -> DeviceSession:
    settings = Settings(database_url="memory://", environment="test", vad_backend="energy")
    session = DeviceSession(
        websocket=MagicMock(),
        settings=settings,
        device_id="aa:bb:cc:dd:ee:ff",
        client_id="cid",
        router=MagicMock(),
        tts_client=MagicMock(),
        brain_factory=lambda: None,
    )
    return session


def test_sensor_notification_reaches_handler() -> None:
    session = _session()
    session.mcp.on_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/phoe_lone.event",
            "params": {"event": "pet", "ts_ms": 1},
        }
    )
    assert "pet" in session._sensor_event_last


def test_unknown_notification_is_ignored() -> None:
    session = _session()
    session.mcp.on_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/something.else",
            "params": {"event": "pet"},
        }
    )
    assert session._sensor_event_last == {}
    assert session._motion_inhibited_until == 0.0


def test_unknown_sensor_event_is_dropped() -> None:
    session = _session()
    session._on_sensor_event({"event": "explode"})
    assert session._sensor_event_last == {}


def test_fall_sets_motion_inhibit() -> None:
    session = _session()
    before = time.monotonic()
    session._on_sensor_event({"event": "fall"})
    assert session._motion_inhibited_until > before


def test_sensor_rate_limit_same_event() -> None:
    session = _session()
    session._on_sensor_event({"event": "pet"})
    first = session._sensor_event_last["pet"]
    session._on_sensor_event({"event": "pet"})
    assert session._sensor_event_last["pet"] == first
