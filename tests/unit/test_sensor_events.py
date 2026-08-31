from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.companion.hub import SLEEP_HINT, CompanionHub
from app.config import Settings
from app.protocol.state import SessionState
from app.sessions.session import DeviceSession, Outbound, SessionManager, _KEEPALIVE_GENERATION
from tests.fakes import FakeBrain


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


def test_sleep_event_marks_departing() -> None:
    session = _session()
    session._on_sensor_event({"event": "sleep"})
    assert "sleep" in session._sensor_event_last
    assert session.departing == "sleeping"


def test_bright_dark_events_are_dropped() -> None:
    session = _session()
    session._on_sensor_event({"event": "bright"})
    session._on_sensor_event({"event": "dark"})
    assert session._sensor_event_last == {}


@pytest.mark.asyncio
async def test_sleep_presence_survives_session_close() -> None:
    session = _session()
    captured: list[dict] = []

    class _Sessions:
        def get(self, _device_id):
            return None if session._closed else session

    hub = CompanionHub(_Sessions())
    session.ws.app.state.companion = hub

    async def capture(_device_id, payload):
        captured.append(payload)

    hub.broadcast = capture  # type: ignore[method-assign]
    session._on_sensor_event({"event": "sleep"})
    assert hub.is_asleep(session.device_id)
    session._closed = True
    await hub.push_presence(session.device_id)
    assert captured[-1]["online"] is False
    assert captured[-1]["sleeping"] is True
    assert SLEEP_HINT in captured[-1]["hint"]


@pytest.mark.asyncio
async def test_attach_clears_sleep() -> None:
    session = _session()
    settings = Settings(database_url="memory://", environment="test", vad_backend="energy")
    manager = SessionManager(settings)
    hub = CompanionHub(manager)
    session.ws.app.state.companion = hub
    hub.mark_asleep(session.device_id)
    await manager.attach(session)
    assert not hub.is_asleep(session.device_id)


@pytest.mark.asyncio
async def test_pet_injects_internal_event_when_idle() -> None:
    from app.ai.gemini import PET_INTERNAL_EVENT

    session = _session()
    session.ws.app.state.companion = None
    session.state.state = SessionState.READY
    brain = FakeBrain()
    session.brain = brain
    session._consume_speakable = AsyncMock()  # type: ignore[method-assign]
    session._speak = AsyncMock()  # type: ignore[method-assign]
    await session._react_to_pet()
    assert brain.pet_events == [PET_INTERNAL_EVENT]
    assert PET_INTERNAL_EVENT in brain.text_turns
    session._speak.assert_awaited()


@pytest.mark.asyncio
async def test_pet_skips_internal_event_while_speaking() -> None:
    session = _session()
    session.state.state = SessionState.SPEAKING
    brain = FakeBrain()
    session.brain = brain
    await session._react_to_pet()
    assert brain.pet_events == []
    assert brain.text_turns == []


def test_keepalive_not_dropped_on_generation_mismatch() -> None:
    session = _session()
    session.state.generation = 4
    stale = Outbound("bytes", b"opus", 3)
    ping = Outbound("json", '{"type":"ping"}', _KEEPALIVE_GENERATION)
    live = Outbound("bytes", b"opus", 4)
    assert session._should_send(stale) is False
    assert session._should_send(ping) is True
    assert session._should_send(live) is True


@pytest.mark.asyncio
async def test_keepalive_send_bypasses_full_outbound_queue() -> None:
    from app.protocol.messages import keepalive

    session = _session()
    sent: list[str] = []

    async def send_text(payload: str) -> None:
        sent.append(payload)

    session.ws.send_text = send_text
    session.out_queue = __import__("asyncio").Queue(maxsize=1)
    session.out_queue.put_nowait(Outbound("bytes", b"blocked", 1))
    ping = Outbound("json", keepalive(session.session_id, ts_ms=1), _KEEPALIVE_GENERATION)
    assert await session._send_outbound(ping) is True
    assert sent == [keepalive(session.session_id, ts_ms=1)]
    assert session.out_queue.full()
