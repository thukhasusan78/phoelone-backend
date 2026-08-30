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


def test_companion_ws_requires_cookie(app) -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/companion/v1/"):
                pass
        assert exc.value.code == 1008


def test_companion_offline_dance(app) -> None:
    with TestClient(app) as client:
        ota_and_bind(client)
        with client.websocket_connect("/companion/v1/") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            presence = ws.receive_json()
            assert presence["type"] == "presence"
            assert presence["online"] is False
            assert "Wake Mickey" in (presence.get("hint") or "")
            ws.send_json({"type": "command.dance", "action": "jump"})
            error = ws.receive_json()
            assert error["type"] == "error"
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

            async def _skip_speak(*_a, **_k):
                return None

            async def _skip_mcp(name, arguments=None):
                return "ok"

            session._speak = _skip_speak
            session.mcp.call = _skip_mcp
            with client.websocket_connect("/companion/v1/") as portal:
                portal.receive_json()
                portal.receive_json()
                portal.send_json({"type": "game.start", "game": "rps", "best_of": 3})
                start = _recv_until(portal, "game.state")
                assert start["type"] == "game.state"
                assert start["phase"] == "awaiting_throw"
                portal.send_json({"type": "game.move", "game": "rps", "player": "rock"})
                state = _recv_until(portal, "game.state")
                assert state["type"] == "game.state"
                assert state["you"] == "rock"
                assert state["mickey"] in {"rock", "paper", "scissors"}
                assert state["winner"] in {"player", "mickey", "draw"}
                assert "you" in state["score"]


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
