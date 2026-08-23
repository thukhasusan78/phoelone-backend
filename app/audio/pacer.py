from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from app.audio.opus import FRAME_MS

PRE_BUFFER_COUNT = 5
FRAME_DURATION_S = FRAME_MS / 1000.0


async def pace_opus_frames(
    packets: Sequence[bytes],
    send: Callable[[bytes], Awaitable[None]],
    *,
    should_continue: Callable[[], bool],
    pre_buffer: int = PRE_BUFFER_COUNT,
    frame_s: float = FRAME_DURATION_S,
) -> None:
    """Send Opus frames without overflowing the ESP32 1.2s decode queue.

    The first ``pre_buffer`` packets go out immediately so playback can start.
    Remaining packets are released at one frame per ``frame_s`` (60 ms).
    """
    deadline: float | None = None
    sent = 0
    for packet in packets:
        if not should_continue():
            return
        await send(packet)
        sent += 1
        if sent < pre_buffer:
            continue
        now = time.monotonic()
        if deadline is None:
            deadline = now
        deadline += frame_s
        delay = deadline - now
        if delay > 0:
            await asyncio.sleep(delay)


async def pace_opus_stream(
    packets: AsyncIterator[bytes],
    send: Callable[[bytes], Awaitable[None]],
    *,
    should_continue: Callable[[], bool],
    pre_buffer: int = PRE_BUFFER_COUNT,
    frame_s: float = FRAME_DURATION_S,
) -> None:
    """Pace an async stream of Opus frames (same pre-buffer + 60 ms cadence)."""
    deadline: float | None = None
    sent = 0
    async for packet in packets:
        if not should_continue():
            return
        await send(packet)
        sent += 1
        if sent < pre_buffer:
            continue
        now = time.monotonic()
        if deadline is None:
            deadline = now
        deadline += frame_s
        delay = deadline - now
        if delay > 0:
            await asyncio.sleep(delay)
