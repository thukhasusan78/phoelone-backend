from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.companion.errors import CompanionError
from app.config import Settings
from app.protocol.state import SessionState
from app.sessions.session import DeviceSession, SessionManager


class RecordingMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.tool_by_name = {
            "self.otto.action": {},
            "self.otto.stop": {},
            "self.battery.get_level": {},
        }

    async def call(self, name: str, arguments: dict | None = None) -> str:
        args = arguments or {}
        self.calls.append((name, args))
        if name == "self.battery.get_level":
            return '{"level": 80, "charging": false}'
        return "ok"


def _session() -> DeviceSession:
    settings = Settings(
        environment="test",
        database_url="memory://",
        auth_pepper="pepper",
        gemini_api_keys="k",
    )
    ws = MagicMock()
    ws.app.state.companion = None
    session = DeviceSession(
        websocket=ws,
        settings=settings,
        device_id="aa:bb:cc:dd:ee:ff",
        client_id="cid",
        router=MagicMock(),
        tts_client=MagicMock(),
        brain_factory=lambda: None,
    )
    session.mcp = RecordingMcp()
    session.state.state = SessionState.READY
    return session


@pytest.mark.asyncio
async def test_companion_dance_and_stop() -> None:
    session = _session()
    await session.companion_action("dance", {"action": "jump"})
    assert session.mcp.calls[0][0] == "self.otto.action"
    assert session.mcp.calls[0][1]["action"] == "jump"
    await session.companion_action("stop", {})
    assert session.mcp.calls[-1][0] == "self.otto.stop"


@pytest.mark.asyncio
async def test_companion_rejects_hand_action() -> None:
    session = _session()
    with pytest.raises(CompanionError) as exc:
        await session.companion_action("dance", {"action": "hands_up"})
    assert exc.value.code == "invalid"
    assert session.mcp.calls == []


@pytest.mark.asyncio
async def test_companion_dance_busy_while_thinking() -> None:
    session = _session()
    session.state.state = SessionState.THINKING
    with pytest.raises(CompanionError) as exc:
        await session.companion_action("dance", {"action": "jump"})
    assert exc.value.code == "busy"


@pytest.mark.asyncio
async def test_refresh_status_parses_battery() -> None:
    session = _session()
    await session.refresh_status()
    assert session._battery == 80
    assert session._charging is False
    snap = session.presence_snapshot()
    assert snap["online"] is True
    assert snap["battery"] == 80


def test_session_manager_get() -> None:
    settings = Settings(environment="test", database_url="memory://", auth_pepper="p")
    manager = SessionManager(settings)
    session = _session()
    manager._by_device[session.device_id] = session
    assert manager.get("AA:BB:CC:DD:EE:FF") is session
    assert manager.online(session.device_id) is True
    session._closed = True
    assert manager.online(session.device_id) is False
