from __future__ import annotations

from typing import Any

import orjson

from app.companion.errors import CompanionError

BODY_TRIM_SERVOS = ("left_leg", "right_leg", "left_foot", "right_foot")
ALL_TRIM_SERVOS = BODY_TRIM_SERVOS + ("left_hand", "right_hand")


def parse_battery_reading(text: str) -> tuple[int | None, bool | None]:
    data = _as_dict(text)
    if not data:
        return None, None
    battery = data.get("battery")
    blob = battery if isinstance(battery, dict) else data
    level = blob.get("level", blob.get("percent", blob.get("battery")))
    charging = blob.get("charging")
    parsed_level: int | None
    try:
        parsed_level = int(level) if level is not None else None
    except (TypeError, ValueError):
        parsed_level = None
    parsed_charging: bool | None
    if charging is None:
        parsed_charging = None
    else:
        parsed_charging = bool(charging)
    return parsed_level, parsed_charging


def parse_alarm_state(text: str) -> dict[str, Any]:
    data = _as_dict(text)
    hour = _int_or_none(data.get("hour"), 0, 23)
    minute = _int_or_none(data.get("minute"), 0, 59)
    enabled = data.get("enabled", data.get("set"))
    if enabled is None:
        enabled = hour is not None and minute is not None
    repeat = data.get("repeat")
    if repeat is None:
        repeat = True
    is_set = bool(enabled) and hour is not None and minute is not None
    return {
        "type": "alarm.state",
        "set": is_set,
        "hour": hour if is_set else None,
        "minute": minute if is_set else None,
        "repeat": bool(repeat) if is_set else True,
    }


def parse_settings_state(
    text: str,
    *,
    firmware_version: str = "",
    can_upgrade: bool = False,
) -> dict[str, Any]:
    data = _as_dict(text)
    speaker = data.get("audio_speaker") if isinstance(data.get("audio_speaker"), dict) else data
    screen = data.get("screen") if isinstance(data.get("screen"), dict) else data
    mode = data.get("press_to_talk", data.get("talk_mode", data.get("mode")))
    if mode not in {"press_to_talk", "click_to_talk"}:
        mode = None
    theme = screen.get("theme") if isinstance(screen, dict) else None
    if theme not in {"light", "dark"}:
        theme = None
    volume = speaker.get("volume") if isinstance(speaker, dict) else None
    brightness = screen.get("brightness") if isinstance(screen, dict) else None
    return {
        "type": "settings.state",
        "volume": _int_or_none(volume, 0, 100),
        "brightness": _int_or_none(brightness, 0, 100),
        "theme": theme,
        "press_to_talk": mode,
        "firmware_version": firmware_version or None,
        "can_upgrade": bool(can_upgrade),
        "trims": {},
    }


def alarm_set_args(message: dict[str, Any]) -> dict[str, Any]:
    hour = _int_or_none(message.get("hour"), 0, 23)
    minute = _int_or_none(message.get("minute"), 0, 59)
    if hour is None or minute is None:
        raise CompanionError("invalid", "Pick a time between 00:00 and 23:59.")
    repeat = message.get("repeat")
    if repeat is None:
        repeat = True
    return {
        "hour": hour,
        "minute": minute,
        "repeat": bool(repeat),
        "sleep_now": bool(message.get("sleep_now")),
    }


def settings_patch_calls(message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    if "volume" in message and message.get("volume") is not None:
        volume = _int_or_none(message.get("volume"), 0, 100)
        if volume is None:
            raise CompanionError("invalid", "Volume must be 0 to 100.")
        calls.append(("self.audio_speaker.set_volume", {"volume": volume}))
    if "brightness" in message and message.get("brightness") is not None:
        brightness = _int_or_none(message.get("brightness"), 0, 100)
        if brightness is None:
            raise CompanionError("invalid", "Brightness must be 0 to 100.")
        calls.append(("self.screen.set_brightness", {"brightness": brightness}))
    if "theme" in message and message.get("theme") is not None:
        theme = str(message.get("theme") or "")
        if theme not in {"light", "dark"}:
            raise CompanionError("invalid", "Theme is light or dark.")
        calls.append(("self.screen.set_theme", {"theme": theme}))
    if "press_to_talk" in message and message.get("press_to_talk") is not None:
        mode = str(message.get("press_to_talk") or "")
        if mode not in {"press_to_talk", "click_to_talk"}:
            raise CompanionError("invalid", "Talk mode is tap or hold.")
        calls.append(("self.set_press_to_talk", {"mode": mode}))
    trims = message.get("trims")
    if isinstance(trims, dict):
        for servo, raw in trims.items():
            name = str(servo or "")
            if name not in ALL_TRIM_SERVOS:
                raise CompanionError("invalid", "Unknown servo trim.")
            value = _int_or_none(raw, -50, 50)
            if value is None:
                raise CompanionError("invalid", "Trim must be -50 to 50.")
            calls.append(("self.otto.set_trim", {"servo_type": name, "trim_value": value}))
    if not calls:
        raise CompanionError("invalid", "Nothing to change.")
    return calls


def parse_trims(text: str) -> dict[str, int]:
    data = _as_dict(text)
    out: dict[str, int] = {}
    for name in BODY_TRIM_SERVOS:
        value = _int_or_none(data.get(name), -50, 50)
        if value is not None:
            out[name] = value
    return out


def parse_firmware_version(text: str) -> str | None:
    data = _as_dict(text)
    application = data.get("application")
    if isinstance(application, dict):
        version = application.get("version")
        if version is not None and str(version).strip():
            return str(version).strip()
    version = data.get("firmware_version") or data.get("version")
    if isinstance(version, str) and version.strip() and version.strip() not in {"1", "2"}:
        return version.strip()
    return None


def can_upgrade(firmware_url: str, firmware_version: str) -> bool:
    url = (firmware_url or "").strip()
    version = (firmware_version or "").strip()
    return bool(url) and not url.endswith("/firmware/none.bin") and version not in {"", "0.0.0"}


def firmware_upgrade_url(firmware_url: str, firmware_version: str) -> str:
    url = (firmware_url or "").strip()
    if not can_upgrade(url, firmware_version):
        raise CompanionError("invalid", "No firmware update is published yet.")
    return url


def _int_or_none(value: Any, lo: int, hi: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < lo or parsed > hi:
        return None
    return parsed


def _as_dict(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = orjson.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = orjson.loads(raw[start : end + 1])
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}
