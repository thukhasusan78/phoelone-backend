from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException, WebSocket
from starlette.websockets import WebSocketState

from app.api.rate_limit import limiter
from app.companion.chat import CHAT_HISTORY, normalize_chat_text
from app.companion.errors import CompanionError
from app.companion.games.rps import THROW_GRACE_S, THROWS, RpsMatch
from app.companion.games.ttt import TttMatch
from app.companion.life import (
    achievement_frame,
    achievements_state,
    patch_memory,
)
from app.companion.reactions import dance_payload
from app.observability.logging import get_logger
from app.protocol.state import SessionState

log = get_logger(__name__)

WAKE_HINT = "Mickey is offline. He reconnects on Wi-Fi, or press his button."
SLEEP_HINT = "Mickey is sleeping. He will wake at his alarm, or press his button."
REBOOT_HINT = "Mickey is restarting. Wait a moment; he will reconnect."
ALARM_HINT = "Mickey is offline. Wait for him to reconnect to change the alarm."
SETTINGS_HINT = "Mickey is offline. Wait for him to reconnect to change settings."

_PRESENCE_INTERVAL_S = 15.0
_NEXT_ROUND_DELAY_S = 1.15
_CARE_TAP_COOLDOWN_S = 8.0


def device_idle_exempt(state: SessionState, has_viewers: bool) -> bool:
    if state in {SessionState.THINKING, SessionState.SPEAKING}:
        return True
    return has_viewers


def offline_presence(*, sleeping: bool = False) -> dict[str, Any]:
    return {
        "type": "presence",
        "online": False,
        "state": None,
        "emotion": None,
        "battery": None,
        "charging": None,
        "sleeping": sleeping,
        "hint": SLEEP_HINT if sleeping else WAKE_HINT,
    }


def error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


class CompanionHub:
    def __init__(self, sessions, store=None) -> None:
        self.sessions = sessions
        self.store = store
        self._viewers: dict[str, set[WebSocket]] = {}
        self._client_ids: dict[str, str] = {}
        self._matches: dict[str, RpsMatch | TttMatch] = {}
        self._game_tasks: dict[str, asyncio.Task] = {}
        self._chat: dict[str, list[dict[str, Any]]] = {}
        self._care_tap_at: dict[str, float] = {}
        self._asleep: set[str] = set()
        self._lock = asyncio.Lock()

    def has_viewers(self, device_id: str) -> bool:
        return bool(self._viewers.get(device_id.lower()))

    def viewer_ids(self) -> list[str]:
        return list(self._viewers.keys())

    def is_asleep(self, device_id: str) -> bool:
        return device_id.lower() in self._asleep

    def mark_asleep(self, device_id: str) -> None:
        self._asleep.add(device_id.lower())

    def mark_awake(self, device_id: str) -> None:
        self._asleep.discard(device_id.lower())

    async def subscribe(
        self, device_id: str, websocket: WebSocket, *, client_id: str = ""
    ) -> None:
        key = device_id.lower()
        async with self._lock:
            self._viewers.setdefault(key, set()).add(websocket)
            if client_id:
                self._client_ids[key] = client_id

    async def unsubscribe(self, device_id: str, websocket: WebSocket) -> None:
        key = device_id.lower()
        async with self._lock:
            group = self._viewers.get(key)
            if not group:
                return
            group.discard(websocket)
            if not group:
                self._viewers.pop(key, None)
                self._client_ids.pop(key, None)

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
        asleep = self.is_asleep(device_id)
        live = session is not None and not session.closed
        if offline or not live:
            await self.broadcast(device_id, offline_presence(sleeping=asleep))
            return
        departing = getattr(session, "departing", None)
        if not departing:
            try:
                await session.refresh_status()
            except Exception as exc:  # noqa: BLE001
                log.info("companion.status_refresh_failed", device_id=device_id, error=str(exc))
        await self.broadcast(device_id, session.presence_snapshot())

    def current_game_state(self, device_id: str) -> dict[str, Any] | None:
        match = self._matches.get(device_id.lower())
        return match.to_state() if match else None

    def recent_chat(self, device_id: str) -> list[dict[str, Any]]:
        return list(self._chat.get(device_id.lower(), ()))

    def _remember_chat(self, device_id: str, payload: dict[str, Any]) -> None:
        key = device_id.lower()
        history = self._chat.setdefault(key, [])
        history.append(payload)
        if len(history) > CHAT_HISTORY:
            del history[:-CHAT_HISTORY]

    def _client_id(self, device_id: str, client_id: str = "") -> str:
        return client_id or self._client_ids.get(device_id.lower(), "")

    async def bootstrap_frames(self, device_id: str, client_id: str) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        memory = await self.store.get_memory(device_id, client_id)
        care = await self.store.get_care(device_id, client_id)
        codes = await self.store.list_achievements(device_id, client_id)
        return [memory.to_state(), care.to_state(), achievements_state(codes)]

    async def credit(self, device_id: str, client_id: str, kind: str) -> None:
        if self.store is None or not client_id:
            return
        try:
            state = await self.store.apply_care(device_id, client_id, kind)
        except CompanionError:
            return
        await self.broadcast(device_id, state.to_state())
        if kind == "chat" and state.chat_count >= 3:
            await self.unlock(device_id, client_id, "chat_streak_3")
        if kind == "pet":
            await self.unlock(device_id, client_id, "first_pet")

    async def unlock(self, device_id: str, client_id: str, code: str) -> None:
        if self.store is None or not client_id:
            return
        created = await self.store.unlock_achievement(device_id, client_id, code)
        if created:
            await self.broadcast(device_id, achievement_frame(code))

    async def handle(
        self, device_id: str, message: dict[str, Any], *, client_id: str = ""
    ) -> None:
        client_id = self._client_id(device_id, client_id)
        msg_type = message.get("type")
        if msg_type == "command.dance":
            await self._dance(device_id, message, client_id)
        elif msg_type == "command.emotion":
            await self._emotion(device_id, message)
        elif msg_type == "command.stop":
            await self._stop(device_id)
        elif msg_type == "game.start":
            await self._game_start(device_id, message, client_id)
        elif msg_type == "game.round":
            await self._game_round(device_id, message, client_id)
        elif msg_type == "game.move":
            await self._game_move(device_id, message, client_id)
        elif msg_type == "chat.send":
            await self._chat_send(device_id, message, client_id)
        elif msg_type == "memory.get":
            await self._memory_get(device_id, client_id)
        elif msg_type == "memory.set":
            await self._memory_set(device_id, client_id, message)
        elif msg_type == "care.action":
            await self._care_action(device_id, client_id, message)
        elif msg_type == "alarm.get":
            await self._alarm_get(device_id)
        elif msg_type == "alarm.set":
            await self._alarm_set(device_id, message, client_id)
        elif msg_type == "alarm.cancel":
            await self._alarm_cancel(device_id)
        elif msg_type == "sleep.now":
            await self._sleep_now(device_id, message, client_id)
        elif msg_type == "settings.get":
            await self._settings_get(device_id)
        elif msg_type == "settings.set":
            await self._settings_set(device_id, message)
        elif msg_type == "settings.reboot":
            await self._settings_reboot(device_id)
        elif msg_type == "settings.upgrade":
            await self._settings_upgrade(device_id)

    async def _dance(self, device_id: str, message: dict[str, Any], client_id: str) -> None:
        action = str(message.get("action") or "")
        dance_payload(action)
        session = self._require_session(device_id)
        await session.companion_action("dance", {"action": action})
        await self.credit(device_id, client_id, "dance")
        await self.unlock(device_id, client_id, "first_web_dance")
        await self.push_presence(device_id)

    async def _emotion(self, device_id: str, message: dict[str, Any]) -> None:
        emotion = str(message.get("emotion") or "")
        session = self._require_session(device_id)
        await session.companion_action("emotion", {"emotion": emotion})
        await self.push_presence(device_id)

    def _cancel_game(self, key: str) -> None:
        task = self._game_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _spawn_game(self, device_id: str, coro, label: str) -> None:
        key = device_id.lower()
        self._cancel_game(key)
        task = asyncio.create_task(coro)
        self._game_tasks[key] = task

        def _done(done: asyncio.Task) -> None:
            if self._game_tasks.get(key) is done:
                self._game_tasks.pop(key, None)
            if done.cancelled():
                return
            try:
                exc = done.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                log.info("companion.game_task_failed", device_id=device_id, game=label, error=str(exc))

        task.add_done_callback(_done)

    def _spawn_rps(self, device_id: str, client_id: str) -> None:
        self._spawn_game(device_id, self._run_rps_session(device_id, client_id), "rps")

    async def _stop(self, device_id: str) -> None:
        key = device_id.lower()
        match = self._matches.get(key)
        aborted = None
        if isinstance(match, RpsMatch) and match.phase == "countdown":
            aborted = match.abort_round()
        elif isinstance(match, TttMatch) and match.phase == "mickey_turn":
            aborted = match.abort_think()
        self._cancel_game(key)
        session = self.sessions.get(device_id)
        if session is None or session.closed:
            if aborted:
                await self.broadcast(device_id, aborted)
            await self.broadcast(device_id, offline_presence(sleeping=self.is_asleep(device_id)))
            return
        await session.companion_action("stop", {})
        if aborted:
            await self.broadcast(device_id, aborted)
            if isinstance(match, TttMatch) and aborted.get("winner"):
                await self._credit_ttt(device_id, self._client_id(device_id), aborted)
        await self.push_presence(device_id)

    async def _game_start(
        self, device_id: str, message: dict[str, Any], client_id: str = ""
    ) -> None:
        session = self._require_session(device_id)
        game = str(message.get("game") or "rps")
        if game not in {"rps", "ttt"}:
            raise CompanionError("invalid", "That game is not available.")
        if session.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        key = device_id.lower()
        self._cancel_game(key)
        if game == "ttt":
            difficulty = str(message.get("difficulty") or "easy")
            match = TttMatch()
            started = match.start(difficulty)
            self._matches[key] = match
            await self.broadcast(device_id, started)
            return
        best_of = message.get("best_of", 3)
        try:
            best_of_n = int(best_of)
        except (TypeError, ValueError):
            best_of_n = 3
        match = RpsMatch()
        started = match.start(best_of_n)
        self._matches[key] = match
        await self.broadcast(device_id, started)
        self._spawn_rps(device_id, client_id)

    async def _game_round(
        self, device_id: str, message: dict[str, Any], client_id: str = ""
    ) -> None:
        if str(message.get("game") or "rps") != "rps":
            raise CompanionError("invalid", "Tic-tac-toe has no rounds. Tap New game.")
        session = self._require_session(device_id)
        if session.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        key = device_id.lower()
        match = self._matches.get(key)
        if isinstance(match, TttMatch):
            raise CompanionError("invalid", "Tic-tac-toe has no rounds. Tap New game.")
        if match is None:
            match = RpsMatch()
            match.start()
            self._matches[key] = match
        if not isinstance(match, RpsMatch):
            raise CompanionError("invalid", "Only rock-paper-scissors uses rounds.")
        if match.phase == "match_over":
            raise CompanionError("invalid", "Match over. Tap New match.")
        if match.phase == "countdown":
            raise CompanionError("busy", "Mickey is throwing. Wait for the reveal.")
        running = self._game_tasks.get(key)
        if running is not None and not running.done():
            raise CompanionError("busy", "Mickey is throwing. Wait for the reveal.")
        self._spawn_rps(device_id, client_id)

    async def _run_rps_session(self, device_id: str, client_id: str) -> None:
        key = device_id.lower()
        try:
            while True:
                session = self.sessions.get(device_id)
                if session is None or session.closed:
                    return
                match = self._matches.get(key)
                if not isinstance(match, RpsMatch) or match.phase == "match_over":
                    return
                if session.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
                    await asyncio.sleep(0.25)
                    continue
                match_id = match.match_id
                if match.phase != "countdown":
                    try:
                        countdown = match.begin_round()
                    except ValueError:
                        return
                    await self.broadcast(device_id, countdown)

                async def reveal_now(expected_id: str = match_id) -> dict[str, Any]:
                    current = self._matches.get(key)
                    if not isinstance(current, RpsMatch) or current.match_id != expected_id:
                        return {"aborted": True}
                    if current.phase != "countdown":
                        return {"aborted": True}
                    await current.wait_for_throw(THROW_GRACE_S)
                    current = self._matches.get(key)
                    if (
                        not isinstance(current, RpsMatch)
                        or current.match_id != expected_id
                        or current.phase != "countdown"
                    ):
                        return {"aborted": True}
                    state = current.reveal()
                    await self.broadcast(device_id, state)
                    return state

                try:
                    result = await session.companion_action(
                        "rps_react",
                        {"on_reveal": reveal_now},
                    )
                except CompanionError as exc:
                    current = self._matches.get(key)
                    if (
                        isinstance(current, RpsMatch)
                        and current.match_id == match_id
                        and current.phase == "countdown"
                    ):
                        if exc.code == "busy":
                            await self.broadcast(device_id, current.abort_round())
                        else:
                            await self.broadcast(device_id, current.reveal())
                    if exc.code != "offline":
                        await self.broadcast(device_id, error_frame(exc.code, exc.message))
                    return

                current = self._matches.get(key)
                if not isinstance(current, RpsMatch) or current.match_id != match_id:
                    return
                if current.last_winner == "player":
                    await self.unlock(device_id, client_id, "first_rps_win")
                if current.last_winner in {"player", "mickey", "draw"}:
                    await self.credit(device_id, client_id, "game")
                await self.push_presence(device_id)
                if isinstance(result, dict) and result.get("aborted"):
                    return
                if current.phase == "match_over" or current.last_winner is None:
                    return
                await asyncio.sleep(_NEXT_ROUND_DELAY_S)
        except asyncio.CancelledError:
            return

    async def _credit_ttt(self, device_id: str, client_id: str, state: dict[str, Any]) -> None:
        winner = state.get("winner")
        if winner == "player":
            await self.unlock(device_id, client_id, "first_ttt_win")
        if winner in {"player", "mickey", "draw"}:
            await self.credit(device_id, client_id, "game")

    async def _run_ttt_think(self, device_id: str, client_id: str) -> None:
        key = device_id.lower()
        try:
            session = self.sessions.get(device_id)
            if session is None or session.closed:
                return
            match = self._matches.get(key)
            if not isinstance(match, TttMatch) or match.phase != "mickey_turn":
                return
            match_id = match.match_id
            try:
                await session.companion_action("ttt_react", {"mode": "think"})
            except CompanionError as exc:
                current = self._matches.get(key)
                if isinstance(current, TttMatch) and current.match_id == match_id:
                    state = current.apply_pending()
                    await self.broadcast(device_id, state)
                    if state.get("winner"):
                        await self._credit_ttt(device_id, client_id, state)
                if exc.code != "offline":
                    await self.broadcast(device_id, error_frame(exc.code, exc.message))
                return
            current = self._matches.get(key)
            if not isinstance(current, TttMatch) or current.match_id != match_id:
                return
            state = current.apply_pending()
            await self.broadcast(device_id, state)
            if state.get("winner"):
                await self._run_ttt_result(device_id, client_id, str(state["winner"]))
        except asyncio.CancelledError:
            return

    async def _run_ttt_result(self, device_id: str, client_id: str, winner: str) -> None:
        session = self.sessions.get(device_id)
        if session is None or session.closed:
            return
        try:
            await session.companion_action("ttt_react", {"mode": "result", "winner": winner})
        except CompanionError as exc:
            if exc.code != "offline":
                await self.broadcast(device_id, error_frame(exc.code, exc.message))
            return
        match = self._matches.get(device_id.lower())
        state = match.to_state() if isinstance(match, TttMatch) else {"winner": winner}
        await self._credit_ttt(device_id, client_id, state)
        await self.push_presence(device_id)

    async def _game_move(
        self, device_id: str, message: dict[str, Any], client_id: str = ""
    ) -> None:
        game = str(message.get("game") or "rps")
        if game == "ttt":
            await self._ttt_move(device_id, message, client_id)
            return
        if game != "rps":
            raise CompanionError("invalid", "That game is not available.")
        player = str(message.get("player") or "")
        if player not in THROWS:
            raise CompanionError("invalid", "Choose rock, paper, or scissors.")
        self._require_session(device_id)
        key = device_id.lower()
        match = self._matches.get(key)
        if not isinstance(match, RpsMatch) or match.phase != "countdown":
            if isinstance(match, RpsMatch) and match.phase == "match_over":
                raise CompanionError("invalid", "Match over. Tap New match.")
            raise CompanionError("invalid", "Wait for the chant, then throw.")
        try:
            match.commit(player)
        except ValueError as exc:
            raise CompanionError("invalid", "Wait for the chant, then throw.") from exc

    async def _ttt_move(
        self, device_id: str, message: dict[str, Any], client_id: str
    ) -> None:
        session = self._require_session(device_id)
        if session.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        key = device_id.lower()
        match = self._matches.get(key)
        if not isinstance(match, TttMatch):
            raise CompanionError("invalid", "Start a tic-tac-toe game first.")
        if match.phase == "match_over":
            raise CompanionError("invalid", "Game over. Tap New game.")
        if match.phase == "mickey_turn":
            raise CompanionError("busy", "Mickey is thinking.")
        raw = message.get("cell")
        try:
            cell = int(raw)
        except (TypeError, ValueError):
            raise CompanionError("invalid", "Pick a square 1 to 9.") from None
        try:
            state = match.play(cell)
        except ValueError as exc:
            reason = str(exc)
            if reason == "occupied":
                raise CompanionError("invalid", "That square is taken.") from exc
            raise CompanionError("invalid", "Pick an empty square.") from exc
        await self.broadcast(device_id, state)
        if state.get("winner"):
            self._spawn_game(
                device_id,
                self._run_ttt_result(device_id, client_id, str(state["winner"])),
                "ttt",
            )
            return
        thinking = match.begin_mickey()
        await self.broadcast(device_id, thinking)
        self._spawn_game(device_id, self._run_ttt_think(device_id, client_id), "ttt")

    async def _chat_send(
        self, device_id: str, message: dict[str, Any], client_id: str = ""
    ) -> None:
        text = normalize_chat_text(message.get("text"))
        session = self._require_session(device_id)
        if session.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        try:
            limiter.check(
                f"companion-chat:{device_id.lower()}",
                session.settings.companion_chat_rate_limit_per_minute,
            )
        except HTTPException:
            raise CompanionError("rate_limited", "Too many chat messages. Wait a moment.") from None
        user_frame = {"type": "chat.user", "text": text}
        self._remember_chat(device_id, user_frame)
        await self.broadcast(device_id, user_frame)
        try:
            result = await session.companion_action("chat", {"text": text})
        except CompanionError as exc:
            await self.broadcast(device_id, error_frame(exc.code, exc.message))
            return
        reply = {
            "type": "chat.reply",
            "text": str(result.get("text") or ""),
            "emotion": str(result.get("emotion") or session._emotion or "happy"),
            "aborted": bool(result.get("aborted")),
        }
        self._remember_chat(device_id, reply)
        await self.broadcast(device_id, reply)
        if reply["text"] and not reply["aborted"]:
            await self.credit(device_id, client_id, "chat")
        await self.push_presence(device_id)

    async def _memory_get(self, device_id: str, client_id: str) -> None:
        if self.store is None:
            return
        memory = await self.store.get_memory(device_id, client_id)
        await self.broadcast(device_id, memory.to_state())

    async def _memory_set(
        self, device_id: str, client_id: str, message: dict[str, Any]
    ) -> None:
        if self.store is None:
            raise CompanionError("invalid", "Memory is not available.")
        current = await self.store.get_memory(device_id, client_id)
        patch_memory(current, message)
        saved = await self.store.set_memory(current)
        await self.broadcast(device_id, saved.to_state())
        session = self.sessions.get(device_id)
        if session is not None and not session.closed:
            apply = getattr(session, "apply_owner_memory", None)
            if callable(apply):
                await apply(saved)

    async def _care_action(
        self, device_id: str, client_id: str, message: dict[str, Any]
    ) -> None:
        kind = str(message.get("kind") or "")
        if kind not in {"pet", "feed"}:
            raise CompanionError("invalid", "Pet or feed Mickey from the site.")
        key = device_id.lower()
        now = time.monotonic()
        last = self._care_tap_at.get(key, 0.0)
        if now - last < _CARE_TAP_COOLDOWN_S:
            if self.store is not None and client_id:
                state = await self.store.get_care(device_id, client_id)
                await self.broadcast(device_id, state.to_state())
            return
        self._care_tap_at[key] = now
        await self.credit(device_id, client_id, kind)

    async def _alarm_get(self, device_id: str) -> None:
        session = self._require_session(device_id, ALARM_HINT)
        state = await session.companion_action("alarm_get")
        await self.broadcast(device_id, state)

    async def _alarm_set(self, device_id: str, message: dict[str, Any], client_id: str = "") -> None:
        session = self._require_session(device_id, ALARM_HINT)
        state = await session.companion_action("alarm_set", message)
        await self.broadcast(device_id, state)
        if message.get("sleep_now"):
            await self.credit(device_id, client_id, "sleep")
            await self.push_presence(device_id)

    async def _alarm_cancel(self, device_id: str) -> None:
        session = self._require_session(device_id, ALARM_HINT)
        state = await session.companion_action("alarm_cancel")
        await self.broadcast(device_id, state)

    async def _sleep_now(self, device_id: str, message: dict[str, Any], client_id: str = "") -> None:
        session = self._require_session(device_id, ALARM_HINT)
        state = await session.companion_action("sleep", message)
        await self.broadcast(device_id, state)
        await self.credit(device_id, client_id, "sleep")
        await self.push_presence(device_id)

    async def _settings_get(self, device_id: str) -> None:
        session = self._require_session(device_id, SETTINGS_HINT)
        state = await session.companion_action("settings_get")
        await self.broadcast(device_id, state)

    async def _settings_set(self, device_id: str, message: dict[str, Any]) -> None:
        session = self._require_session(device_id, SETTINGS_HINT)
        state = await session.companion_action("settings_set", message)
        await self.broadcast(device_id, state)
        await self.push_presence(device_id)

    async def _settings_reboot(self, device_id: str) -> None:
        session = self._require_session(device_id, SETTINGS_HINT)
        await session.companion_action("reboot")
        await self.push_presence(device_id)

    async def _settings_upgrade(self, device_id: str) -> None:
        session = self._require_session(device_id, SETTINGS_HINT)
        await session.companion_action("upgrade")
        await self.push_presence(device_id)

    def _require_session(self, device_id: str, hint: str = WAKE_HINT):
        session = self.sessions.get(device_id)
        if session is None or session.closed:
            if self.is_asleep(device_id):
                raise CompanionError("offline", SLEEP_HINT)
            raise CompanionError("offline", hint)
        return session

    async def presence_loop(self, device_id: str, websocket: WebSocket) -> None:
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.sleep(_PRESENCE_INTERVAL_S)
                await self.push_presence(device_id)
        except asyncio.CancelledError:
            return
