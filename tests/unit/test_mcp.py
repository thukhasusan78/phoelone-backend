from __future__ import annotations

import pytest

from app.mcp.client import McpClient, McpError
from app.mcp.tools import to_gemini_declaration, validate_arguments


async def test_validate_required() -> None:
    tool = {
        "name": "self.audio_speaker.set_volume",
        "inputSchema": {
            "type": "object",
            "properties": {"volume": {"type": "integer"}},
            "required": ["volume"],
        },
    }
    with pytest.raises(ValueError):
        validate_arguments(tool, {})
    assert validate_arguments(tool, {"volume": 70, "extra": 1}) == {"volume": 70}


async def test_gemini_declaration() -> None:
    tool = {
        "name": "self.otto.stop",
        "description": "Stop",
        "inputSchema": {"type": "object", "properties": {}},
    }
    decl = to_gemini_declaration(tool)
    assert decl["name"] == "self.otto.stop"


async def test_mcp_correlation() -> None:
    sent: list[str] = []

    async def send(payload: str) -> None:
        sent.append(payload)

    client = McpClient("sid", send, timeout_s=0.2)
    task_result = {}

    async def call() -> None:
        task_result["r"] = await client.send_request("initialize", {"capabilities": {}})

    import asyncio

    task = asyncio.create_task(call())
    await asyncio.sleep(0.05)
    assert sent
    client.on_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    await task
    assert task_result["r"]["ok"] is True


async def test_mcp_timeout() -> None:
    async def send(_payload: str) -> None:
        return None

    client = McpClient("sid", send, timeout_s=0.05)
    with pytest.raises(McpError):
        await client.send_request("tools/list")


async def test_mcp_call_does_not_deadlock_on_lock() -> None:
    import asyncio

    import orjson

    holder: dict[str, McpClient] = {}

    async def send(payload: str) -> None:
        data = orjson.loads(payload)
        rpc = data["payload"]
        holder["client"].on_message(
            {
                "jsonrpc": "2.0",
                "id": rpc["id"],
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        )

    client = McpClient("sid", send, timeout_s=1)
    client.tool_by_name["self.audio_speaker.set_volume"] = {
        "name": "self.audio_speaker.set_volume"
    }
    holder["client"] = client
    text = await asyncio.wait_for(
        client.call("self.audio_speaker.set_volume", {"volume": 80}),
        timeout=1,
    )
    assert text == "ok"


async def test_mcp_notification_invokes_handler() -> None:
    seen: list[dict] = []

    async def send(_payload: str) -> None:
        return None

    client = McpClient("sid", send)
    client.set_notification_handler(seen.append)
    client.on_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/phoe_lone.event",
            "params": {"event": "pet", "ts_ms": 1},
        }
    )
    assert len(seen) == 1
    assert seen[0]["params"]["event"] == "pet"


async def test_mcp_notification_does_not_break_correlation() -> None:
    sent: list[str] = []

    async def send(payload: str) -> None:
        sent.append(payload)

    client = McpClient("sid", send, timeout_s=0.2)
    client.set_notification_handler(lambda _p: None)
    task_result = {}

    async def call() -> None:
        task_result["r"] = await client.send_request("initialize", {"capabilities": {}})

    import asyncio

    task = asyncio.create_task(call())
    await asyncio.sleep(0.05)
    client.on_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/phoe_lone.event",
            "params": {"event": "bright"},
        }
    )
    client.on_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    await task
    assert task_result["r"]["ok"] is True


async def test_mcp_notification_handler_exception_is_swallowed() -> None:
    async def send(_payload: str) -> None:
        return None

    def boom(_payload: dict) -> None:
        raise RuntimeError("handler exploded")

    client = McpClient("sid", send)
    client.set_notification_handler(boom)
    client.on_message({"jsonrpc": "2.0", "method": "notifications/other", "params": {}})
