from __future__ import annotations

import time

from app.companion.auth import (
    pins_match,
    sign_companion_cookie,
    verify_companion_cookie,
)
from app.companion.errors import CompanionError
from app.companion.hub import device_idle_exempt
from app.companion.status import (
    alarm_set_args,
    can_upgrade,
    firmware_upgrade_url,
    parse_alarm_state,
    parse_battery_reading,
    parse_settings_state,
    settings_patch_calls,
)
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


def test_parse_alarm_state() -> None:
    on = parse_alarm_state('{"enabled": true, "hour": 7, "minute": 5, "repeat": true}')
    assert on["type"] == "alarm.state"
    assert on["set"] is True
    assert on["hour"] == 7
    assert on["minute"] == 5
    assert on["repeat"] is True
    off = parse_alarm_state('{"enabled": false}')
    assert off["set"] is False
    assert off["hour"] is None


def test_alarm_set_args_and_settings_patch() -> None:
    args = alarm_set_args({"hour": 19, "minute": 30, "repeat": False, "sleep_now": True})
    assert args == {"hour": 19, "minute": 30, "repeat": False, "sleep_now": True}
    try:
        alarm_set_args({"hour": 99, "minute": 0})
        raise AssertionError("expected invalid hour")
    except CompanionError as exc:
        assert exc.code == "invalid"
    calls = settings_patch_calls({"volume": 40, "theme": "light"})
    assert calls[0][0] == "self.audio_speaker.set_volume"
    assert calls[1][0] == "self.screen.set_theme"


def test_parse_settings_and_upgrade_gate() -> None:
    state = parse_settings_state(
        '{"audio_speaker": {"volume": 70}, "screen": {"brightness": 80, "theme": "dark"}}',
        firmware_version="1.2.3",
        can_upgrade=True,
    )
    assert state["volume"] == 70
    assert state["theme"] == "dark"
    assert state["can_upgrade"] is True
    assert state["trims"] == {}
    assert not can_upgrade("http://x/firmware/none.bin", "0.0.0")
    try:
        firmware_upgrade_url("http://x/firmware/none.bin", "0.0.0")
        raise AssertionError("expected dummy firmware to fail")
    except CompanionError as exc:
        assert exc.code == "invalid"


def test_trims_patch_and_parse() -> None:
    from app.companion.status import parse_firmware_version, parse_trims

    calls = settings_patch_calls({"trims": {"left_leg": 4}})
    assert calls == [("self.otto.set_trim", {"servo_type": "left_leg", "trim_value": 4})]
    parsed = parse_trims('{"left_leg": 4, "right_foot": -3, "left_hand": 9}')
    assert parsed == {"left_leg": 4, "right_foot": -3}
    assert parse_firmware_version('{"application": {"version": "2.4.2"}}') == "2.4.2"


def test_device_idle_exempt() -> None:
    assert device_idle_exempt(SessionState.THINKING, False)
    assert device_idle_exempt(SessionState.SPEAKING, False)
    assert device_idle_exempt(SessionState.READY, True)
    assert not device_idle_exempt(SessionState.READY, False)
    assert not device_idle_exempt(SessionState.LISTENING, False)
