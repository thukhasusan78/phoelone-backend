from __future__ import annotations

import asyncio

import pytest

from app.audio.opus import (
    DOWNLINK_FRAME_SAMPLES,
    ffmpeg_available,
    iter_pcm_frames,
    iter_pcm_frames_from_audio_stream,
    iter_pcm_frames_from_mp3,
    media_to_pcm24k,
)


def test_no_empty_downlink_frames() -> None:
    frames = iter_pcm_frames(b"")
    assert len(frames) == 1
    assert len(frames[0]) == DOWNLINK_FRAME_SAMPLES * 2


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_iter_pcm_frames_from_mp3_yields_60ms_frames() -> None:
    async def run() -> None:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.24",
            "-f",
            "mp3",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        mp3, _ = await proc.communicate()
        assert mp3

        async def chunks():
            mid = max(1, len(mp3) // 3)
            yield mp3[:mid]
            yield mp3[mid:]

        frames = [frame async for frame in iter_pcm_frames_from_mp3(chunks())]
        assert frames
        assert all(len(frame) == DOWNLINK_FRAME_SAMPLES * 2 for frame in frames)
        assert len(frames) >= 3

    asyncio.run(run())


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_media_to_pcm24k_decodes_mp3_bytes() -> None:
    async def run() -> None:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.12",
            "-f",
            "mp3",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        mp3, _ = await proc.communicate()
        assert mp3
        pcm = await media_to_pcm24k(mp3, max_seconds=1.0)
        assert len(pcm) >= DOWNLINK_FRAME_SAMPLES * 2
        assert len(pcm) % 2 == 0

    asyncio.run(run())


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_iter_pcm_frames_from_audio_stream_yields_before_eof() -> None:
    async def run() -> None:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.36",
            "-f",
            "mp3",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        mp3, _ = await proc.communicate()
        assert mp3

        async def chunks():
            step = max(1, len(mp3) // 4)
            for offset in range(0, len(mp3), step):
                yield mp3[offset : offset + step]
                await asyncio.sleep(0)

        frames = [
            frame
            async for frame in iter_pcm_frames_from_audio_stream(
                chunks(),
                timeout_s=8.0,
                first_frame_timeout_s=8.0,
                max_seconds=1.0,
                input_format="mp3",
            )
        ]
        assert frames
        assert all(len(frame) == DOWNLINK_FRAME_SAMPLES * 2 for frame in frames)
        assert len(frames) >= 3

    asyncio.run(run())


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_iter_pcm_frames_from_subprocess_pipes_stdout() -> None:
    async def run() -> None:
        from app.audio.opus import iter_pcm_frames_from_subprocess

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.24",
            "-f",
            "mp3",
            "pipe:1",
        ]
        frames = [
            frame
            async for frame in iter_pcm_frames_from_subprocess(
                cmd, first_frame_timeout_s=8.0, max_seconds=1.0
            )
        ]
        assert frames
        assert all(len(frame) == DOWNLINK_FRAME_SAMPLES * 2 for frame in frames)
        assert len(frames) >= 3

    asyncio.run(run())
