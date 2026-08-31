from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from tests.activation import ota_and_bind
from tests.fakes import FakeBrain


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="memory://",
        allow_auto_provision=True,
        auth_pepper="pepper",
        gemini_api_keys="k",
        public_http_origin="http://testserver",
        public_ws_origin="ws://testserver",
    )


def _recv_json(ws):
    while True:
        payload = json.loads(ws.receive_text())
        if payload.get("type") != "ping":
            return payload


HELLO = {
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


@pytest.fixture
def app():
    settings = _settings()
    application = create_app(settings)
    brain = FakeBrain()
    application.state._test_brain = brain
    return application, brain


def _token(client: TestClient) -> str:
    _, token = ota_and_bind(client, client_id="cid")
    return token


def _connect(client: TestClient, token: str):
    return client.websocket_connect(
        "/xiaozhi/v1/",
        headers={
            "Authorization": f"Bearer {token}",
            "Protocol-Version": "1",
            "Device-Id": "aa:bb:cc:dd:ee:ff",
            "Client-Id": "cid",
        },
    )


def _complete_hello(ws) -> str:
    ws.send_text(json.dumps(HELLO))
    hello = _recv_json(ws)
    assert hello["type"] == "hello"
    assert hello["transport"] == "websocket"
    assert hello["audio_params"]["sample_rate"] == 24000
    session_id = hello["session_id"]
    mcp_init = _recv_json(ws)
    assert mcp_init["type"] == "mcp"
    assert mcp_init["payload"]["method"] == "initialize"
    ws.send_text(
        json.dumps(
            {
                "session_id": session_id,
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": mcp_init["payload"]["id"],
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mickey", "version": "2.4.2"},
                    },
                },
            }
        )
    )
    tools_list = _recv_json(ws)
    assert tools_list["payload"]["method"] == "tools/list"
    ws.send_text(
        json.dumps(
            {
                "session_id": session_id,
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": tools_list["payload"]["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "self.otto.stop",
                                "description": "stop",
                                "inputSchema": {"type": "object", "properties": {}},
                            },
                            {
                                "name": "self.otto.action",
                                "description": "action",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"action": {"type": "string"}},
                                },
                            },
                        ]
                    },
                },
            }
        )
    )
    return session_id


def test_websocket_hello_and_listen(app) -> None:
    application, brain = app

    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = _token(client)
        with _connect(client, token) as ws:
            session_id = _complete_hello(ws)
            assert session_id


def test_websocket_pong_is_known(app) -> None:
    application, brain = app

    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = _token(client)
        with _connect(client, token) as ws:
            session_id = _complete_hello(ws)
            ws.send_text(
                json.dumps({"session_id": session_id, "type": "pong", "ts_ms": 1710000000000})
            )
            ws.send_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "type": "listen",
                        "state": "start",
                        "mode": "manual",
                    }
                )
            )


def test_websocket_sensor_notify_does_not_throw(app) -> None:
    application, brain = app

    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = _token(client)
        with _connect(client, token) as ws:
            session_id = _complete_hello(ws)
            ws.send_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "type": "mcp",
                        "payload": {
                            "jsonrpc": "2.0",
                            "method": "notifications/phoe_lone.event",
                            "params": {"event": "pet", "ts_ms": 1},
                        },
                    }
                )
            )
            ws.send_text(
                json.dumps({"session_id": session_id, "type": "pong"})
            )


def test_websocket_rejects_bad_token(app) -> None:
    application, _ = app
    with TestClient(application) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/xiaozhi/v1/",
                headers={
                    "Authorization": "Bearer nope",
                    "Device-Id": "aa:bb:cc:dd:ee:ff",
                    "Client-Id": "cid",
                },
            ):
                pass


def _expect_close(ws, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        while True:
            ws.receive_text()
    assert exc.value.code == code


def test_websocket_rejects_hello_version_2(app) -> None:
    application, brain = app
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = _token(client)
        with _connect(client, token) as ws:
            ws.send_text(json.dumps({**HELLO, "version": 2}))
            _expect_close(ws, 1003)


def test_websocket_rejects_features_aec(app) -> None:
    application, brain = app
    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = _token(client)
        with _connect(client, token) as ws:
            ws.send_text(json.dumps({**HELLO, "features": {"mcp": True, "aec": True}}))
            _expect_close(ws, 1003)
