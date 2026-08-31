from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import Settings
from app.mcp.catalog import USER_ONLY_TOOLS, is_forbidden
from app.mcp.client import McpClient, McpError
from app.mcp.tools import (
    enrich_discovered_tools,
    is_user_only,
    to_gemini_declaration,
    validate_arguments,
)
from app.observability.logging import get_logger
from app.observability.metrics import HOST_TOOLS
from app.protocol.models import KNOWN_EMOTIONS
from app.tools.datetime import DateTimeTool
from app.tools.email import EmailTool
from app.tools.knowledge import KnowledgeTool
from app.tools.music import DEVICE_PLAY_TOOLS, MusicTool
from app.tools.news import NewsTool
from app.tools.weather import WeatherTool

log = get_logger(__name__)

EmotionCallback = Callable[[str], Awaitable[None]]
ExitCallback = Callable[[], Awaitable[None]]


HOST_DECLARATIONS = [
    WeatherTool.declaration,
    NewsTool.declaration,
    KnowledgeTool.declaration,
    MusicTool.declaration,
    DateTimeTool.declaration,
    EmailTool.declaration,
    {
        "name": "handle_exit_intent",
        "description": (
            "Call when the user clearly wants to end the conversation (goodbye, bye, etc.). "
            "Provide a short Burmese farewell in say_goodbye."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "One short Burmese farewell sentence.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "set_emotion",
        "description": "Set the robot face emotion for this reply.",
        "parameters": {
            "type": "object",
            "properties": {
                "emotion": {
                    "type": "string",
                    "enum": sorted(KNOWN_EMOTIONS),
                }
            },
            "required": ["emotion"],
        },
    },
]

UNCACHED_HOST_TOOLS = frozenset({"get_datetime", "send_email", "search_weather", "search_music"})
DEVICE_MUSIC_PLAY_TOOLS = frozenset(DEVICE_PLAY_TOOLS)
_TOOL_SPEAK_HINT = (
    "INTERNAL status only. Do not read this JSON, field names, or values aloud. "
    "Reply with one short spoken Burmese sentence."
)


def canonical_tool_name(name: str) -> str:
    """Gemini sometimes drops the self. prefix on device MCP tools."""
    raw = (name or "").strip()
    if not raw:
        return raw
    if raw.startswith("self."):
        return raw
    if raw.startswith(("otto.", "audio_", "screen.", "mickey.", "phoe_lone.")):
        return f"self.{raw}"
    aliases = {
        "otto.action": "self.otto.action",
        "otto.stop": "self.otto.stop",
        "otto.servo_sequences": "self.otto.servo_sequences",
    }
    return aliases.get(raw, raw)


class CircuitBreaker:
    def __init__(self, threshold: int = 5, reset_s: float = 30.0) -> None:
        self.threshold = threshold
        self.reset_s = reset_s
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = time.monotonic() + self.reset_s

    def ok(self) -> None:
        self.failures = 0
        self.open_until = 0.0


class ToolRouter:
    def __init__(
        self,
        settings: Settings,
        weather: WeatherTool,
        news: NewsTool,
        knowledge: KnowledgeTool,
        music: MusicTool,
        redis=None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.host = {
            weather.name: weather,
            news.name: news,
            knowledge.name: knowledge,
            music.name: music,
            DateTimeTool.name: DateTimeTool(settings),
            EmailTool.name: EmailTool(settings),
        }
        self.circuits = {name: CircuitBreaker() for name in self.host}

    def gemini_tools(self, mcp: McpClient) -> list[dict[str, Any]]:
        device = [
            to_gemini_declaration(tool)
            for tool in enrich_discovered_tools(mcp.tools)
            if not is_user_only(tool) and tool["name"] not in DEVICE_MUSIC_PLAY_TOOLS
        ]
        names = {t["name"] for t in device}
        if "self.otto.stop" not in names:
            stop = mcp.tool_by_name.get("self.otto.stop") or {
                "name": "self.otto.stop",
                "description": "Immediately stop motion.",
                "inputSchema": {"type": "object", "properties": {}},
            }
            device.append(to_gemini_declaration(stop))
        return device + HOST_DECLARATIONS

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        mcp: McpClient,
        set_emotion: EmotionCallback,
        *,
        on_exit: ExitCallback | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = canonical_tool_name(name)
        if name == "set_emotion":
            emotion = str(arguments.get("emotion") or "neutral")
            await set_emotion(emotion)
            return {"ok": True, "emotion": emotion}
        if name == "handle_exit_intent":
            if on_exit is not None:
                await on_exit()
            farewell = str(arguments.get("say_goodbye") or "").strip()
            return {"ok": True, "exit": True, "say_goodbye": farewell or None}
        if name in self.host:
            return await self._dispatch_host(name, arguments or {}, context=context)
        if name.startswith("self."):
            if name in USER_ONLY_TOOLS or is_forbidden(name):
                return {"error": f"Unknown tool: {name}"}
            tool = mcp.tool_by_name.get(name)
            if tool is None:
                from app.mcp.catalog import catalog_entry

                tool = catalog_entry(name)
            if tool is None:
                return {"error": f"Unknown tool: {name}"}
            try:
                args = validate_arguments(tool, arguments)
                text = await mcp.call(name, args)
                return {"result": text}
            except (McpError, ValueError) as exc:
                return {"error": str(exc)}
        return {"error": f"Unknown tool: {name}"}

    async def _dispatch_host(
        self,
        name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        circuit = self.circuits[name]
        if not circuit.allow():
            HOST_TOOLS.labels(name=name, result="circuit_open").inc()
            return {"error": "temporarily unavailable"}
        args = dict(arguments)
        if name == "search_weather" and context:
            for key in (
                "client_ip",
                "user_text",
                "device_city",
                "device_latitude",
                "device_longitude",
            ):
                if key in context and context[key] is not None:
                    args.setdefault(key, context[key])
        cache_key = f"tool:{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if self.redis is not None and name not in UNCACHED_HOST_TOOLS:
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    HOST_TOOLS.labels(name=name, result="cache").inc()
                    return json.loads(cached)
            except Exception:  # noqa: BLE001
                pass
        try:
            result = await self.host[name](**args)
            payload = result if isinstance(result, dict) else {"result": result}
            circuit.ok()
            HOST_TOOLS.labels(name=name, result="ok").inc()
            if self.redis is not None and name not in UNCACHED_HOST_TOOLS:
                try:
                    await self.redis.setex(
                        cache_key,
                        self.settings.redis_cache_ttl_s,
                        json.dumps(payload, ensure_ascii=False),
                    )
                except Exception:  # noqa: BLE001
                    pass
            return payload
        except Exception as exc:  # noqa: BLE001
            circuit.fail()
            HOST_TOOLS.labels(name=name, result="error").inc()
            log.warning("host_tool.failed", name=name, error=str(exc))
            return {"error": str(exc)}

    @staticmethod
    def as_function_response(result: dict[str, Any]) -> dict[str, Any]:
        """Compact internal status. Prompt + TTS sanitizer must still block JSON speech."""
        payload = dict(result)
        raw = payload.get("result")
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    payload.pop("result", None)
                    if "ok" in parsed:
                        payload["ok"] = parsed.get("ok")
                    if "action" in parsed:
                        payload["action"] = parsed.get("action")
                    if "error" in parsed:
                        payload["error"] = parsed.get("error")
        payload["note"] = _TOOL_SPEAK_HINT
        return {"result": json.dumps(payload, ensure_ascii=False)}
