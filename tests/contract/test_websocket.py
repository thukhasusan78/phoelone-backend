from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
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
    response = client.get(
        "/xiaozhi/ota/",
        headers={"Device-Id": "aa:bb:cc:dd:ee:ff", "Client-Id": "cid"},
    )
    return response.json()["websocket"]["token"]


def test_websocket_hello_and_listen(app) -> None:
    application, brain = app

    with TestClient(application) as client:
        application.state.brain_factory = lambda: brain
        token = _token(client)
        with client.websocket_connect(
            "/xiaozhi/v1/",
            headers={
                "Authorization": f"Bearer {token}",
                "Protocol-Version": "1",
                "Device-Id": "aa:bb:cc:dd:ee:ff",
                "Client-Id": "cid",
            },
        ) as ws:
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
                                "serverInfo": {"name": "otto-robot", "version": "2.4.2"},
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
