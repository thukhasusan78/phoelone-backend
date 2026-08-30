from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.observability.logging import get_logger
from app.observability.metrics import MCP_CALLS
from app.protocol.messages import mcp

log = get_logger(__name__)


class McpError(Exception):
    pass


class McpClient:
    def __init__(
        self,
        session_id: str,
        send_json,
        timeout_s: float = 8.0,
    ) -> None:
        self.session_id = session_id
        self._send_json = send_json
        self.timeout_s = timeout_s
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self.tools: list[dict[str, Any]] = []
        self.tool_by_name: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._motion_lock = asyncio.Lock()
        self._notification_handler: Callable[[dict[str, Any]], None] | None = None

    def set_notification_handler(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        self._notification_handler = handler

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            request_id = self._allocate_id()
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending[request_id] = future
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id,
        }
        if params is not None:
            payload["params"] = params
        await self._send_json(mcp(self.session_id, payload))
        wait_s = self.timeout_s if timeout is None else timeout
        try:
            result = await asyncio.wait_for(future, timeout=wait_s)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            MCP_CALLS.labels(result="timeout").inc()
            self._pending.pop(request_id, None)
            raise McpError(f"MCP timeout calling {method}") from exc
        if "error" in result:
            MCP_CALLS.labels(result="error").inc()
            message = result["error"].get("message", "MCP error") if isinstance(result["error"], dict) else "MCP error"
            raise McpError(str(message))
        MCP_CALLS.labels(result="ok").inc()
        return result.get("result") or {}

    def on_message(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        if isinstance(method, str) and method.startswith("notifications"):
            handler = self._notification_handler
            if handler is not None:
                try:
                    handler(payload)
                except Exception as exc:  # noqa: BLE001
                    log.warning("mcp.notification_failed", error=str(exc), method=method)
            return
        request_id = payload.get("id")
        if not isinstance(request_id, int):
            return
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(payload)

    async def initialize(
        self,
        vision_url: str | None = None,
        vision_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"capabilities": {}}
        if vision_url:
            vision: dict[str, Any] = {"url": vision_url}
            if vision_token:
                vision["token"] = vision_token
            params["capabilities"]["vision"] = vision
        return await self.send_request("initialize", params)

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = ""
        while True:
            result = await self.send_request(
                "tools/list",
                {"cursor": cursor, "withUserTools": False},
            )
            batch = result.get("tools") or []
            tools.extend(batch)
            next_cursor = result.get("nextCursor") or ""
            if not next_cursor:
                break
            cursor = next_cursor
        self.tools = tools
        self.tool_by_name = {t["name"]: t for t in tools if "name" in t}
        self.apply_english_catalog()
        log.info("mcp.tools_discovered", count=len(self.tools), names=list(self.tool_by_name))
        return tools

    def apply_english_catalog(self) -> None:
        from app.mcp.tools import enrich_discovered_tools

        enriched = enrich_discovered_tools(self.tools)
        self.tools = enriched
        self.tool_by_name = {t["name"]: t for t in enriched}

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if name not in self.tool_by_name and not name.startswith("self."):
            raise McpError(f"Unknown tool: {name}")
        args = arguments or {}
        is_motion = name.startswith("self.otto.") and name != "self.otto.stop"
        timeout = self.timeout_s
        if is_motion:
            timeout = max(self.timeout_s, 20.0)
        # Do not wrap _call in self._lock: send_request already takes that lock
        # for id allocation, and asyncio.Lock is not reentrant (tool turns hung).
        if is_motion or name == "self.otto.stop":
            async with self._motion_lock:
                return await self._call(name, args, timeout=timeout)
        return await self._call(name, args, timeout=timeout)

    async def _call(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> str:
        result = await self.send_request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        if result.get("isError"):
            text = _content_text(result)
            raise McpError(text or "tool error")
        return _content_text(result)

    def cancel_pending(self) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()


def _content_text(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts).strip()
