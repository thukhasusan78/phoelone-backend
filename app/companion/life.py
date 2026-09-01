from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.companion.errors import CompanionError

METER_MAX = 100
NAME_MAX = 48
LIKES_MAX = 160
CARE_TICK_S = 480.0
NEGLECT_BEFORE_DECAY = timedelta(hours=3)
BOND_NEGLECT = timedelta(hours=24)
# Myanmar standard time — streak "days" match the robot's local clock.
CARE_TZ_OFFSET_MINUTES = 390

_CARE_DELTA: dict[str, dict[str, int]] = {
    "pet": {"happiness": 8, "bond": 3},
    "feed": {"happiness": 4},
    "chat": {"happiness": 3, "bond": 1},
    "voice": {"bond": 1},
    "game": {"happiness": 5, "bond": 1},
    "dance": {"happiness": 3, "energy": -6},
    "sleep": {"energy": 18},
    "decay": {"happiness": -2, "energy": -1},
}

ACHIEVEMENT_TITLES = {
    "first_activate": "First hello",
    "first_web_dance": "First web dance",
    "first_rps_win": "Beat Mickey",
    "first_ttt_win": "Tic-tac-toe champ",
    "chat_streak_3": "Three chats",
    "first_pet": "First pet",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_date_iso(stamp: datetime, *, offset_minutes: int = CARE_TZ_OFFSET_MINUTES) -> str:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(timezone(timedelta(minutes=offset_minutes)))
    return local.date().isoformat()


def clamp_meter(value: int) -> int:
    return max(0, min(METER_MAX, int(value)))


def cap_field(raw: object, limit: int) -> str:
    return " ".join(str(raw or "").split()).strip()[:limit]


@dataclass
class OwnerMemory:
    device_id: str
    client_id: str
    owner_name: str = ""
    nickname: str = ""
    likes: str = ""
    locale: str = "my-MM"
    updated_at: datetime | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "type": "memory.state",
            "owner_name": self.owner_name,
            "nickname": self.nickname,
            "likes": self.likes,
            "locale": self.locale,
        }


@dataclass
class CareState:
    device_id: str
    client_id: str
    happiness: int = 55
    energy: int = 70
    bond: int = 30
    streak_days: int = 0
    chat_count: int = 0
    last_touch_at: datetime | None = None
    last_streak_on: str | None = None
    updated_at: datetime | None = None

    def to_state(self) -> dict[str, Any]:
        updated = self.updated_at.isoformat() if self.updated_at else None
        return {
            "type": "care.state",
            "happiness": self.happiness,
            "energy": self.energy,
            "bond": self.bond,
            "streak_days": self.streak_days,
            "updated_at": updated,
        }


def empty_memory(device_id: str, client_id: str) -> OwnerMemory:
    return OwnerMemory(device_id=device_id, client_id=client_id)


def empty_care(device_id: str, client_id: str) -> CareState:
    return CareState(device_id=device_id, client_id=client_id, updated_at=utcnow())


def patch_memory(current: OwnerMemory, payload: dict[str, Any]) -> OwnerMemory:
    if "owner_name" in payload:
        current.owner_name = cap_field(payload.get("owner_name"), NAME_MAX)
    if "nickname" in payload:
        current.nickname = cap_field(payload.get("nickname"), NAME_MAX)
    if "likes" in payload:
        current.likes = cap_field(payload.get("likes"), LIKES_MAX)
    current.updated_at = utcnow()
    return current


def owner_prompt_prefix(memory: OwnerMemory | None) -> str:
    if memory is None:
        return ""
    if not (memory.owner_name or memory.nickname or memory.likes):
        return ""
    lines = [
        "OWNER MEMORY (saved on the companion dashboard — real facts, not an INTERNAL EVENT)",
        "Use these when you speak. Do not invent extra biography.",
    ]
    if memory.owner_name:
        lines.append(f"Owner's spoken name: {memory.owner_name}")
    if memory.nickname:
        lines.append(f"They call you: {memory.nickname}")
    if memory.likes:
        lines.append(f"They like: {memory.likes}")
    return "\n".join(lines)


def apply_care(state: CareState, kind: str, *, now: datetime | None = None) -> CareState:
    if kind not in _CARE_DELTA:
        raise CompanionError("invalid", "Unknown care action.")
    stamp = now or utcnow()
    delta = _CARE_DELTA[kind]
    state.happiness = clamp_meter(state.happiness + delta.get("happiness", 0))
    state.energy = clamp_meter(state.energy + delta.get("energy", 0))
    state.bond = clamp_meter(state.bond + delta.get("bond", 0))
    if kind != "decay":
        _touch_streak(state, stamp)
        if kind == "chat":
            state.chat_count += 1
        state.last_touch_at = stamp
    state.updated_at = stamp
    return state


def _touch_streak(state: CareState, stamp: datetime) -> None:
    today = local_date_iso(stamp)
    if state.last_streak_on == today:
        return
    yesterday = local_date_iso(stamp - timedelta(days=1))
    if state.last_streak_on == yesterday:
        state.streak_days += 1
    else:
        state.streak_days = 1
    state.last_streak_on = today


def should_decay(state: CareState, *, now: datetime | None = None) -> bool:
    stamp = now or utcnow()
    touched = state.last_touch_at or state.updated_at
    if touched is None:
        return False
    if touched.tzinfo is None:
        touched = touched.replace(tzinfo=timezone.utc)
    return stamp - touched >= NEGLECT_BEFORE_DECAY


def decay_care(state: CareState, *, now: datetime | None = None) -> CareState:
    stamp = now or utcnow()
    if not should_decay(state, now=stamp):
        return state
    apply_care(state, "decay", now=stamp)
    touched = state.last_touch_at
    if touched is not None:
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=timezone.utc)
        if stamp - touched >= BOND_NEGLECT:
            state.bond = clamp_meter(state.bond - 1)
            state.updated_at = stamp
    return state


def achievement_frame(code: str) -> dict[str, Any]:
    return {
        "type": "achieve.unlock",
        "code": code,
        "title": ACHIEVEMENT_TITLES.get(code, code),
    }


def achievements_state(codes: list[str]) -> dict[str, Any]:
    return {
        "type": "achieve.state",
        "codes": list(codes),
        "titles": {code: ACHIEVEMENT_TITLES.get(code, code) for code in codes},
    }
