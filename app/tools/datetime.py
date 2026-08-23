from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings


class DateTimeTool:
    name = "get_datetime"
    declaration = {
        "name": "get_datetime",
        "description": (
            "Return the current local date and time for the robot's timezone. "
            "Use for 'what time is it' or 'what day is it'."
        ),
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(self, **_: Any) -> dict[str, Any]:
        offset = timedelta(minutes=int(self.settings.timezone_offset_minutes))
        now = datetime.now(timezone(offset))
        return {
            "iso": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "weekday": now.strftime("%A"),
            "timezone_offset_minutes": int(self.settings.timezone_offset_minutes),
        }
