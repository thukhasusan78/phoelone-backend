from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.rate_limit import client_key, limiter
from app.auth.service import AuthError
from app.config import Settings
from app.db.models import normalize_mac
from app.location import extract_ota_location, refine_with_wifi
from app.tools.http import SafeHttp
from app.observability.logging import get_logger
from app.observability.metrics import OTA_REQUESTS

log = get_logger(__name__)
router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.api_route("/xiaozhi/ota", methods=["GET", "POST"])
@router.api_route("/xiaozhi/ota/", methods=["GET", "POST"])
async def ota(
    request: Request,
    device_id: str | None = Header(default=None, alias="Device-Id"),
    client_id: str | None = Header(default=None, alias="Client-Id"),
    serial_number: str | None = Header(default=None, alias="Serial-Number"),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
    user_agent: str | None = Header(default=None, alias="User-Agent"),
    activation_version: str | None = Header(default=None, alias="Activation-Version"),
) -> JSONResponse:
    settings = _settings(request)
    limiter.check(client_key(request, device_id), settings.ota_rate_limit_per_minute)
    if not device_id or not client_id:
        OTA_REQUESTS.labels(method=request.method, result="bad_request").inc()
        raise HTTPException(status_code=400, detail="Device-Id and Client-Id are required")

    body: dict[str, Any] = {}
    if request.method == "POST":
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body = raw
        except Exception:
            body = {}

    locale = accept_language or body.get("language") or "my-MM"
    try:
        token = await request.app.state.auth.issue_or_get_token(
            device_id,
            client_id,
            serial_number=serial_number,
            locale=str(locale)[:16],
            user_agent=user_agent,
        )
    except AuthError as exc:
        OTA_REQUESTS.labels(method=request.method, result="forbidden").inc()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    now_ms = int(time_ms())
    payload = {
        "websocket": {
            "url": settings.websocket_url,
            "token": token,
            "version": settings.ota_websocket_version,
        },
        "server_time": {
            "timestamp": now_ms,
            "timezone_offset": settings.timezone_offset_minutes,
        },
        "firmware": {
            "version": settings.firmware_version,
            "url": settings.resolved_firmware_url,
            "force": 0,
        },
    }
    # Intentionally omit mqtt: this firmware profile uses WebSocket voice.
    # Advertising MQTT would make the device leave the low-latency WS path.
    OTA_REQUESTS.labels(method=request.method, result="ok").inc()
    board = body.get("board") if isinstance(body.get("board"), dict) else {}
    log.info(
        "ota.ok",
        method=request.method,
        device_id=device_id,
        activation_version=activation_version,
        wifi_ssid=board.get("ssid"),
        wifi_bssid=board.get("bssid") or board.get("wifi_bssid"),
    )
    store = getattr(request.app.state, "locations", None)
    if store is not None:
        hint = extract_ota_location(body)
        raw_http = getattr(request.app.state, "http", None)
        if raw_http is not None and hint.bssid:
            hint = await refine_with_wifi(SafeHttp(raw_http), hint)
        if hint.ssid or hint.bssid or hint.city or hint.latitude is not None:
            await store.put(normalize_mac(device_id), hint)
    return JSONResponse(payload)


def time_ms() -> int:
    import time

    return int(time.time() * 1000)
