from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.audio.pacer import pace_opus_frames, pace_opus_stream


def test_prebuffer_sends_without_sleep() -> None:
    sent: list[bytes] = []

    async def send(packet: bytes) -> None:
        sent.append(packet)

    async def run() -> None:
        with patch("app.audio.pacer.asyncio.sleep", new=AsyncMock()) as slept:
            await pace_opus_frames(
                [b"a", b"b", b"c"],
                send,
                should_continue=lambda: True,
                pre_buffer=5,
            )
            slept.assert_not_called()

    asyncio.run(run())
    assert sent == [b"a", b"b", b"c"]


def test_paces_after_prebuffer() -> None:
    sent: list[bytes] = []

    async def send(packet: bytes) -> None:
        sent.append(packet)

    async def run() -> None:
        with patch("app.audio.pacer.asyncio.sleep", new=AsyncMock()) as slept:
            await pace_opus_frames(
                [b"1", b"2", b"3", b"4"],
                send,
                should_continue=lambda: True,
                pre_buffer=2,
                frame_s=0.06,
            )
            assert slept.await_count == 3  # frames 2,3,4 after pre-buffer of 2

    asyncio.run(run())
    assert sent == [b"1", b"2", b"3", b"4"]


def test_stops_when_should_continue_false() -> None:
    sent: list[bytes] = []

    async def send(packet: bytes) -> None:
        sent.append(packet)

    async def run() -> None:
        await pace_opus_frames(
            [b"1", b"2", b"3"],
            send,
            should_continue=lambda: len(sent) < 1,
            pre_buffer=5,
        )

    asyncio.run(run())
    assert sent == [b"1"]


def test_stream_prebuffer_sends_without_sleep() -> None:
    sent: list[bytes] = []

    async def send(packet: bytes) -> None:
        sent.append(packet)

    async def packets():
        for packet in (b"a", b"b", b"c"):
            yield packet

    async def run() -> None:
        with patch("app.audio.pacer.asyncio.sleep", new=AsyncMock()) as slept:
            await pace_opus_stream(
                packets(),
                send,
                should_continue=lambda: True,
                pre_buffer=5,
            )
            slept.assert_not_called()

    asyncio.run(run())
    assert sent == [b"a", b"b", b"c"]


def test_stream_paces_after_prebuffer() -> None:
    sent: list[bytes] = []

    async def send(packet: bytes) -> None:
        sent.append(packet)

    async def packets():
        for packet in (b"1", b"2", b"3", b"4"):
            yield packet

    async def run() -> None:
        with patch("app.audio.pacer.asyncio.sleep", new=AsyncMock()) as slept:
            await pace_opus_stream(
                packets(),
                send,
                should_continue=lambda: True,
                pre_buffer=2,
                frame_s=0.06,
            )
            assert slept.await_count == 3

    asyncio.run(run())
    assert sent == [b"1", b"2", b"3", b"4"]
