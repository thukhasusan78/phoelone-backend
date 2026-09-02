from __future__ import annotations

import re

from app.ai.prompts import SYSTEM_PROMPT
from app.ai.tool_router import HOST_DECLARATIONS, ToolRouter
from app.config import Settings
from app.mcp.catalog import (
    LLM_TOOLS,
    MICKEY_SENSOR_TOOLS,
    PHOE_LONE_FALLBACK_NAMES,
    PHOE_LONE_SENSOR_TOOLS,
    USER_ONLY_TOOLS,
)
from app.mcp.client import McpClient
from app.mcp.tools import enrich_discovered_tools
from app.tools.knowledge import KnowledgeTool
from app.tools.music import MusicTool
from app.tools.news import NewsTool
from app.tools.weather import WeatherTool

MYANMAR = re.compile(r"[\u1000-\u109F]")

REQUIRED_DEVICE_TOOLS = {
    "self.get_device_status",
    "self.audio_speaker.set_volume",
    "self.screen.set_brightness",
    "self.screen.set_theme",
    "self.otto.action",
    "self.otto.stop",
    "self.otto.servo_sequences",
    "self.otto.set_trim",
    "self.otto.get_trims",
    "self.otto.get_status",
    "self.battery.get_level",
    "self.otto.get_ip",
    "self.mickey.alarm.set",
    "self.mickey.alarm.get",
    "self.mickey.alarm.cancel",
    "self.mickey.sleep.now",
}

SENSOR_DEVICE_TOOLS = set(PHOE_LONE_SENSOR_TOOLS) | set(MICKEY_SENSOR_TOOLS)


def test_system_prompt_is_english() -> None:
    assert "You are Mickey" in SYSTEM_PROMPT
    assert "Always answer in natural, concise Burmese" in SYSTEM_PROMPT
    assert "MUST reply primarily in standard Burmese Unicode" in SYSTEM_PROMPT
    assert "empty string" in SYSTEM_PROMPT
    assert "<ctrl46>" in SYSTEM_PROMPT
    spoken_name = "Your spoken name is Mickey."
    assert spoken_name in SYSTEM_PROMPT
    assert "Never say you are Phoe Lone" in SYSTEM_PROMPT
    assert "petting your head" in SYSTEM_PROMPT
    assert MYANMAR.search(SYSTEM_PROMPT) is None
    for name in (
        "self.otto.action",
        "self.otto.stop",
        "self.mickey.alarm.set",
        "self.mickey.alarm.get",
        "self.mickey.alarm.cancel",
        "self.mickey.sleep.now",
        "search_weather",
        "search_news",
        "search_music",
        "get_datetime",
        "send_email",
        "handle_exit_intent",
    ):
        assert name in SYSTEM_PROMPT


def test_catalog_covers_spec_tools() -> None:
    assert REQUIRED_DEVICE_TOOLS <= set(LLM_TOOLS)
    assert "self.set_press_to_talk" not in LLM_TOOLS
    assert SENSOR_DEVICE_TOOLS <= set(LLM_TOOLS)
    assert REQUIRED_DEVICE_TOOLS <= set(PHOE_LONE_FALLBACK_NAMES)
    assert SENSOR_DEVICE_TOOLS.isdisjoint(PHOE_LONE_FALLBACK_NAMES)
    assert "walk" in (LLM_TOOLS["self.otto.action"]["inputSchema"]["properties"]["action"]["enum"])
    assert "self.reboot" in USER_ONLY_TOOLS
    assert "self.camera.take_photo" not in PHOE_LONE_FALLBACK_NAMES


def test_enrich_replaces_non_english_description() -> None:
    discovered = [
        {
            "name": "self.otto.action",
            "description": "错误：动作",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "self.reboot",
            "description": "reboot",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"audience": ["user"]},
        },
    ]
    tools = enrich_discovered_tools(discovered)
    names = {t["name"] for t in tools}
    assert "self.otto.action" in names
    assert "self.otto.stop" in names
    assert "self.reboot" not in names
    action = next(t for t in tools if t["name"] == "self.otto.action")
    assert MYANMAR.search(action["description"]) is None
    assert "错误" not in action["description"]
    assert "action" in action["inputSchema"]["properties"]


def test_empty_discovery_uses_core_catalog_without_sensors() -> None:
    tools = enrich_discovered_tools([])
    names = {t["name"] for t in tools}
    assert REQUIRED_DEVICE_TOOLS <= names
    assert SENSOR_DEVICE_TOOLS.isdisjoint(names)
    for tool in tools:
        assert MYANMAR.search(tool["description"]) is None


def test_discovered_sensor_tools_are_enriched() -> None:
    tools = enrich_discovered_tools(
        [
            {
                "name": "self.phoe_lone.imu.get_reading",
                "description": "错误",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
    )
    names = {t["name"] for t in tools}
    assert "self.phoe_lone.imu.get_reading" in names
    assert "self.phoe_lone.light.get_level" not in names
    imu = next(t for t in tools if t["name"] == "self.phoe_lone.imu.get_reading")
    assert "wired:true" in imu["description"]
    assert "错误" not in imu["description"]


def test_dual_fleet_sensor_tools_prefer_mickey_names() -> None:
    tools = enrich_discovered_tools(
        [
            {
                "name": "self.phoe_lone.imu.get_reading",
                "description": "imu",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "self.mickey.imu.get_reading",
                "description": "imu",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "self.phoe_lone.touch.get_state",
                "description": "touch",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "self.mickey.touch.get_state",
                "description": "touch",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "self.phoe_lone.light.get_level",
                "description": "light",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "self.mickey.light.get_level",
                "description": "light",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
    )
    names = {t["name"] for t in tools}
    assert "self.mickey.imu.get_reading" in names
    assert "self.mickey.touch.get_state" in names
    assert "self.mickey.light.get_level" in names
    assert "self.phoe_lone.imu.get_reading" not in names
    assert "self.phoe_lone.touch.get_state" not in names
    assert "self.phoe_lone.light.get_level" not in names
    imu = next(t for t in tools if t["name"] == "self.mickey.imu.get_reading")
    assert "wired:true" in imu["description"]
    light = next(t for t in tools if t["name"] == "self.mickey.light.get_level")
    assert "wired:false" in light["description"]


def test_router_exposes_host_and_device_tools() -> None:
    settings = Settings(database_url="memory://")
    http = object()
    router = ToolRouter(
        settings,
        WeatherTool(http),  # type: ignore[arg-type]
        NewsTool(http, settings),  # type: ignore[arg-type]
        KnowledgeTool(http, settings),  # type: ignore[arg-type]
        MusicTool(http),  # type: ignore[arg-type]
    )

    async def send(_):
        return None

    mcp = McpClient("s", send)
    mcp.apply_english_catalog()
    decls = router.gemini_tools(mcp)
    names = {d["name"] for d in decls}
    assert REQUIRED_DEVICE_TOOLS <= names
    assert SENSOR_DEVICE_TOOLS.isdisjoint(names)
    assert {d["name"] for d in HOST_DECLARATIONS} <= names
    assert "search_weather" in names
    assert "get_datetime" in names
    assert "send_email" in names
    assert "handle_exit_intent" in names
    assert "self.reboot" not in names
    assert "self.music.play_song" not in names
    assert "self.online_music.play_music" not in names
    for decl in decls:
        assert MYANMAR.search(decl["description"]) is None


def test_sensor_catalog_is_dual_fleet() -> None:
    imu = LLM_TOOLS["self.mickey.imu.get_reading"]["description"]
    light = LLM_TOOLS["self.mickey.light.get_level"]["description"]
    touch = LLM_TOOLS["self.mickey.touch.get_state"]["description"]
    for name in MICKEY_SENSOR_TOOLS + PHOE_LONE_SENSOR_TOOLS:
        assert name in LLM_TOOLS
    assert "wired:true" in imu
    assert "ok:false" in imu
    assert "wired:true" in touch
    assert "wired:false" in light
    assert "never invent" in light.lower()
    assert "lux" in light
    assert "notification" in touch
    assert "self.otto.stop" in imu
    assert "wired:true" in SYSTEM_PROMPT
    assert "wired:false" in SYSTEM_PROMPT
    assert "unwired stubs" not in SYSTEM_PROMPT
    assert "ok:false" in SYSTEM_PROMPT
    assert "fall" in SYSTEM_PROMPT.lower()
    assert "do not walk" in SYSTEM_PROMPT.lower()
    assert "self.mickey." in SYSTEM_PROMPT
    assert "self.phoe_lone." in SYSTEM_PROMPT
    assert "light is not connected" in SYSTEM_PROMPT.lower()
    assert LLM_TOOLS["self.phoe_lone.imu.get_reading"]["description"] != imu
    assert "wired:true" in LLM_TOOLS["self.phoe_lone.imu.get_reading"]["description"]
    assert "wired:false" in LLM_TOOLS["self.phoe_lone.light.get_level"]["description"]


def test_mickey_alarm_catalog_and_prompt() -> None:
    setter = LLM_TOOLS["self.mickey.alarm.set"]
    props = setter["inputSchema"]["properties"]
    assert setter["inputSchema"]["required"] == ["hour", "minute"]
    assert {"hour", "minute", "repeat", "sleep_now"} <= set(props)
    assert "firmware default" in props["repeat"]["description"].lower()
    assert "7:00 AM" in setter["description"] or "hour=7" in SYSTEM_PROMPT
    sleep = LLM_TOOLS["self.mickey.sleep.now"]
    sleep_props = sleep["inputSchema"]["properties"]
    assert {"hour", "minute", "seconds"} <= set(sleep_props)
    assert "no wake time" in sleep["description"].lower()
    assert "self.mickey.sleep.now" in SYSTEM_PROMPT
    assert "handle_exit_intent" in SYSTEM_PROMPT
    assert "good night" in SYSTEM_PROMPT.lower()
    assert "repeat=false" in SYSTEM_PROMPT
    assert "no alarm is stored" in SYSTEM_PROMPT.lower() or "already stored" in SYSTEM_PROMPT.lower()
    tools = enrich_discovered_tools(
        [
            {
                "name": "self.otto.stop",
                "description": "stop",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
    )
    names = {t["name"] for t in tools}
    assert {
        "self.mickey.alarm.set",
        "self.mickey.alarm.get",
        "self.mickey.alarm.cancel",
        "self.mickey.sleep.now",
    } <= names


def test_discovered_device_music_tools_are_hidden_from_gemini() -> None:
    settings = Settings(database_url="memory://")
    http = object()
    router = ToolRouter(
        settings,
        WeatherTool(http),  # type: ignore[arg-type]
        NewsTool(http, settings),  # type: ignore[arg-type]
        KnowledgeTool(http, settings),  # type: ignore[arg-type]
        MusicTool(http),  # type: ignore[arg-type]
    )

    async def send(_):
        return None

    mcp = McpClient("s", send)
    mcp.tools = [
        {
            "name": "self.music.play_song",
            "description": "play",
            "inputSchema": {"type": "object", "properties": {"song_name": {"type": "string"}}},
        },
        {
            "name": "self.get_device_status",
            "description": "status",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    mcp.tool_by_name = {t["name"]: t for t in mcp.tools}
    names = {d["name"] for d in router.gemini_tools(mcp)}
    assert "search_music" in names
    assert "self.get_device_status" in names
    assert "self.music.play_song" not in names

