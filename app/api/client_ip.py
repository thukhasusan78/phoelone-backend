from __future__ import annotations

from fastapi import WebSocket


def client_ip_from_websocket(websocket: WebSocket) -> str | None:
    """Best-effort public client IP for geolocation (X-Forwarded-For first hop)."""
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate
    if websocket.client is not None:
        return websocket.client.host
    return None
