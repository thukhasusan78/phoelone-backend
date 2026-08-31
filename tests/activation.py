from __future__ import annotations

from starlette.testclient import TestClient


def device_headers(
    device_id: str = "aa:bb:cc:dd:ee:ff",
    client_id: str = "11111111-2222-3333-4444-555555555555",
) -> dict[str, str]:
    return {"Device-Id": device_id, "Client-Id": client_id}


def ota_and_bind(
    client: TestClient,
    *,
    device_id: str = "aa:bb:cc:dd:ee:ff",
    client_id: str = "11111111-2222-3333-4444-555555555555",
    extra_json: dict | None = None,
) -> tuple[dict, str]:
    headers = device_headers(device_id, client_id)
    payload = extra_json or {
        "version": 2,
        "board": {"type": "mickey", "name": "mickey"},
    }
    response = client.post("/xiaozhi/ota/", headers=headers, json=payload)
    body = response.json()
    activation = body.get("activation")
    if isinstance(activation, dict) and activation.get("code"):
        bound = client.post("/activate", json={"code": activation["code"]})
        assert bound.status_code == 200, bound.text
    return body, body["websocket"]["token"]
