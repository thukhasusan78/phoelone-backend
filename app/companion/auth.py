from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.websockets import WebSocket

from app.config import Settings
from app.db.models import hash_token, normalize_mac, tokens_match

COOKIE_NAME = "companion"


@dataclass(frozen=True)
class CompanionIdentity:
    device_id: str
    client_id: str


def sign_companion_cookie(
    device_id: str,
    client_id: str,
    pepper: str,
    *,
    ttl_s: int,
    now: float | None = None,
) -> str:
    exp = int((now if now is not None else time.time()) + ttl_s)
    device_id = normalize_mac(device_id)
    client_id = client_id.strip()
    payload = f"{device_id}|{client_id}|{exp}"
    signature = hash_token(payload, pepper)
    return f"{payload}|{signature}"


def verify_companion_cookie(
    value: str | None,
    pepper: str,
    *,
    now: float | None = None,
) -> CompanionIdentity | None:
    if not value or value.count("|") != 3:
        return None
    device_id, client_id, exp_raw, signature = value.split("|", 3)
    if not device_id or not client_id or not exp_raw.isdigit():
        return None
    payload = f"{device_id}|{client_id}|{exp_raw}"
    if not tokens_match(payload, signature, pepper):
        return None
    if int(exp_raw) <= int(now if now is not None else time.time()):
        return None
    return CompanionIdentity(device_id=normalize_mac(device_id), client_id=client_id)


def identity_from_cookies(cookies: dict[str, str], pepper: str) -> CompanionIdentity | None:
    return verify_companion_cookie(cookies.get(COOKIE_NAME), pepper)


def identity_from_request(request: Request, settings: Settings) -> CompanionIdentity | None:
    return identity_from_cookies(request.cookies, settings.auth_pepper)


def identity_from_websocket(websocket: WebSocket, settings: Settings) -> CompanionIdentity | None:
    return identity_from_cookies(websocket.cookies, settings.auth_pepper)


def set_companion_cookie(
    response: Response,
    settings: Settings,
    device_id: str,
    client_id: str,
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        sign_companion_cookie(
            device_id,
            client_id,
            settings.auth_pepper,
            ttl_s=settings.companion_cookie_ttl_s,
        ),
        max_age=settings.companion_cookie_ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def clear_companion_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def pins_match(provided: str, configured: str) -> bool:
    if not configured:
        return False
    left = provided.strip().encode("utf-8")
    right = configured.strip().encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def parse_pin_payload(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("pin") or "")
    return ""
