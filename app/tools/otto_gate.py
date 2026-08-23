"""Gate Otto MCP calls so noise / empty STT cannot sag servos via spurious stop."""

from __future__ import annotations

import re

_STOP_HINTS = (
    "ရပ်",
    "stop",
    "ပိတ်",
    "နေရာပြန်",
    "မလုပ်နဲ့",
    "မလုပ်ပါနဲ့",
    "halt",
    "freeze",
)
# Used for intent detection / tests — not required to dispatch action tools.
# Do not use a lone "က" (too many Burmese words contain it).
_MOTION_HINTS = (
    "လမ်း",
    "လျှောက်",
    "သွား",
    "ရှေ့",
    "နောက်",
    "လှည့်",
    "ခုန်",
    "ထိုင်",
    "ရပ်တည်",
    "အက",
    "ကခုန်",
    "ကပေး",
    "ကပါ",
    "ခြေ",
    "တိုး",
    "walk",
    "turn",
    "jump",
    "sit",
    "stand",
    "home",
    "dance",
    "swing",
    "move",
    "forward",
    "back",
    "left",
    "right",
    "moonwalk",
    "showcase",
)

OTTO_MOTION_TOOLS = frozenset(
    {
        "self.otto.stop",
        "self.otto.action",
        "self.otto.servo_sequences",
    }
)

_ACTION_TOOLS = frozenset({"self.otto.action", "self.otto.servo_sequences"})


def is_otto_stop_request(text: str) -> bool:
    """True when the user explicitly asked to stop / park the robot."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = raw.casefold()
    return any(hint in raw or hint in folded for hint in _STOP_HINTS)


def is_otto_motion_request(text: str) -> bool:
    """True when the user asked for motion (walk/dance/etc.) or an explicit stop."""
    raw = (text or "").strip()
    if not raw:
        return False
    if is_otto_stop_request(raw):
        return True
    folded = raw.casefold()
    return any(hint in raw or hint in folded for hint in _MOTION_HINTS)


def should_dispatch_otto_tool(name: str, user_text: str, *, device_moving: bool | None = None) -> bool:
    """
    Decide whether an Otto MCP call from the LLM should reach the device.

    Empty / filler STT never forwards motion tools (Gemini often invents stop).
    self.otto.stop still needs an explicit stop phrase, or a moving device.
    walk/dance/action: if the utterance is real speech, trust the tool call.
    Gemini hears audio natively; STT is often wrong and must not block motion.
    """
    if name not in OTTO_MOTION_TOOLS:
        return True
    if looks_like_noise_utterance(user_text):
        return False
    if name == "self.otto.stop":
        if is_otto_stop_request(user_text):
            return True
        return bool(device_moving)
    if name in _ACTION_TOOLS:
        return True
    return is_otto_motion_request(user_text)


def parse_otto_moving(status_text: str | None) -> bool:
    """Parse self.otto.get_status reply ('moving' / 'idle')."""
    raw = (status_text or "").strip().casefold()
    if not raw:
        return False
    if "moving" in raw:
        return True
    if "idle" in raw or "rest" in raw:
        return False
    return False


_NOISE_ONLY = re.compile(
    r"^(oh+|ah+|uh+|um+|mm+|hmm+|eh+|နော်+|အင်း+)[\.\!\?]*$",
    re.IGNORECASE,
)


def looks_like_noise_utterance(text: str) -> bool:
    """Single filler syllables that should not drive Otto tools."""
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return True
    if len(cleaned) <= 2 and not re.search(r"[\u1000-\u109F]", cleaned):
        return True
    return bool(_NOISE_ONLY.match(cleaned))
