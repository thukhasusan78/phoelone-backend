from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from tests.activation import ota_and_bind
from tests.contract.test_websocket import _complete_hello, _connect, _recv_json
from tests.fakes import FakeBrain

_ALARM_JSON = '{"enabled":true,"hour":7,"minute":0,"repeat":true}'


def _recv_until(ws, msg_type: str) -> dict:
    while True:
        payload = ws.receive_json()
        if payload.get("type") == msg_type:
            return payload


def _settings(**extra) -> Settings:
    values = dict(
        environment="test",
        database_url="memory://",
        allow_auto_provision=True,
        auth_pepper="pepper",
        gemini_api_keys="k",
        public_http_origin="http://testserver",
        public_ws_origin="ws://testserver",
    )
    values.update(extra)
    return Settings(**values)


@pytest.fixture
def app():
    application = create_app(_settings())
    application.state._test_brain = FakeBrain()
    return application


def test_activate_sets_cookie_and_opens_dashboard(app) -> None:
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Activate Mickey" in home.text
        ota_and_bind(client)
        assert client.cookies.get("companion")
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Dance pad" in dashboard.text
        assert "Rock" in dashboard.text
        assert "rps-arena" in dashboard.text
        assert "rps-chant" in dashboard.text
        assert "ttt-board" in dashboard.text
        assert "btn-ttt-new" in dashboard.text
        assert "btn-ttt-hard" in dashboard.text
        assert "btn-play" in dashboard.text
        assert "alarm-time" in dashboard.text
        assert "set-volume" in dashboard.text
        assert "btn-sleep" in dashboard.text
        assert "btn-reboot" in dashboard.text
        assert "chat-log" in dashboard.text
        assert "Say something to Mickey" in dashboard.text
        assert "Mickey knows me" in dashboard.text
        assert "meter-happy" in dashboard.text
        assert "trim-left_leg" in dashboard.text
        assert "Thu Kha Su San" in dashboard.text
        assert 'id="about"' in dashboard.text
        assert "Jarvis AI Agent" in dashboard.text
        assert "https://github.com/thukhasusan78" in dashboard.text
        assert "View Creator Portfolio" in dashboard.text
        assert 'id="portfolio-panel"' in dashboard.text
        assert "Bluetooth Jammers" in dashboard.text
        assert "Power Station Internal" not in dashboard.text
        text = dashboard.text
        assert text.index("Mickey knows me") < text.index("View Creator Portfolio")
        assert text.index("View Creator Portfolio") < text.index("Thu Kha Su San")


def test_companion_ws_requires_cookie(app) -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/companion/v1/"):
                pass
        assert exc.value.code == 1008


def test_tiktok_stats_requires_cookie(app) -> None:
    with TestClient(app) as client:
        response = client.get("/api/tiktok_stats")
        assert response.status_code == 401


def test_tiktok_stats_returns_payload(app, monkeypatch) -> None:
    async def _fake(http=None, *, now=None):
        from app.companion.tiktok import TikTokStats

        return TikTokStats(9721, 75300)

    monkeypatch.setattr("app.api.companion.fetch_tiktok_stats", _fake)
    with TestClient(app) as client:
        ota_and_bind(client)
        response = client.get("/api/tiktok_stats")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["followers"] == 9721
        assert body["formatted"]["followers"] == "9.72K"
        assert body["formatted"]["likes"] == "75.3K"


def test_companion_offline_dance(app) -> None:
    with TestClient(app) as client:
        ota_and_bind(client)
        with client.websocket_connect("/companion/v1/") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            presence = _recv_until(ws, "presence")
            assert presence["online"] is False
            assert "offline" in (presence.get("hint") or "").lower()
            assert "Wake Mickey" not in (presence.get("hint") or "")
            ws.send_json({"type": "command.dance", "action": "jump"})
            error = _recv_until(ws, "error")
            assert error["code"] == "offline"


def test_companion_dance_and_stop_reach_device(app) -> None:
    application = app
    brain = FakeBrain()
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = ota_and_bind(client, client_id="cid")[1]
        with _connect(client, token) as device:
            session_id = _complete_hello(device)
            session = application.state.sessions.get("aa:bb:cc:dd:ee:ff")
            assert session is not None

            async def _skip_speak(*_a, **_k):
                return None

            session._speak = _skip_speak
            with client.websocket_connect("/companion/v1/") as portal:
                assert portal.receive_json()["type"] == "hello"
                presence = portal.receive_json()
                assert presence["online"] is True
                portal.send_json({"type": "command.dance", "action": "jump"})
                mcp = _recv_json(device)
                if mcp["type"] == "llm":
                    mcp = _recv_json(device)
                assert mcp["type"] == "mcp"
                assert mcp["payload"]["params"]["name"] == "self.otto.action"
                assert mcp["payload"]["params"]["arguments"]["action"] == "jump"
                device.send_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "type": "mcp",
                            "payload": {
                                "jsonrpc": "2.0",
                                "id": mcp["payload"]["id"],
                                "result": {"content": [{"type": "text", "text": "ok"}]},
                            },
                        }
                    )
                )
                portal.send_json({"type": "command.dance", "action": "hands_up"})
                denied = _recv_until(portal, "error")
                assert denied["code"] == "invalid"
                portal.send_json({"type": "command.stop"})
                stop = _recv_json(device)
                if stop["type"] == "llm":
                    stop = _recv_json(device)
                assert stop["payload"]["params"]["name"] == "self.otto.stop"


def test_companion_alarm_set_reaches_device(app) -> None:
    application = app
    brain = FakeBrain()
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = ota_and_bind(client, client_id="cid")[1]
        with _connect(client, token) as device:
            session_id = _complete_hello(device)
            session = application.state.sessions.get("aa:bb:cc:dd:ee:ff")
            assert session is not None
            with client.websocket_connect("/companion/v1/") as portal:
                portal.receive_json()
                portal.receive_json()
                portal.send_json(
                    {
                        "type": "alarm.set",
                        "hour": 7,
                        "minute": 0,
                        "repeat": True,
                    }
                )
                mcp = _recv_json(device)
                if mcp["type"] == "llm":
                    mcp = _recv_json(device)
                assert mcp["type"] == "mcp"
                assert mcp["payload"]["params"]["name"] == "self.mickey.alarm.set"
                assert mcp["payload"]["params"]["arguments"]["hour"] == 7
                assert mcp["payload"]["params"]["arguments"]["repeat"] is True
                device.send_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "type": "mcp",
                            "payload": {
                                "jsonrpc": "2.0",
                                "id": mcp["payload"]["id"],
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": _ALARM_JSON,
                                        }
                                    ]
                                },
                            },
                        }
                    )
                )
                get_mcp = _recv_json(device)
                if get_mcp["type"] == "llm":
                    get_mcp = _recv_json(device)
                assert get_mcp["payload"]["params"]["name"] == "self.mickey.alarm.get"
                device.send_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "type": "mcp",
                            "payload": {
                                "jsonrpc": "2.0",
                                "id": get_mcp["payload"]["id"],
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": _ALARM_JSON,
                                        }
                                    ]
                                },
                            },
                        }
                    )
                )
                state = _recv_until(portal, "alarm.state")
                assert state["set"] is True
                assert state["hour"] == 7


def test_companion_rps_state(app) -> None:
    application = app
    brain = FakeBrain()
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = ota_and_bind(client, client_id="cid")[1]
        with _connect(client, token) as device:
            _complete_hello(device)
            session = application.state.sessions.get("aa:bb:cc:dd:ee:ff")
            assert session is not None

            spoken: list[str] = []

            async def _capture_speak(text, _generation, emotion="happy"):
                spoken.append(str(text))
                return None

            async def _skip_mcp(name, arguments=None):
                return "ok"

            session._speak = _capture_speak
            session.mcp.call = _skip_mcp
            with client.websocket_connect("/companion/v1/") as portal:
                portal.receive_json()
                portal.receive_json()
                portal.send_json({"type": "game.start", "game": "rps", "best_of": 3})
                start = _recv_until(portal, "game.state")
                assert start["type"] == "game.state"
                assert start["phase"] == "awaiting_throw"
                countdown = _recv_until(portal, "game.state")
                assert countdown["type"] == "game.state"
                assert countdown["phase"] == "countdown"
                assert countdown.get("you") is None
                assert countdown.get("mickey") is None
                assert countdown.get("winner") is None
                assert countdown.get("countdown_ms")
                portal.send_json({"type": "game.move", "game": "rps", "player": "rock"})
                state = _recv_until(portal, "game.state")
                assert state["type"] == "game.state"
                assert state["you"] == "rock"
                assert state["mickey"] in {"rock", "paper", "scissors"}
                assert state["winner"] in {"player", "mickey", "draw"}
                assert "you" in state["score"]
                assert state["phase"] in {"awaiting_throw", "match_over"}
                assert spoken
                assert "Rock" in spoken[0] and "Scissors" in spoken[0]
                assert any(line != spoken[0] for line in spoken[1:])


def test_companion_ttt_state(app) -> None:
    application = app
    brain = FakeBrain()
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = ota_and_bind(client, client_id="cid")[1]
        with _connect(client, token) as device:
            _complete_hello(device)
            session = application.state.sessions.get("aa:bb:cc:dd:ee:ff")
            assert session is not None

            async def _capture_speak(text, _generation, emotion="happy"):
                return None

            async def _skip_mcp(name, arguments=None):
                return "ok"

            session._speak = _capture_speak
            session.mcp.call = _skip_mcp
            with client.websocket_connect("/companion/v1/") as portal:
                portal.receive_json()
                portal.receive_json()
                portal.send_json({"type": "game.start", "game": "ttt"})
                start = _recv_until(portal, "game.state")
                assert start["game"] == "ttt"
                assert start["phase"] == "your_turn"
                assert start["board"] == [None] * 9
                portal.send_json({"type": "game.move", "game": "ttt", "cell": 4})
                placed = _recv_until(portal, "game.state")
                assert placed["board"][4] == "x"
                for _ in range(8):
                    if "o" in (placed.get("board") or []):
                        break
                    placed = _recv_until(portal, "game.state")
                assert placed["game"] == "ttt"
                assert "o" in placed["board"]
                assert placed["board"][4] == "x"


def test_companion_chat_offline(app) -> None:
    with TestClient(app) as client:
        ota_and_bind(client)
        with client.websocket_connect("/companion/v1/") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat.send", "text": "မင်္ဂလာပါ"})
            error = _recv_until(ws, "error")
            assert error["code"] == "offline"


def test_companion_chat_rejects_empty(app) -> None:
    with TestClient(app) as client:
        ota_and_bind(client)
        with client.websocket_connect("/companion/v1/") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat.send", "text": "   "})
            error = _recv_until(ws, "error")
            assert error["code"] == "invalid"


def test_companion_chat_reaches_device(app) -> None:
    application = app
    brain = FakeBrain(output_text="မင်္ဂလာပါနော်။")
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = ota_and_bind(client, client_id="cid")[1]
        with _connect(client, token) as device:
            session_id = _complete_hello(device)
            session = application.state.sessions.get("aa:bb:cc:dd:ee:ff")
            assert session is not None

            async def _skip_stream(*_a, **_k):
                return None

            session._stream_sentence = _skip_stream
            with client.websocket_connect("/companion/v1/") as portal:
                portal.receive_json()
                portal.receive_json()
                portal.send_json({"type": "chat.send", "text": "မင်္ဂလာပါ"})
                user = _recv_until(portal, "chat.user")
                assert user["text"] == "မင်္ဂလာပါ"
                started = False
                sentence = None
                while True:
                    payload = _recv_json(device)
                    if payload.get("type") == "tts" and payload.get("state") == "start":
                        started = True
                    if payload.get("type") == "tts" and payload.get("state") == "sentence_start":
                        sentence = payload.get("text")
                        break
                assert started
                assert sentence
                assert "မင်္ဂလာပါ" in sentence or "နော်" in sentence
                reply = _recv_until(portal, "chat.reply")
                assert reply["text"]
                assert brain.text_turns == ["မင်္ဂလာပါ"]
                assert brain.begun is False
                assert session_id


def test_companion_memory_and_care_roundtrip(app) -> None:
    with TestClient(app) as client:
        ota_and_bind(client)
        with client.websocket_connect("/companion/v1/") as ws:
            _recv_until(ws, "hello")
            care = _recv_until(ws, "care.state")
            happy = care["happiness"]
            ws.send_json({"type": "memory.set", "owner_name": "Thukha", "likes": "tea"})
            memory = _recv_until(ws, "memory.state")
            assert memory["owner_name"] == "Thukha"
            assert memory["likes"] == "tea"
            ws.send_json({"type": "memory.get"})
            again = _recv_until(ws, "memory.state")
            assert again["owner_name"] == "Thukha"
            ws.send_json({"type": "care.action", "kind": "pet"})
            pet = _recv_until(ws, "care.state")
            assert pet["happiness"] > happy
            unlock = _recv_until(ws, "achieve.unlock")
            assert unlock["code"] in {"first_pet", "first_activate"}


def test_companion_care_broadcasts_to_second_tab(app) -> None:
    with TestClient(app) as client:
        ota_and_bind(client)
        with client.websocket_connect("/companion/v1/") as a:
            with client.websocket_connect("/companion/v1/") as b:
                _recv_until(a, "hello")
                _recv_until(b, "hello")
                _recv_until(a, "care.state")
                _recv_until(b, "care.state")
                a.send_json({"type": "care.action", "kind": "pet"})
                seen = _recv_until(b, "care.state")
                assert seen["happiness"] >= 55


def test_companion_unlock_with_pin() -> None:
    application = create_app(_settings(companion_pin="2468"))
    with TestClient(application) as client:
        ota_and_bind(client)
        client.cookies.clear()
        home = client.get("/")
        assert "Activate Mickey" in home.text
        bad = client.post("/companion/unlock", json={"pin": "0000"})
        assert bad.status_code == 403
        ok = client.post("/companion/unlock", json={"pin": "2468"})
        assert ok.status_code == 200
        assert client.cookies.get("companion")
        dash = client.get("/")
        assert "Dance pad" in dash.text
