from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.rate_limit import client_key, limiter
from app.auth.service import AuthError
from app.config import Settings
from app.db.models import normalize_mac
from app.location import extract_ota_location, refine_with_wifi
from app.observability.logging import get_logger
from app.observability.metrics import OTA_REQUESTS
from app.ota.boards import PRIMARY_BOARD_TYPE, is_known_board_type, normalize_board_type
from app.tools.http import SafeHttp

log = get_logger(__name__)
router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def time_ms() -> int:
    import time

    return int(time.time() * 1000)


def _ota_payload(settings: Settings, token: str) -> dict[str, Any]:
    version, url = settings.advertised_firmware()
    return {
        "websocket": {
            "url": settings.websocket_url,
            "token": token,
            "version": settings.ota_websocket_version,
        },
        "server_time": {
            "timestamp": time_ms(),
            "timezone_offset": settings.timezone_offset_minutes,
        },
        "firmware": {
            "version": version,
            "url": url,
            "force": 0,
        },
    }


async def _json_body(request: Request) -> dict[str, Any]:
    if request.method != "POST":
        return {}
    try:
        raw = await request.json()
        if isinstance(raw, dict):
            return raw
    except Exception:
        return {}
    return {}


def _extract_challenge(body: dict[str, Any]) -> str | None:
    challenge = body.get("challenge")
    payload = body.get("Payload")
    if isinstance(payload, dict):
        nested = payload.get("challenge")
        if isinstance(nested, str) and nested:
            return nested
    if isinstance(challenge, str) and challenge:
        return challenge
    return None


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

    body = await _json_body(request)
    locale = accept_language or body.get("language") or "my-MM"
    try:
        auth_result = await request.app.state.auth.prepare_ota(
            device_id,
            client_id,
            serial_number=serial_number,
            locale=str(locale)[:16],
            user_agent=user_agent,
        )
    except AuthError as exc:
        OTA_REQUESTS.labels(method=request.method, result="forbidden").inc()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    payload = _ota_payload(settings, auth_result.token)
    if auth_result.activation is not None:
        payload["activation"] = auth_result.activation
    # Intentionally omit mqtt: this firmware profile uses WebSocket voice.
    OTA_REQUESTS.labels(method=request.method, result="ok").inc()
    board = body.get("board") if isinstance(body.get("board"), dict) else {}
    board_type = normalize_board_type(board.get("type"))
    log.info(
        "ota.ok",
        method=request.method,
        device_id=device_id,
        activation_version=activation_version,
        pending=auth_result.activation is not None,
        board_type=board_type or None,
        board_known=is_known_board_type(board_type),
        board_primary=PRIMARY_BOARD_TYPE,
        board_is_primary=board_type == PRIMARY_BOARD_TYPE,
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


@router.api_route("/xiaozhi/ota/activate", methods=["POST"])
@router.api_route("/xiaozhi/ota/activate/", methods=["POST"])
async def ota_activate(
    request: Request,
    device_id: str | None = Header(default=None, alias="Device-Id"),
    client_id: str | None = Header(default=None, alias="Client-Id"),
) -> JSONResponse:
    settings = _settings(request)
    limiter.check(client_key(request, device_id), settings.ota_rate_limit_per_minute)
    if not device_id or not client_id:
        raise HTTPException(status_code=400, detail="Device-Id and Client-Id are required")

    body = await _json_body(request)
    try:
        state = await request.app.state.auth.poll_activation(
            device_id,
            client_id,
            challenge=_extract_challenge(body),
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if state == "active":
        log.info("ota.activate.ok", device_id=device_id)
        return JSONResponse({}, status_code=200)
    log.info("ota.activate.pending", device_id=device_id, state=state)
    return JSONResponse({"status": "pending"}, status_code=202)
