from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

SESSIONS_ACTIVE = Gauge("phoe_lone_sessions_active", "Active WebSocket sessions")
OTA_REQUESTS = Counter("phoe_lone_ota_requests_total", "OTA requests", ["method", "result"])
WS_CONNECTIONS = Counter("phoe_lone_ws_connections_total", "WebSocket attempts", ["result"])
TURNS = Counter("phoe_lone_turns_total", "Completed voice turns", ["result"])
TURN_LATENCY = Histogram(
    "phoe_lone_turn_latency_seconds",
    "Voice turn latency",
    buckets=(0.5, 1, 2, 4, 8, 16, 32, 64),
)
STAGE_LATENCY = Histogram(
    "phoe_lone_stage_latency_seconds",
    "Pipeline stage latency",
    ["stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
MCP_CALLS = Counter("phoe_lone_mcp_calls_total", "Device MCP calls", ["result"])
HOST_TOOLS = Counter("phoe_lone_host_tools_total", "Host tool calls", ["name", "result"])
FRAMES_DROPPED = Counter("phoe_lone_frames_dropped_total", "Dropped audio frames", ["direction"])
AUDIO_GATE = Counter(
    "phoe_lone_audio_gate_chunks_total",
    "Uplink PCM chunks accepted or dropped by the RMS speech gate",
    ["result"],
)
QUEUE_DEPTH = Gauge("phoe_lone_queue_depth", "Session queue depth", ["queue"])
UPSTREAM_ERRORS = Counter("phoe_lone_upstream_errors_total", "Upstream failures", ["service"])


def metrics_payload() -> bytes:
    return generate_latest()
