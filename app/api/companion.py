from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.portal import pin_unlock_page
from app.api.rate_limit import client_key, limiter
from app.companion.auth import (
    clear_companion_cookie,
    identity_from_request,
    identity_from_websocket,
    parse_pin_payload,
    pins_match,
    set_companion_cookie,
)
from app.companion.errors import CompanionError
from app.companion.hub import error_frame
from app.companion.tiktok import TikTokStatsError, fetch_tiktok_stats
from app.observability.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


def _wants_json(request: Request) -> bool:
    content_type = (request.headers.get("content-type") or "").lower()
    return "application/json" in content_type


@router.get("/api/tiktok_stats")
@router.get("/companion/tiktok-stats")
async def tiktok_stats(request: Request):
    settings = request.app.state.settings
    identity = identity_from_request(request, settings)
    if identity is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    limiter.check(client_key(request), settings.companion_rate_limit_per_minute)
    try:
        stats = await fetch_tiktok_stats()
    except TikTokStatsError as exc:
        log.info("tiktok.stats_unavailable", error=str(exc))
        raise HTTPException(status_code=502, detail="tiktok stats unavailable") from exc
    return stats.to_json()


@router.post("/companion/logout")
async def companion_logout(request: Request):
    if _wants_json(request):
        response = JSONResponse({"ok": True})
    else:
        response = RedirectResponse(url="/", status_code=303)
    clear_companion_cookie(response)
    return response


@router.post("/companion/unlock", response_model=None)
async def companion_unlock(request: Request):
    settings = request.app.state.settings
    limiter.check(client_key(request), settings.companion_rate_limit_per_minute)
    if not settings.companion_pin:
        error = "PIN unlock is not configured."
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": error}, status_code=403)
        return pin_unlock_page(request, error=error, status_code=403)

    if _wants_json(request):
        try:
            raw = await request.json()
        except Exception:
            raw = {}
        pin = parse_pin_payload(raw)
        device_hint = str(raw.get("device_id") or "") if isinstance(raw, dict) else ""
    else:
        form = await request.form()
        pin = str(form.get("pin") or "")
        device_hint = str(form.get("device_id") or "")

    if not pins_match(pin, settings.companion_pin):
        error = "Wrong PIN."
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": error}, status_code=403)
        return pin_unlock_page(request, error=error, status_code=403)

    rows = [row for row in await request.app.state.auth.repo.list_devices() if row.is_active]
    if not rows:
        error = "No active robot is linked yet."
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": error}, status_code=404)
        return pin_unlock_page(request, error=error, status_code=404)

    record = rows[0]
    if len(rows) > 1:
        if device_hint:
            hint = device_hint.strip().lower()
            record = next((row for row in rows if row.device_id == hint), record)
        else:
            record = max(rows, key=lambda row: row.last_seen_at or row.created_at)

    if _wants_json(request):
        response: JSONResponse | RedirectResponse = JSONResponse({"ok": True})
    else:
        response = RedirectResponse(url="/", status_code=303)
    set_companion_cookie(response, settings, record.device_id, record.client_id)
    return response


@router.websocket("/companion/v1")
@router.websocket("/companion/v1/")
async def companion_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    identity = identity_from_websocket(websocket, settings)
    if identity is None:
        await websocket.close(code=1008)
        return
    record = await websocket.app.state.auth.repo.get(identity.device_id, identity.client_id)
    if record is None or not record.is_active:
        await websocket.close(code=1008)
        return
    try:
        limiter.check(
            f"companion:{identity.device_id}",
            settings.companion_rate_limit_per_minute,
        )
    except HTTPException:
        await websocket.close(code=1013)
        return

    await websocket.accept()
    hub = websocket.app.state.companion
    device_id = identity.device_id
    await hub.subscribe(device_id, websocket, client_id=identity.client_id)
    await websocket.send_json({"type": "hello", "device_id": device_id})
    await hub.push_presence(device_id)
    game = hub.current_game_state(device_id)
    if game:
        await websocket.send_json(game)
    for line in hub.recent_chat(device_id):
        await websocket.send_json(line)
    for frame in await hub.bootstrap_frames(device_id, identity.client_id):
        await websocket.send_json(frame)
    pulse = asyncio.create_task(hub.presence_loop(device_id, websocket))
    try:
        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                await websocket.send_json(error_frame("invalid", "Expected a JSON object."))
                continue
            try:
                limiter.check(
                    f"companion:{identity.device_id}",
                    settings.companion_rate_limit_per_minute,
                )
            except HTTPException:
                await websocket.send_json(
                    error_frame("rate_limited", "Too many companion commands. Wait a moment.")
                )
                continue
            try:
                await hub.handle(device_id, raw, client_id=identity.client_id)
            except CompanionError as exc:
                await websocket.send_json(error_frame(exc.code, exc.message))
            except Exception as exc:  # noqa: BLE001
                log.warning("companion.handle_failed", error=str(exc), device_id=device_id)
                await websocket.send_json(error_frame("invalid", "That command failed."))
    except WebSocketDisconnect:
        pass
    finally:
        pulse.cancel()
        try:
            await pulse
        except (asyncio.CancelledError, Exception):
            pass
        await hub.unsubscribe(device_id, websocket)
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass
