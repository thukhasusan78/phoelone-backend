from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.activation import device_headers, ota_and_bind


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
def client(settings) -> Settings:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client, app


def test_health(client) -> None:
    ac, _ = client
    response = ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_portal_home(client) -> None:
    ac, _ = client
    response = ac.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Activate Mickey" in response.text


@pytest.mark.parametrize("path", ["/xiaozhi/ota", "/xiaozhi/ota/"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_ota_paths(client, path: str, method: str) -> None:
    ac, _app = client
    headers = {
        **device_headers(),
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
    assert body["activation"]["code"].isdigit()
    assert len(body["activation"]["code"]) == 6
    assert body["activation"]["challenge"]
    assert body["activation"]["message"] == (
        "Please enter the verification code in phoelone.thukha.online"
    )
    assert body["activation"]["timeout_ms"] == 30000
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
                "type": "mickey",
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
        {
            "board": {
                "ssid": "Home",
                "bssid": "AA-BB-CC-DD-EE-FF",
                "city": "Mandalay",
                "lat": 21.9,
                "lng": 96.1,
            }
        }
    )
    assert hint.bssid == "aa:bb:cc:dd:ee:ff"
    assert hint.city == "Mandalay"
    assert hint.latitude == 21.9
    assert hint.longitude == 96.1


def test_ota_unknown_fields_ok(client) -> None:
    ac, _ = client
    headers = device_headers(client_id="client-x")
    response = ac.post(
        "/xiaozhi/ota/",
        headers=headers,
        json={"version": 2, "future_field": {"nested": True}, "board": {"type": "mickey"}},
    )
    assert response.status_code == 200
    assert "activation" in response.json()


def test_ota_stores_wifi_ssid(client) -> None:
    ac, app = client
    headers = device_headers(client_id="client-wifi")
    response = ac.post(
        "/xiaozhi/ota/",
        headers=headers,
        json={"version": 2, "board": {"type": "mickey", "ssid": "MandalayFiber"}},
    )
    assert response.status_code == 200
    hint = app.state.locations._mem.get("aa:bb:cc:dd:ee:ff")
    assert hint is not None
    assert hint["ssid"] == "MandalayFiber"


def test_activation_poll_and_bind(client) -> None:
    ac, _ = client
    headers = device_headers(client_id="wait-bind")
    first = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    assert first.status_code == 200
    activation = first.json()["activation"]
    code = activation["code"]
    challenge = activation["challenge"]

    pending = ac.post("/xiaozhi/ota/activate", headers=headers, json={})
    assert pending.status_code == 202

    retry = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    assert retry.json()["activation"]["code"] == code
    assert retry.json()["activation"]["challenge"] == challenge
    assert retry.json()["websocket"]["token"] == first.json()["websocket"]["token"]

    bad = ac.post("/activate", json={"code": "000000"})
    assert bad.status_code == 400
    assert bad.json()["ok"] is False

    ok = ac.post("/activate", json={"code": code})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    done = ac.post("/xiaozhi/ota/activate", headers=headers, json={})
    assert done.status_code == 200

    bound = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    assert bound.status_code == 200
    assert "activation" not in bound.json()
    first_token = bound.json()["websocket"]["token"]
    again = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    assert again.status_code == 200
    assert again.json()["websocket"]["token"] == first_token


def test_activation_form_bind(client) -> None:
    ac, _ = client
    headers = device_headers(client_id="form-bind")
    ota = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    code = ota.json()["activation"]["code"]
    page = ac.post("/activate", data={"code": code})
    assert page.status_code == 200
    assert "Dance pad" in page.text
    assert ac.cookies.get("companion")
    assert ac.post("/xiaozhi/ota/activate", headers=headers, json={}).status_code == 200


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
    headers = device_headers()
    _, token = ota_and_bind(ac, client_id=headers["Client-Id"])
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


def test_pending_device_cannot_open_vision(client) -> None:
    ac, _ = client
    headers = device_headers(client_id="still-pending")
    ota = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
    token = ota.json()["websocket"]["token"]
    headers["Authorization"] = f"Bearer {token}"
    response = ac.post("/vision/explain/", headers=headers)
    assert response.status_code == 403


def test_ota_accepts_mickey_board_type(client) -> None:
    from app.ota.boards import PRIMARY_BOARD_TYPE, is_known_board_type

    assert PRIMARY_BOARD_TYPE == "mickey"
    assert is_known_board_type("Mickey")
    assert is_known_board_type("phoe-lone")
    assert is_known_board_type("otto-robot")
    assert not is_known_board_type("unknown-board")

    ac, _ = client
    headers = device_headers(client_id="mickey-board")
    response = ac.post(
        "/xiaozhi/ota/",
        headers=headers,
        json={"version": 2, "board": {"type": "mickey", "name": "mickey"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert "mqtt" not in body
    assert body["websocket"]["token"]
    assert body["firmware"]["force"] == 0


def test_dummy_firmware_bin_is_404(client) -> None:
    ac, _ = client
    response = ac.get("/firmware/none.bin")
    assert response.status_code == 404


def test_firmware_bin_served_and_advertised(tmp_path) -> None:
    blob = b"esp-firmware-image" + b"\x00" * 128
    (tmp_path / "mickey.bin").write_bytes(blob)
    application = create_app(
        Settings(
            environment="test",
            database_url="memory://",
            allow_auto_provision=True,
            auth_pepper="pepper",
            gemini_api_keys="k",
            public_http_origin="http://testserver",
            public_ws_origin="ws://testserver",
            firmware_dir=str(tmp_path),
            firmware_version="2.4.3",
            firmware_url="http://testserver/firmware/mickey.bin",
        )
    )
    with TestClient(application) as ac:
        got = ac.get("/firmware/mickey.bin")
        assert got.status_code == 200
        assert got.content == blob
        assert got.headers.get("content-length") == str(len(blob))
        assert "octet-stream" in (got.headers.get("content-type") or "")
        traversal = ac.get("/firmware/..%2Fsecrets.bin")
        assert traversal.status_code == 404
        headers = device_headers(client_id="fw-pub")
        ota = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
        body = ota.json()
        assert body["firmware"]["version"] == "2.4.3"
        assert body["firmware"]["url"].endswith("/firmware/mickey.bin")
        assert body["firmware"]["force"] == 0


def test_ota_stays_dummy_when_bin_missing(tmp_path) -> None:
    application = create_app(
        Settings(
            environment="test",
            database_url="memory://",
            allow_auto_provision=True,
            auth_pepper="pepper",
            gemini_api_keys="k",
            public_http_origin="http://testserver",
            public_ws_origin="ws://testserver",
            firmware_dir=str(tmp_path),
            firmware_version="2.4.3",
            firmware_url="http://testserver/firmware/mickey.bin",
        )
    )
    with TestClient(application) as ac:
        headers = device_headers(client_id="fw-missing")
        ota = ac.post("/xiaozhi/ota/", headers=headers, json={"version": 2})
        body = ota.json()
        assert body["firmware"]["version"] == "0.0.0"
        assert body["firmware"]["url"].endswith("/firmware/none.bin")
        assert ac.get("/firmware/mickey.bin").status_code == 404

