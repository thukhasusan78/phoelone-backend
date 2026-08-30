from __future__ import annotations

from typing import Any

from app.mcp.catalog import (
    LLM_TOOLS,
    MICKEY_DEVICE_TOOLS,
    PHOE_LONE_FALLBACK_NAMES,
    USER_ONLY_TOOLS,
    catalog_entry,
    is_forbidden,
)

MOTION_TOOLS = frozenset(
    {
        "self.otto.action",
        "self.otto.servo_sequences",
        "self.otto.set_trim",
    }
)


def validate_arguments(tool: dict[str, Any], arguments: dict[str, Any] | None) -> dict[str, Any]:
    schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    args = dict(arguments or {})
    for key in required:
        if key not in args:
            raise ValueError(f"Missing valid argument: {key}")
    extra = [k for k in args if k not in properties and properties]
    if extra and properties:
        for key in extra:
            args.pop(key, None)
    return args


def to_gemini_declaration(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    return {
        "name": tool["name"],
        "description": tool.get("description") or tool["name"],
        "parameters": _sanitize_schema(schema),
    }


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    allowed = {"type", "properties", "required", "description", "enum", "items"}
    cleaned = {k: v for k, v in schema.items() if k in allowed}
    cleaned.setdefault("type", "object")
    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            name: _sanitize_schema(prop) if isinstance(prop, dict) else {"type": "string"}
            for name, prop in cleaned["properties"].items()
        }
    return cleaned


def is_user_only(tool: dict[str, Any]) -> bool:
    name = str(tool.get("name") or "")
    if name in USER_ONLY_TOOLS or is_forbidden(name):
        return True
    annotations = tool.get("annotations") or {}
    audience = annotations.get("audience") or []
    return audience == ["user"]


def _merge_catalog(tool: dict[str, Any]) -> dict[str, Any]:
    name = tool["name"]
    catalog = catalog_entry(name)
    if not catalog:
        return {
            "name": name,
            "description": tool.get("description") or name,
            "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
        }
    return {
        "name": name,
        "description": catalog["description"],
        "inputSchema": catalog.get("inputSchema")
        or tool.get("inputSchema")
        or {"type": "object", "properties": {}},
    }


def enrich_discovered_tools(discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """English-enrich LLM-visible device tools. Fall back to the Phoe Lone catalog if discovery is empty."""
    visible = [t for t in discovered if t.get("name") and not is_user_only(t)]
    by_name = {t["name"]: t for t in visible}
    if not by_name:
        return [_merge_catalog({"name": name, **LLM_TOOLS[name]}) for name in PHOE_LONE_FALLBACK_NAMES]

    enriched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in visible:
        name = tool["name"]
        if name in seen:
            continue
        seen.add(name)
        enriched.append(_merge_catalog(tool))

    has_otto = any(name.startswith("self.otto.") for name in seen)
    if has_otto and "self.otto.stop" not in seen:
        stop = by_name.get("self.otto.stop") or LLM_TOOLS["self.otto.stop"]
        enriched.append(_merge_catalog(stop))
        seen.add("self.otto.stop")
    for name in MICKEY_DEVICE_TOOLS:
        if name not in seen and name in LLM_TOOLS:
            enriched.append(_merge_catalog({"name": name, **LLM_TOOLS[name]}))
            seen.add(name)
    return enriched
