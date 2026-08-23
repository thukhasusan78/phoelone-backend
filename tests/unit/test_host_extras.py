from __future__ import annotations

from app.config import Settings
from app.protocol.messages import alert, llm_emotion, system
from app.protocol.models import KNOWN_EMOTIONS
from app.tools.datetime import DateTimeTool
from app.tools.email import EmailTool


async def test_get_datetime_uses_offset() -> None:
    tool = DateTimeTool(Settings(database_url="memory://", timezone_offset_minutes=390))
    result = await tool()
    assert result["timezone_offset_minutes"] == 390
    assert result["iso"].endswith("+06:30")
    assert result["weekday"]
    assert result["date"]
    assert result["time"]


async def test_send_email_disabled_without_smtp() -> None:
    tool = EmailTool(Settings(database_url="memory://"))
    result = await tool(to="a@example.com", subject="Hi", body="Hello")
    assert result["configured"] is False
    assert "not configured" in result["error"]


def test_system_and_alert_json() -> None:
    assert '"type":"system"' in system("s", "reboot")
    assert '"command":"reboot"' in system("s", "reboot")
    body = alert("s", "Warning", "Speech playback failed", "sad")
    assert '"type":"alert"' in body
    assert '"emotion":"sad"' in body


def test_expanded_emotions() -> None:
    assert "robot_2" in KNOWN_EMOTIONS
    assert "thinking" in KNOWN_EMOTIONS
    assert '"emotion":"thinking"' in llm_emotion("s", "thinking")
    assert '"emotion":"neutral"' in llm_emotion("s", "not-a-face")
