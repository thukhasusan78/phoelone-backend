from __future__ import annotations

import logging
import re
from typing import Any

import structlog

_REDACT_KEYS = {
    "authorization",
    "token",
    "websocket_token",
    "api_key",
    "gemini_api_keys",
    "auth_pepper",
    "password",
    "tavily_key",
    "openweather_api_key",
    "metrics_token",
    "audio",
    "pcm",
    "opus",
    "transcript",
    "text",
}

_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in _REDACT_KEYS:
        return "[redacted]"
    if isinstance(value, str):
        return _BEARER_RE.sub("Bearer [redacted]", value)
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    return value


def _redact_event(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: _redact_value(k, v) for k, v in event_dict.items()}


def configure_logging(level: str = "info", json_logs: bool = True) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_event,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
