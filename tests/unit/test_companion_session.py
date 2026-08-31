from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.companion.errors import CompanionError
from app.companion.reactions import rps_countdown_line, rps_timeout_line
from app.config import Settings
from app.protocol.state import SessionState
from app.sessions.session import DeviceSession, SessionManager
from tests.fakes import FakeBrain


class RecordingMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.tool_by_name = {
            "self.otto.action": {},
            "self.otto.stop": {},
            "self.battery.get_level": {},
            "self.mickey.alarm.get": {},
            "self.mickey.alarm.set": {},
            "self.mickey.alarm.cancel": {},
            "self.mickey.sleep.now": {},
            "self.get_device_status": {},
            "self.audio_speaker.set_volume": {},
            "self.screen.set_brightness": {},
            "self.screen.set_theme": {},
            "self.otto.get_trims": {},
            "self.otto.set_trim": {},
            "self.reboot": {},
            "self.upgrade_firmware": {},
            "self.get_system_info": {},
        }

    async def call(self, name: str, arguments: dict | None = None) -> str:
        args = arguments or {}
        self.calls.append((name, args))
        if name == "self.battery.get_level":
            return '{"level": 80, "charging": false}'
        if name == "self.mickey.alarm.get":
            return '{"enabled": true, "hour": 7, "minute": 0, "repeat": true}'
        if name == "self.get_device_status":
            return (
                '{"audio_speaker": {"volume": 70}, '
                '"screen": {"brightness": 80, "theme": "dark"}}'
            )
        if name == "self.otto.get_trims":
            return '{"left_leg": 1, "right_leg": 0, "left_foot": -2, "right_foot": 0}'
        if name == "self.get_system_info":
            return '{"application": {"version": "2.4.2"}}'
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
async def test_companion_rps_speaks_countdown_before_reveal() -> None:
    session = _session()
    spoken: list[str] = []
    events: list[str] = []

    async def _capture_speak(text, _generation, emotion="happy"):
        spoken.append(str(text))
        events.append("speak")

    async def on_reveal() -> None:
        events.append("reveal")

    session._speak = _capture_speak
    await session.companion_action(
        "rps_react",
        {"winner": "mickey", "on_reveal": on_reveal},
    )
    assert spoken[0] == rps_countdown_line()
    assert len(spoken) >= 2
    assert spoken[1] != rps_countdown_line()
    assert events[:2] == ["speak", "reveal"]
    actions = [args.get("action") for name, args in session.mcp.calls if name == "self.otto.action"]
    assert actions[0] == "swing"
    assert "jump" in actions
    assert actions[-1] == "home"
    assert session.state.state == SessionState.READY


@pytest.mark.asyncio
async def test_companion_rps_homes_after_sit_and_speaks_reaction() -> None:
    session = _session()
    spoken: list[str] = []

    async def _capture_speak(text, _generation, emotion="happy"):
        spoken.append(str(text))

    session._speak = _capture_speak
    await session.companion_action("rps_react", {"winner": "player"})
    actions = [args.get("action") for name, args in session.mcp.calls if name == "self.otto.action"]
    assert "sit" in actions
    assert actions[-1] == "home"
    assert spoken[0] == rps_countdown_line()
    assert any(line != rps_countdown_line() for line in spoken[1:])
    assert session.state.state == SessionState.READY


@pytest.mark.asyncio
async def test_companion_rps_timeout_speaks_and_homes() -> None:
    session = _session()
    spoken: list[str] = []

    async def _capture_speak(text, _generation, emotion="happy"):
        spoken.append(str(text))

    async def on_reveal() -> dict:
        return {"timeout": True, "phase": "awaiting_throw", "winner": None}

    session._speak = _capture_speak
    result = await session.companion_action("rps_react", {"on_reveal": on_reveal})
    assert result.get("timeout") is True
    assert spoken[0] == rps_countdown_line()
    assert spoken[-1] == rps_timeout_line()
    actions = [args.get("action") for name, args in session.mcp.calls if name == "self.otto.action"]
    assert actions[-1] == "home"


@pytest.mark.asyncio
async def test_companion_chat_speaks_reply() -> None:
    session = _session()
    brain = FakeBrain(output_text="ဟိုင်းနော်။")
    session.brain = brain

    async def _skip_stream(*_a, **_k):
        return None

    session._stream_sentence = _skip_stream
    result = await session.companion_action("chat", {"text": "မင်္ဂလာပါ"})
    assert brain.text_turns == ["မင်္ဂလာပါ"]
    assert brain.begun is False
    assert "ဟိုင်း" in result["text"] or "နော်" in result["text"]
    assert session.state.state == SessionState.READY


@pytest.mark.asyncio
async def test_companion_chat_busy_while_speaking() -> None:
    session = _session()
    session.brain = FakeBrain()
    session.state.state = SessionState.SPEAKING
    with pytest.raises(CompanionError) as exc:
        await session.companion_action("chat", {"text": "hi"})
    assert exc.value.code == "busy"
    assert session.brain.text_turns == []


@pytest.mark.asyncio
async def test_companion_applies_owner_memory_to_brain() -> None:
    session = _session()
    brain = FakeBrain()
    session.brain = brain
    from app.companion.life import empty_memory, patch_memory

    mem = empty_memory(session.device_id, session.client_id)
    patch_memory(mem, {"owner_name": "Thukha"})
    await session.apply_owner_memory(mem, reconnect=False)
    assert "Thukha" in brain.owner_prefix


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


@pytest.mark.asyncio
async def test_companion_alarm_set_then_get() -> None:
    session = _session()
    saved = await session.companion_action(
        "alarm_set",
        {"hour": 6, "minute": 30, "repeat": True},
    )
    set_call = (
        "self.mickey.alarm.set",
        {"hour": 6, "minute": 30, "repeat": True, "sleep_now": False},
    )
    assert set_call in [(name, args) for name, args in session.mcp.calls]
    got = await session.companion_action("alarm_get")
    assert got["set"] is True
    assert got["hour"] == 7
    assert saved["hour"] == 7
    cancelled = await session.companion_action("alarm_cancel")
    assert cancelled["set"] is False


@pytest.mark.asyncio
async def test_companion_settings_set_volume_and_refuse_dummy_upgrade() -> None:
    session = _session()
    state = await session.companion_action("settings_set", {"volume": 40})
    assert any(name == "self.audio_speaker.set_volume" for name, _ in session.mcp.calls)
    assert state["volume"] == 40
    snapshot = await session.companion_action("settings_get")
    assert snapshot["theme"] == "dark"
    assert snapshot["can_upgrade"] is False
    assert snapshot["trims"]["left_leg"] == 1
    assert snapshot["firmware_version"] == "2.4.2"
    with pytest.raises(CompanionError) as exc:
        await session.companion_action("upgrade")
    assert exc.value.code == "invalid"
    await session.companion_action("reboot")
    assert session.mcp.calls[-1][0] == "self.reboot"
    assert session.departing == "rebooting"


@pytest.mark.asyncio
async def test_companion_dance_does_not_wait_on_chat_lock() -> None:
    session = _session()
    session.state.state = SessionState.SPEAKING
    await session._companion_lock.acquire()
    try:
        result = await asyncio.wait_for(
            session.companion_action("dance", {"action": "jump"}),
            timeout=0.3,
        )
        assert result["ok"] is True
    finally:
        session._companion_lock.release()


@pytest.mark.asyncio
async def test_game_start_busy_while_thinking() -> None:
    from app.companion.hub import CompanionHub

    session = _session()
    session.state.state = SessionState.THINKING

    class _Sessions:
        def get(self, _device_id):
            return session

    hub = CompanionHub(_Sessions())
    with pytest.raises(CompanionError) as exc:
        await hub.handle("aa:bb:cc:dd:ee:ff", {"type": "game.start", "game": "rps"})
    assert exc.value.code == "busy"


@pytest.mark.asyncio
async def test_sleep_marks_sleeping_not_offline() -> None:
    session = _session()
    state = await session.companion_action("sleep", {})
    assert state["type"] == "alarm.state"
    assert session.departing == "sleeping"
    snap = session.presence_snapshot()
    assert snap["online"] is True
    assert snap["sleeping"] is True
    assert "sleeping" in (snap.get("hint") or "").lower()


@pytest.mark.asyncio
async def test_owner_memory_reconnects_when_ready_again() -> None:
    session = _session()
    brain = FakeBrain()
    session.brain = brain
    from app.companion.life import empty_memory, patch_memory

    mem = empty_memory(session.device_id, session.client_id)
    patch_memory(mem, {"owner_name": "Thukha"})
    session.state.state = SessionState.SPEAKING
    await session.apply_owner_memory(mem)
    assert "Thukha" in brain.owner_prefix
    assert session._owner_reconnect_pending is True
    session.state.state = SessionState.READY
    await session._maybe_reconnect_owner_memory()
    assert session._owner_reconnect_pending is False
    assert brain.cancelled is False


@pytest.mark.asyncio
async def test_care_pet_debounce() -> None:
    from app.companion.hub import CompanionHub
    from app.db.companion_store import InMemoryCompanionStore

    store = InMemoryCompanionStore()
    session = _session()

    class _Sessions:
        def get(self, _device_id):
            return session

    hub = CompanionHub(_Sessions(), store)
    device = "aa:bb:cc:dd:ee:ff"
    await hub.handle(device, {"type": "care.action", "kind": "pet"}, client_id="cid")
    first = await store.get_care(device, "cid")
    await hub.handle(device, {"type": "care.action", "kind": "pet"}, client_id="cid")
    second = await store.get_care(device, "cid")
    assert second.happiness == first.happiness
