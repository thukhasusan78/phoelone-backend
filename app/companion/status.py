from __future__ import annotations

from typing import Any

import orjson


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
