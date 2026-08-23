from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from app.observability.logging import get_logger

log = get_logger(__name__)

UPLINK_RATE = 16000
DOWNLINK_RATE = 24000
FRAME_MS = 60
UPLINK_FRAME_SAMPLES = UPLINK_RATE * FRAME_MS // 1000  # 960
DOWNLINK_FRAME_SAMPLES = DOWNLINK_RATE * FRAME_MS // 1000  # 1440


class CodecError(Exception):
    pass


class OpusCodec(Protocol):
    def decode_uplink(self, packet: bytes) -> bytes: ...

    def encode_downlink(self, pcm: bytes) -> bytes: ...

    def reset(self) -> None: ...


class LibOpusCodec:
    def __init__(self) -> None:
        try:
            import opuslib_next as opuslib
        except ImportError as exc:  # pragma: no cover
            raise CodecError("opuslib is not installed") from exc
        self._opuslib = opuslib
        self._decoder = opuslib.Decoder(UPLINK_RATE, 1)
        self._encoder = opuslib.Encoder(DOWNLINK_RATE, 1, opuslib.APPLICATION_AUDIO)

    def decode_uplink(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        try:
            return self._decoder.decode(packet, UPLINK_FRAME_SAMPLES)
        except Exception as exc:
            raise CodecError(f"opus decode failed: {exc}") from exc

    def encode_downlink(self, pcm: bytes) -> bytes:
        if len(pcm) < DOWNLINK_FRAME_SAMPLES * 2:
            pcm = pcm.ljust(DOWNLINK_FRAME_SAMPLES * 2, b"\x00")
        try:
            return self._encoder.encode(pcm[: DOWNLINK_FRAME_SAMPLES * 2], DOWNLINK_FRAME_SAMPLES)
        except Exception as exc:
            raise CodecError(f"opus encode failed: {exc}") from exc

    def reset(self) -> None:
        import opuslib_next as opuslib

        self._decoder = opuslib.Decoder(UPLINK_RATE, 1)
        self._encoder = opuslib.Encoder(DOWNLINK_RATE, 1, opuslib.APPLICATION_AUDIO)


def create_codec() -> OpusCodec:
    try:
        return LibOpusCodec()
    except (CodecError, Exception):  # pragma: no cover
        log.warning("codec.libopus_unavailable")
        return NullCodec()


class NullCodec:
    """Used in tests or when libopus is not installed. Not for production audio."""

    def decode_uplink(self, packet: bytes) -> bytes:
        return b"\x00" * (UPLINK_FRAME_SAMPLES * 2) if packet else b""

    def encode_downlink(self, pcm: bytes) -> bytes:
        return b"\x00\x01"

    def reset(self) -> None:
        return None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def mp3_to_pcm24k(mp3: bytes, timeout_s: float = 45.0) -> bytes:
    return await media_to_pcm24k(mp3, timeout_s=timeout_s)


async def media_to_pcm24k(
    media: bytes,
    timeout_s: float = 45.0,
    *,
    max_seconds: float | None = None,
) -> bytes:
    """Decode mp3/aac/m4a/wav bytes to 24 kHz mono PCM16."""
    if not media:
        return b""
    if not ffmpeg_available():
        raise CodecError("ffmpeg is required to decode TTS audio")
    import tempfile

    # m4a/AAC often cannot be probed from a non-seekable pipe; use a temp file.
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=True) as tmp:
        tmp.write(media)
        tmp.flush()
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            tmp.name,
        ]
        if max_seconds is not None and max_seconds > 0:
            cmd.extend(["-t", f"{max_seconds:.3f}"])
        cmd.extend(
            [
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(DOWNLINK_RATE),
                "pipe:1",
            ]
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    if proc.returncode != 0:
        raise CodecError(f"ffmpeg failed: {stderr.decode('utf-8', errors='replace')}")
    return stdout


async def iter_pcm_frames_from_mp3(
    mp3_chunks: AsyncIterator[bytes],
    timeout_s: float = 45.0,
) -> AsyncIterator[bytes]:
    """Decode a streaming MP3 into 60 ms PCM frames as soon as ffmpeg yields samples.

    Do not pass ``-fflags nobuffer`` — on ffmpeg 4.x that flag can emit an empty
    PCM stream with a successful exit code.
    """
    if not ffmpeg_available():
        raise CodecError("ffmpeg is required to decode TTS audio")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-flags",
        "low_delay",
        "-f",
        "mp3",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(DOWNLINK_RATE),
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None

    async def _feed() -> None:
        assert proc.stdin is not None
        try:
            async for chunk in mp3_chunks:
                if not chunk:
                    continue
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        finally:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    feeder = asyncio.create_task(_feed())
    buf = bytearray()
    frame_bytes = DOWNLINK_FRAME_SAMPLES * 2
    try:
        while True:
            data = await asyncio.wait_for(proc.stdout.read(frame_bytes), timeout=timeout_s)
            if not data:
                break
            buf.extend(data)
            while len(buf) >= frame_bytes:
                yield bytes(buf[:frame_bytes])
                del buf[:frame_bytes]
        if buf:
            yield bytes(buf).ljust(frame_bytes, b"\x00")
        await feeder
        await proc.wait()
        if proc.returncode not in (0, None):
            stderr = b""
            if proc.stderr is not None:
                stderr = await proc.stderr.read()
            raise CodecError(f"ffmpeg failed: {stderr.decode('utf-8', errors='replace')}")
    finally:
        if not feeder.done():
            feeder.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def iter_pcm_frames_from_audio_stream(
    audio_chunks: AsyncIterator[bytes],
    timeout_s: float = 8.0,
    *,
    first_frame_timeout_s: float = 12.0,
    max_seconds: float | None = None,
    input_format: str | None = None,
    probesize: str = "65536",
    analyzeduration: str = "0",
) -> AsyncIterator[bytes]:
    """Decode a streaming audio body into 60 ms PCM frames as ffmpeg yields samples."""
    if not ffmpeg_available():
        raise CodecError("ffmpeg is required to decode audio")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-probesize",
        probesize,
        "-analyzeduration",
        analyzeduration,
        "-flags",
        "low_delay",
    ]
    if input_format:
        cmd.extend(["-f", input_format])
    cmd.extend(
        [
            "-i",
            "pipe:0",
        ]
    )
    if max_seconds is not None and max_seconds > 0:
        cmd.extend(["-t", f"{max_seconds:.3f}"])
    cmd.extend(
        [
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(DOWNLINK_RATE),
            "pipe:1",
        ]
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None

    async def _feed() -> None:
        assert proc.stdin is not None
        try:
            async for chunk in audio_chunks:
                if not chunk:
                    continue
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("audio.ffmpeg_feed_failed", error=str(exc))
            raise
        finally:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    feeder = asyncio.create_task(_feed())

    def _retrieve_feeder_exc(task: asyncio.Task) -> None:
        # Prevent "Future exception was never retrieved" when ffmpeg closes
        # stdin at music_max_seconds while the feeder is still draining.
        try:
            task.result()
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("audio.ffmpeg_feed_failed", error=str(exc))

    feeder.add_done_callback(_retrieve_feeder_exc)
    buf = bytearray()
    frame_bytes = DOWNLINK_FRAME_SAMPLES * 2
    max_frames = None
    if max_seconds is not None and max_seconds > 0:
        max_frames = int(max_seconds * 1000 / FRAME_MS) + 1
    yielded = 0
    first = True
    try:
        while max_frames is None or yielded < max_frames:
            wait = first_frame_timeout_s if first else timeout_s
            try:
                data = await asyncio.wait_for(proc.stdout.read(frame_bytes), timeout=wait)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                stderr = await _ffmpeg_stderr(proc)
                stage = "first_frame" if first else "next_frame"
                log.warning(
                    "audio.ffmpeg_timeout",
                    stage=stage,
                    wait_s=wait,
                    yielded=yielded,
                    stderr=stderr,
                )
                raise CodecError(
                    f"ffmpeg {stage} timeout after {wait:.1f}s: {stderr}"
                ) from exc
            if not data:
                break
            first = False
            buf.extend(data)
            while len(buf) >= frame_bytes:
                yield bytes(buf[:frame_bytes])
                del buf[:frame_bytes]
                yielded += 1
                if max_frames is not None and yielded >= max_frames:
                    return
        if buf and (max_frames is None or yielded < max_frames):
            yield bytes(buf).ljust(frame_bytes, b"\x00")
            yielded += 1
        if yielded == 0:
            stderr = await _ffmpeg_stderr(proc)
            log.warning(
                "audio.ffmpeg_empty",
                exit_code=proc.returncode,
                stderr=stderr,
            )
            raise CodecError(
                f"ffmpeg produced no audio (exit {proc.returncode}): {stderr}"
            )
    finally:
        if not feeder.done():
            feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("audio.ffmpeg_feed_failed", error=str(exc))
        if proc.returncode is None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            proc.kill()
            await proc.wait()


async def iter_pcm_frames_from_file(
    path: str | Path,
    timeout_s: float = 8.0,
    *,
    first_frame_timeout_s: float = 12.0,
    max_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    """Decode a local audio file into 60 ms PCM frames via ffmpeg."""
    if not ffmpeg_available():
        raise CodecError("ffmpeg is required to decode audio")
    file_path = Path(path)
    if not file_path.is_file():
        raise CodecError(f"music file not found: {file_path}")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(file_path),
    ]
    if max_seconds is not None and max_seconds > 0:
        cmd.extend(["-t", f"{max_seconds:.3f}"])
    cmd.extend(
        [
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(DOWNLINK_RATE),
            "pipe:1",
        ]
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    buf = bytearray()
    frame_bytes = DOWNLINK_FRAME_SAMPLES * 2
    max_frames = None
    if max_seconds is not None and max_seconds > 0:
        max_frames = int(max_seconds * 1000 / FRAME_MS) + 1
    yielded = 0
    first = True
    try:
        while max_frames is None or yielded < max_frames:
            wait = first_frame_timeout_s if first else timeout_s
            try:
                data = await asyncio.wait_for(proc.stdout.read(frame_bytes), timeout=wait)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                stderr = await _ffmpeg_stderr(proc)
                stage = "first_frame" if first else "next_frame"
                raise CodecError(
                    f"ffmpeg {stage} timeout after {wait:.1f}s: {stderr}"
                ) from exc
            if not data:
                break
            first = False
            buf.extend(data)
            while len(buf) >= frame_bytes:
                yield bytes(buf[:frame_bytes])
                del buf[:frame_bytes]
                yielded += 1
                if max_frames is not None and yielded >= max_frames:
                    return
        if buf and (max_frames is None or yielded < max_frames):
            yield bytes(buf).ljust(frame_bytes, b"\x00")
            yielded += 1
        if yielded == 0:
            stderr = await _ffmpeg_stderr(proc)
            raise CodecError(f"ffmpeg produced no audio (exit {proc.returncode}): {stderr}")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def iter_pcm_frames_from_subprocess(
    cmd: list[str],
    timeout_s: float = 8.0,
    *,
    first_frame_timeout_s: float = 25.0,
    max_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    """Pipe a downloader's stdout (yt-dlp -o -) into ffmpeg PCM frames."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    stderr_buf = bytearray()

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        try:
            while True:
                data = await proc.stderr.read(4096)
                if not data:
                    break
                room = 800 - len(stderr_buf)
                if room > 0:
                    stderr_buf.extend(data[:room])
        except Exception:  # noqa: BLE001
            return

    stderr_task = asyncio.create_task(_drain_stderr())

    async def _chunks() -> AsyncIterator[bytes]:
        assert proc.stdout is not None
        try:
            while True:
                data = await proc.stdout.read(65536)
                if not data:
                    break
                yield data
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    frames = 0
    try:
        async for frame in iter_pcm_frames_from_audio_stream(
            _chunks(),
            timeout_s=timeout_s,
            first_frame_timeout_s=first_frame_timeout_s,
            max_seconds=max_seconds,
            # webm/mp4 from yt-dlp needs more than the low-latency mp3 probe.
            probesize="5000000",
            analyzeduration="10000000",
        ):
            frames += 1
            yield frame
    finally:
        if not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        if frames == 0 and proc.returncode not in (0, None):
            err = bytes(stderr_buf)[:400].decode("utf-8", errors="replace")
            raise CodecError(f"{cmd[0]} failed (exit {proc.returncode}): {err}")


def iter_pcm_frames(pcm: bytes, samples: int = DOWNLINK_FRAME_SAMPLES) -> list[bytes]:
    frame_bytes = samples * 2
    if not pcm:
        return [b"\x00" * frame_bytes]
    frames: list[bytes] = []
    for offset in range(0, len(pcm), frame_bytes):
        chunk = pcm[offset : offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk.ljust(frame_bytes, b"\x00")
        frames.append(chunk)
    return frames


async def _ffmpeg_stderr(proc: asyncio.subprocess.Process) -> str:
    if proc.stderr is None:
        return ""
    try:
        raw = await asyncio.wait_for(proc.stderr.read(), timeout=0.3)
    except Exception:  # noqa: BLE001
        return ""
    return raw.decode("utf-8", errors="replace").strip()
