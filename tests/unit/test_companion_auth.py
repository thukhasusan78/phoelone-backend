from __future__ import annotations

import time

from app.companion.auth import (
    pins_match,
    sign_companion_cookie,
    verify_companion_cookie,
)
from app.companion.hub import device_idle_exempt
from app.companion.status import parse_battery_reading
from app.protocol.state import SessionState


def test_cookie_roundtrip() -> None:
    value = sign_companion_cookie("AA-BB-CC-DD-EE-FF", "cid", "pepper", ttl_s=60)
    identity = verify_companion_cookie(value, "pepper")
    assert identity is not None
    assert identity.device_id == "aa:bb:cc:dd:ee:ff"
    assert identity.client_id == "cid"


def test_cookie_rejects_bad_signature() -> None:
    value = sign_companion_cookie("aa:bb:cc:dd:ee:ff", "cid", "pepper", ttl_s=60)
    assert verify_companion_cookie(value + "x", "pepper") is None
    assert verify_companion_cookie(value, "other") is None


def test_cookie_expires() -> None:
    now = time.time()
    value = sign_companion_cookie("aa:bb:cc:dd:ee:ff", "cid", "pepper", ttl_s=10, now=now)
    assert verify_companion_cookie(value, "pepper", now=now + 11) is None
    assert verify_companion_cookie(value, "pepper", now=now + 5) is not None


def test_pins_match() -> None:
    assert pins_match("secret", "secret")
    assert not pins_match("secret", "other")
    assert not pins_match("secret", "")
    assert not pins_match("ab", "abc")


def test_parse_battery_reading() -> None:
    assert parse_battery_reading('{"level": 80, "charging": true}') == (80, True)
    assert parse_battery_reading('{"battery": {"level": 12, "charging": false}}') == (12, False)
    assert parse_battery_reading("not-json") == (None, None)


def test_device_idle_exempt() -> None:
    assert device_idle_exempt(SessionState.THINKING, False)
    assert device_idle_exempt(SessionState.SPEAKING, False)
    assert device_idle_exempt(SessionState.READY, True)
    assert not device_idle_exempt(SessionState.READY, False)
    assert not device_idle_exempt(SessionState.LISTENING, False)
