from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket
from starlette.websockets import WebSocketState

from app.api.client_ip import client_ip_from_websocket
from app.api.rate_limit import limiter
from app.auth.service import AuthError
from app.observability.logging import get_logger
from app.observability.metrics import WS_CONNECTIONS
from app.sessions.session import DeviceSession

log = get_logger(__name__)
router = APIRouter()


@router.websocket("/xiaozhi/v1")
@router.websocket("/xiaozhi/v1/")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    device_id = websocket.headers.get("device-id")
    client_id = websocket.headers.get("client-id")
    authorization = websocket.headers.get("authorization")
    if not device_id or not client_id:
        WS_CONNECTIONS.labels(result="missing_identity").inc()
        await websocket.close(code=1008)
        return
    try:
        limiter.check(f"ws:{device_id.lower()}", settings.ws_rate_limit_per_minute)
        await websocket.app.state.auth.authenticate_ws(device_id, client_id, authorization)
    except AuthError:
        WS_CONNECTIONS.labels(result="unauthorized").inc()
        await websocket.close(code=1008)
        return
    except HTTPException:
        WS_CONNECTIONS.labels(result="rate_limited").inc()
        await websocket.close(code=1013)
        return

    await websocket.accept()
    WS_CONNECTIONS.labels(result="ok").inc()
    session = DeviceSession(
        websocket=websocket,
        settings=settings,
        device_id=device_id.lower(),
        client_id=client_id,
        router=websocket.app.state.tool_router,
        tts_client=websocket.app.state.tts,
        brain_factory=websocket.app.state.brain_factory,
        authorization=authorization,
        client_ip=client_ip_from_websocket(websocket),
    )
    manager = websocket.app.state.sessions
    try:
        await manager.attach(session)
        await session.run()
    finally:
        await manager.detach(session)
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()
