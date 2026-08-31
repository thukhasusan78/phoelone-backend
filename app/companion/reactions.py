from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.companion.errors import CompanionError

DANCE_ACTIONS = frozenset(
    {
        "walk",
        "jump",
        "swing",
        "moonwalk",
        "bend",
        "shake_leg",
        "updown",
        "sit",
        "showcase",
        "home",
    }
)

_HAND_ACTIONS = frozenset(
    {
        "hands_up",
        "hands_down",
        "hand_wave",
        "windmill",
        "takeoff",
        "fitness",
        "greeting",
        "shy",
        "radio_calisthenics",
        "magic_circle",
    }
)

_SLOW_LOCO = frozenset({"walk", "turn"})

_RPS_LINES = {
    "player": (
        "အင်း ရှုံးသွားတယ်။",
        "ရှင် နိုင်သွားတာပဲ။",
        "နောက်တစ်ပွဲ ပြန်ကစားမယ်နော်။",
    ),
    "mickey": (
        "ကျွန်တော် နိုင်ပြီ။",
        "ဟီး ကျွန်တော် နိုင်တယ်။",
        "ဒီတစ်ပွဲ ကျွန်တော်ပဲ။",
    ),
    "draw": (
        "တူနေတယ်နော်။",
        "သရေပဲ။",
        "နောက်တစ်ခါ ထပ်လုပ်ကြမယ်။",
    ),
}

_RPS_LINE_INDEX = {"player": 0, "mickey": 0, "draw": 0}

_RPS_COUNTDOWN_LINE = "Rock... Paper... Scissors!"
_RPS_TIMEOUT_LINE = "အင်း မမှီဘူး။"
_RPS_MATCH_OVER = {
    "player": "ရှင်ပဲ ဒီပွဲ နိုင်သွားပြီ။",
    "mickey": "ဒီပွဲ ကျွန်တော် နိုင်ပြီ။",
}
RPS_SIT_HOLD_S = 0.4


@dataclass(frozen=True)
class RpsPlan:
    think_emotion: str
    motion: dict[str, Any]
    line: str
    end_emotion: str
    countdown_line: str = _RPS_COUNTDOWN_LINE


def rps_countdown_line() -> str:
    return _RPS_COUNTDOWN_LINE


def rps_timeout_line() -> str:
    return _RPS_TIMEOUT_LINE


def dance_payload(action: str) -> dict[str, Any]:
    if action in _HAND_ACTIONS:
        raise CompanionError("invalid", "This robot has no hand servos.")
    if action not in DANCE_ACTIONS:
        raise CompanionError("invalid", "That move is not available.")
    payload: dict[str, Any] = {"action": action}
    if action in _SLOW_LOCO:
        payload["steps"] = 2
        payload["speed"] = 2000
        payload["direction"] = 1
    elif action in {"jump", "swing", "moonwalk", "bend", "shake_leg", "updown"}:
        payload["steps"] = 1
        payload["speed"] = 1200
    return payload


def rps_recover_motion() -> dict[str, Any]:
    return dance_payload("home")


def rps_plan(winner: str, *, match_over: bool = False) -> RpsPlan:
    if winner not in _RPS_LINES:
        winner = "draw"
    idx = _RPS_LINE_INDEX[winner] % len(_RPS_LINES[winner])
    _RPS_LINE_INDEX[winner] = idx + 1
    line = _RPS_LINES[winner][idx]
    closer = _RPS_MATCH_OVER.get(winner, "") if match_over else ""
    if closer:
        line = f"{line} {closer}"
    if winner == "player":
        return RpsPlan(
            think_emotion="thinking",
            motion=dance_payload("sit"),
            line=line,
            end_emotion="sad",
        )
    if winner == "mickey":
        return RpsPlan(
            think_emotion="thinking",
            motion=dance_payload("jump"),
            line=line,
            end_emotion="happy",
        )
    return RpsPlan(
        think_emotion="thinking",
        motion=dance_payload("home"),
        line=line,
        end_emotion="confused",
    )


def rps_think_motion() -> dict[str, Any]:
    return {"action": "swing", "steps": 1, "speed": 1400, "amount": 20}
