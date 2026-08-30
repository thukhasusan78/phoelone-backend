from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.companion.errors import CompanionError
from app.companion.games.rps import THROWS, RpsMatch
from app.companion.reactions import dance_payload
from app.observability.logging import get_logger
from app.protocol.state import SessionState

log = get_logger(__name__)

WAKE_HINT = "Wake Mickey (button or wake word), then play."

_PRESENCE_INTERVAL_S = 15.0


def device_idle_exempt(state: SessionState, has_viewers: bool) -> bool:
    if state in {SessionState.THINKING, SessionState.SPEAKING}:
        return True
    return has_viewers


def offline_presence() -> dict[str, Any]:
    return {
        "type": "presence",
        "online": False,
        "state": None,
        "emotion": None,
        "battery": None,
        "charging": None,
        "hint": WAKE_HINT,
    }


def error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


class CompanionHub:
    def __init__(self, sessions) -> None:
        self.sessions = sessions
        self._viewers: dict[str, set[WebSocket]] = {}
        self._matches: dict[str, RpsMatch] = {}
        self._lock = asyncio.Lock()

    def has_viewers(self, device_id: str) -> bool:
        return bool(self._viewers.get(device_id.lower()))

    async def subscribe(self, device_id: str, websocket: WebSocket) -> None:
        key = device_id.lower()
        async with self._lock:
            self._viewers.setdefault(key, set()).add(websocket)

    async def unsubscribe(self, device_id: str, websocket: WebSocket) -> None:
        key = device_id.lower()
        async with self._lock:
            group = self._viewers.get(key)
            if not group:
                return
            group.discard(websocket)
            if not group:
                self._viewers.pop(key, None)

    async def broadcast(self, device_id: str, payload: dict[str, Any]) -> None:
        key = device_id.lower()
        async with self._lock:
            sockets = list(self._viewers.get(key, ()))
        dead: list[WebSocket] = []
        for ws in sockets:
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                group = self._viewers.get(key)
                if group:
                    for ws in dead:
                        group.discard(ws)
                    if not group:
                        self._viewers.pop(key, None)

    async def push_presence(self, device_id: str, *, offline: bool = False) -> None:
        session = self.sessions.get(device_id)
        if offline or session is None or session.closed:
            await self.broadcast(device_id, offline_presence())
            return
        try:
            await session.refresh_status()
        except Exception as exc:  # noqa: BLE001
            log.info("companion.status_refresh_failed", device_id=device_id, error=str(exc))
        await self.broadcast(device_id, session.presence_snapshot())

    def current_game_state(self, device_id: str) -> dict[str, Any] | None:
        match = self._matches.get(device_id.lower())
        return match.to_state() if match else None

    async def handle(self, device_id: str, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type == "command.dance":
            await self._dance(device_id, message)
        elif msg_type == "command.stop":
            await self._stop(device_id)
        elif msg_type == "game.start":
            await self._game_start(device_id, message)
        elif msg_type == "game.move":
            await self._game_move(device_id, message)

    async def _dance(self, device_id: str, message: dict[str, Any]) -> None:
        action = str(message.get("action") or "")
        dance_payload(action)
        session = self._require_session(device_id)
        await session.companion_action("dance", {"action": action})
        await self.push_presence(device_id)

    async def _stop(self, device_id: str) -> None:
        session = self.sessions.get(device_id)
        if session is None or session.closed:
            await self.broadcast(device_id, offline_presence())
            return
        await session.companion_action("stop", {})
        await self.push_presence(device_id)

    async def _game_start(self, device_id: str, message: dict[str, Any]) -> None:
        self._require_session(device_id)
        game = str(message.get("game") or "rps")
        if game != "rps":
            raise CompanionError("invalid", "Only rock-paper-scissors is available.")
        best_of = message.get("best_of", 3)
        try:
            best_of_n = int(best_of)
        except (TypeError, ValueError):
            best_of_n = 3
        match = self._matches.setdefault(device_id.lower(), RpsMatch())
        await self.broadcast(device_id, match.start(best_of_n))

    async def _game_move(self, device_id: str, message: dict[str, Any]) -> None:
        if str(message.get("game") or "rps") != "rps":
            raise CompanionError("invalid", "Only rock-paper-scissors is available.")
        player = str(message.get("player") or "")
        if player not in THROWS:
            raise CompanionError("invalid", "Choose rock, paper, or scissors.")
        session = self._require_session(device_id)
        if session.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        match = self._matches.get(device_id.lower())
        if match is None or match.phase == "match_over":
            match = RpsMatch()
            match.start()
            self._matches[device_id.lower()] = match
        try:
            state = match.move(player)
        except ValueError as exc:
            raise CompanionError("invalid", "Start a new match first.") from exc
        await self.broadcast(device_id, state)
        winner = state.get("winner")
        if winner:
            try:
                await session.companion_action("rps_react", {"winner": winner})
            except CompanionError as exc:
                if exc.code != "offline":
                    await self.broadcast(device_id, error_frame(exc.code, exc.message))
            await self.push_presence(device_id)

    def _require_session(self, device_id: str):
        session = self.sessions.get(device_id)
        if session is None or session.closed:
            raise CompanionError("offline", WAKE_HINT)
        return session

    async def presence_loop(self, device_id: str, websocket: WebSocket) -> None:
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.sleep(_PRESENCE_INTERVAL_S)
                await self.push_presence(device_id)
        except asyncio.CancelledError:
            return
