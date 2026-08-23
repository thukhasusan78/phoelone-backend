from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="memory://",
        allow_auto_provision=True,
        auth_pepper="pepper",
        gemini_api_keys="k",
        public_http_origin="http://testserver",
        public_ws_origin="ws://testserver",
        firmware_url="http://testserver/firmware/none.bin",
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client, app


def test_health(client) -> None:
    ac, _ = client
    response = ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/xiaozhi/ota", "/xiaozhi/ota/"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_ota_paths(client, path: str, method: str) -> None:
    ac, _app = client
    headers = {
        "Device-Id": "aa:bb:cc:dd:ee:ff",
        "Client-Id": "11111111-2222-3333-4444-555555555555",
        "Activation-Version": "1",
        "Accept-Language": "en-US",
    }
    if method == "POST":
        response = ac.post(path, headers=headers, json={"version": 2, "language": "en-US"})
    else:
        response = ac.get(path, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "mqtt" not in body
    assert "activation" not in body
    assert body["websocket"]["url"].endswith("/xiaozhi/v1/")
    assert body["websocket"]["version"] == 1
    assert body["websocket"]["token"]
    assert body["firmware"]["version"] == "0.0.0"
    assert body["server_time"]["timezone_offset"] == 390


def test_ota_requires_identity(client) -> None:
    ac, _ = client
    response = ac.get("/xiaozhi/ota/")
    assert response.status_code == 400


def test_extract_ota_wifi_ssid_without_gps() -> None:
    from app.location import extract_ota_location

    hint = extract_ota_location(
        {
            "version": 2,
            "board": {
                "type": "otto-robot",
                "ssid": "MandalayFiber",
                "rssi": -50,
                "channel": 6,
                "ip": "192.168.1.50",
                "mac": "aa:bb:cc:dd:ee:ff",
            },
        }
    )
    assert hint.ssid == "MandalayFiber"
    assert hint.bssid is None
    assert hint.city is None
    assert hint.latitude is None


def test_extract_ota_optional_bssid_and_city() -> None:
    from app.location import extract_ota_location

    hint = extract_ota_location(
        {"board": {"ssid": "Home", "bssid": "AA-BB-CC-DD-EE-FF", "city": "Mandalay", "lat": 21.9, "lng": 96.1}}
    )
    assert hint.bssid == "aa:bb:cc:dd:ee:ff"
    assert hint.city == "Mandalay"
    assert hint.latitude == 21.9
    assert hint.longitude == 96.1


def test_ota_unknown_fields_ok(client) -> None:
    ac, _ = client
    headers = {
        "Device-Id": "aa:bb:cc:dd:ee:ff",
        "Client-Id": "client-x",
    }
    response = ac.post(
        "/xiaozhi/ota/",
        headers=headers,
        json={"version": 2, "future_field": {"nested": True}, "board": {"type": "otto-robot"}},
    )
    assert response.status_code == 200


def test_ota_stores_wifi_ssid(client) -> None:
    ac, app = client
    headers = {
        "Device-Id": "aa:bb:cc:dd:ee:ff",
        "Client-Id": "client-wifi",
    }
    response = ac.post(
        "/xiaozhi/ota/",
        headers=headers,
        json={"version": 2, "board": {"type": "otto-robot", "ssid": "MandalayFiber"}},
    )
    assert response.status_code == 200
    hint = app.state.locations._mem.get("aa:bb:cc:dd:ee:ff")
    assert hint is not None
    assert hint["ssid"] == "MandalayFiber"


def test_provisioned_only() -> None:
    settings = Settings(
        environment="test",
        database_url="memory://",
        allow_auto_provision=False,
        auth_pepper="pepper",
        gemini_api_keys="k",
        public_http_origin="http://testserver",
        public_ws_origin="ws://testserver",
    )
    app = create_app(settings)
    with TestClient(app) as ac:
        response = ac.get(
            "/xiaozhi/ota/",
            headers={"Device-Id": "aa:bb:cc:dd:ee:ff", "Client-Id": "c"},
        )
        assert response.status_code == 403


def test_vision_explain_stub(client) -> None:
    ac, _ = client
    headers = {
        "Device-Id": "aa:bb:cc:dd:ee:ff",
        "Client-Id": "11111111-2222-3333-4444-555555555555",
    }
    ota = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    token = ota.json()["websocket"]["token"]
    headers["Authorization"] = f"Bearer {token}"
    empty = ac.post("/vision/explain/", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["success"] is False
    assert empty.json()["text"] == "no image"
    uploaded = ac.post(
        "/vision/explain/",
        headers=headers,
        data={"question": "what do you see"},
        files={"file": ("camera.jpg", b"\xff\xd8fakejpeg", "image/jpeg")},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["success"] is False
    assert body["text"] == "camera not available"


def test_vision_explain_unauthorized(client) -> None:
    ac, _ = client
    response = ac.post(
        "/vision/explain/",
        headers={"Device-Id": "aa:bb:cc:dd:ee:ff", "Client-Id": "missing-device"},
    )
    assert response.status_code == 403
