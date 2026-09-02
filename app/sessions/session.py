from __future__ import annotations

import asyncio
import io
import re
import time
import uuid
import wave
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from app.ai.gemini import Brain, FunctionCall, TurnResult, is_transient_gemini_error, PET_INTERNAL_EVENT
from app.ai.tool_router import ToolRouter, canonical_tool_name
from app.audio.edge_tts import EdgeTtsClient, TtsError
from app.audio.opus import (
    CodecError,
    create_codec,
    iter_pcm_frames,
    iter_pcm_frames_from_audio_stream,
    iter_pcm_frames_from_file,
    iter_pcm_frames_from_mp3,
    iter_pcm_frames_from_subprocess,
    media_to_pcm24k,
    mp3_to_pcm24k,
)
from app.audio.pacer import pace_opus_frames, pace_opus_stream
from app.audio.speech_gate import create_speech_gate
from app.audio.text import FALLBACK_BURMESE, cap_text, chunk_burmese, sanitize_for_tts
from app.companion.errors import CompanionError
from app.companion.hub import REBOOT_HINT, SLEEP_HINT, WAKE_HINT, device_idle_exempt
from app.companion.reactions import (
    RPS_SIT_HOLD_S,
    dance_payload,
    rps_countdown_line,
    rps_plan,
    rps_recover_motion,
    rps_think_motion,
    rps_timeout_line,
    ttt_plan,
)
from app.companion.status import (
    alarm_set_args,
    can_upgrade,
    firmware_upgrade_url,
    parse_alarm_state,
    parse_battery_reading,
    parse_firmware_version,
    parse_settings_state,
    parse_trims,
    settings_patch_calls,
)
from app.config import Settings
from app.mcp.client import McpClient, McpError
from app.observability.logging import get_logger
from app.observability.metrics import (
    AUDIO_GATE,
    FRAMES_DROPPED,
    QUEUE_DEPTH,
    SESSIONS_ACTIVE,
    STAGE_LATENCY,
    TURN_LATENCY,
    TURNS,
)
from app.protocol.messages import (
    abort_speaking,
    alert,
    keepalive,
    llm_emotion,
    server_hello,
    stt,
    tts,
)
from app.protocol.models import (
    KNOWN_EMOTIONS,
    AbortMessage,
    DeviceHello,
    ListenMessage,
    McpEnvelope,
    PongMessage,
)
from app.protocol.state import SessionState, StateMachine
from app.tools.http import HttpGuardError, assert_public_https
from app.tools.local_music import music_local_root, resolve_local_music_path
from app.tools.music import (
    device_music_call,
    is_music_play_request,
    is_youtube_playback,
    music_payload_for_llm,
    music_search_query,
    ytdlp_stream_cmd,
)
from app.tools.otto_gate import (
    OTTO_MOTION_TOOLS,
    looks_like_noise_utterance,
    parse_otto_moving,
    should_dispatch_otto_tool,
)

log = get_logger(__name__)

_MUSIC_DONE_BURMESE = "သီချင်း ပြီးသွားပါပြီနော်။"
_PET_BURMESE = "ပွေ့ပေးလို့ ဝမ်းသာတယ်နော်။"
_PICKUP_BURMESE = "ဟေ့ ချီလိုက်ပြီလား။"
_FALL_BURMESE = "နာတယ်နော်။"

_SENSOR_EVENTS = frozenset({"pickup", "putdown", "fall", "pet", "sleep"})
_SENSOR_MIN_INTERVAL_S = 0.5
_SENSOR_SPEECH_GAP_S = 8.0
_FALL_INHIBIT_S = 5.0
_SENSOR_CARE_KINDS = {"pet": "pet", "pickup": "pickup"}
_KEEPALIVE_GENERATION = -1

# Uplink Opus frame is 60 ms @ 16 kHz mono PCM16.
_UPLINK_FRAME_SECONDS = 0.06
# Hold ~2s of gated PCM while Gemini Live connects so wake speech is not lost.
_UPLINK_HOLD_FRAMES = 33
_DEBUG_WAV = Path(__file__).resolve().parents[2] / "debug_live_input.wav"

_FAREWELL_PHRASES = frozenset(
    {
        "bye",
        "bye bye",
        "goodbye",
        "good bye",
        "see you",
        "see ya",
        "talk later",
        "ဘိုင်း",
        "ဘိုင်ဘိုင်",
        "ဂွတ်ဘိုင်",
        "နှုတ်ဆက်ပါ",
        "နှုတ်ဆက်ပါတယ်",
        "သွားတော့မယ်",
        "သွားပြီ",
    }
)


@dataclass
class PendingMusic:
    track: str
    artist: str
    stream_url: str
    source: str
    preview: bool
    device_tool: str | None = None
    device_args: dict[str, Any] | None = None


_BUFFERED_MUSIC_TYPES = {
    "audio/mp4",
    "audio/aac",
    "audio/x-m4a",
    "audio/m4a",
    "video/mp4",
}


def _music_content_type(header: str) -> str:
    return (header or "").split(";")[0].strip().lower()


def _music_stream_pipe_friendly(content_type: str, head: bytes) -> bool:
    """MP4/AAC previews need a seekable file; MP3/Ogg/WAV can decode from a pipe."""
    ct = _music_content_type(content_type)
    if ct in _BUFFERED_MUSIC_TYPES:
        return False
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return False
    return True


def _ffmpeg_input_format(content_type: str, head: bytes) -> str | None:
    ct = _music_content_type(content_type)
    if "mpeg" in ct or "mp3" in ct or head.startswith(b"ID3"):
        return "mp3"
    if "ogg" in ct or head.startswith(b"OggS"):
        return "ogg"
    if "wav" in ct or head.startswith(b"RIFF"):
        return "wav"
    if len(head) >= 2 and head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"}:
        return "mp3"
    return None


def _normalize_farewell(text: str) -> str:
    cleaned = re.sub(r"[^\w\s\u1000-\u109F]+", " ", text.strip(), flags=re.UNICODE)
    return " ".join(cleaned.split()).casefold()


def _pcm16le_mono_to_wav_bytes(pcm: bytes, sample_rate_hz: int = 16000) -> bytes:
    if not pcm:
        return b""
    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]
    out = io.BytesIO()
    with wave.open(out, mode="wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(pcm)
    return out.getvalue()


class HandshakeError(RuntimeError):
    """Device hello is not WebSocket protocol v1 raw Opus."""

    def __init__(self, reason: str, *, close_code: int = 1003, **details: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code
        self.details = details


class Outbound:
    __slots__ = ("kind", "payload", "generation")

    def __init__(self, kind: str, payload: str | bytes, generation: int) -> None:
        self.kind = kind
        self.payload = payload
        self.generation = generation


class DeviceSession:
    def __init__(
        self,
        websocket: WebSocket,
        settings: Settings,
        device_id: str,
        client_id: str,
        router: ToolRouter,
        tts_client: EdgeTtsClient,
        brain_factory,
        authorization: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        self.ws = websocket
        self.settings = settings
        self.device_id = device_id
        self.client_id = client_id
        self.router = router
        self.tts_client = tts_client
        self.brain_factory = brain_factory
        self.authorization = authorization
        self.client_ip = client_ip
        self.device_location = None
        self.session_id = str(uuid.uuid4())
        self.state = StateMachine()
        self.codec = create_codec()
        self.mcp = McpClient(self.session_id, self._queue_json, timeout_s=settings.mcp_timeout_s)
        self.mcp.set_notification_handler(self._on_mcp_notification)
        self.brain: Brain | None = None
        self.out_queue: asyncio.Queue[Outbound | None] = asyncio.Queue(settings.outbound_queue_size)
        self._writer_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._ws_lock = asyncio.Lock()
        self._pet_task: asyncio.Task | None = None
        self._turn_task: asyncio.Task | None = None
        self._listen_started_at = 0.0
        self._utterance_bytes = 0
        self._last_rx = time.monotonic()
        self._last_uplink = 0.0
        self._closed = False
        self._close_code = 1000
        self._close_reason = ""
        self._awaiting_wake = False
        self._swallow_auto_listen = False
        self._ignored_auto_listen = False
        self._live_stale = False
        self._emotion = "happy"
        self._listen_mode = "auto"
        self._pcm_started = False
        self._forwarded_seconds = 0.0
        self._forward_capped = False
        self._pcm_debug = bytearray()
        self._awaiting_tts_stop = False
        self._gate = self._new_gate()
        self._brain_ready = False
        self._begin_task: asyncio.Task | None = None
        self._pcm_hold: deque[bytes] = deque(maxlen=_UPLINK_HOLD_FRAMES)
        self._last_gate_key: tuple[str, str] | None = None
        self._uplink_ignored_logged = False
        self._tts_played = False
        self._pending_music: PendingMusic | None = None
        self._last_pong_monotonic = 0.0
        self._motion_inhibited_until = 0.0
        self._sensor_event_last: dict[str, float] = {}
        self._last_sensor_speech = 0.0
        self._companion_lock = asyncio.Lock()
        self._companion_user_text: str | None = None
        self._owner_reconnect_pending = False
        self._departing: str | None = None
        self._battery: int | None = None
        self._charging: bool | None = None
        self._status_at = 0.0

    @property
    def departing(self) -> str | None:
        return self._departing

    def _tool_context(self, user_text: str | None = None) -> dict[str, Any]:
        ctx: dict[str, Any] = {}
        if self.client_ip:
            ctx["client_ip"] = self.client_ip
        if user_text:
            ctx["user_text"] = user_text
        loc = self.device_location
        if loc is not None:
            if loc.city:
                ctx["device_city"] = loc.city
            if loc.latitude is not None:
                ctx["device_latitude"] = loc.latitude
            if loc.longitude is not None:
                ctx["device_longitude"] = loc.longitude
        return ctx

    def _log_state(self, target: SessionState, *, previous: SessionState | None = None) -> None:
        prev = previous if previous is not None else self.state.state
        if prev == target:
            return
        log.info(
            "session.state",
            from_state=prev.value,
            to_state=target.value,
            generation=self.state.generation,
            session_id=self.session_id,
        )

    def _set_state(self, target: SessionState, *, force: bool = False) -> None:
        prev = self.state.state
        if prev == target:
            return
        if force:
            self.state.state = target
        else:
            try:
                self.state.transition(target)
            except Exception:
                self.state.state = target
        self._log_state(target, previous=prev)
        self._schedule_presence_broadcast()

    def _schedule_presence_broadcast(self) -> None:
        hub = self._companion_hub()
        if hub is None or self._closed:
            return
        if not hub.has_viewers(self.device_id):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        snapshot = self.presence_snapshot()
        loop.create_task(hub.broadcast(self.device_id, snapshot), name="companion-presence")

    async def _request_exit(self) -> None:
        """End the voice conversation; stay on /xiaozhi/v1/ in READY until the next wakeword."""
        self._awaiting_wake = True
        self._live_stale = True
        log.info("session.exit_requested", session_id=self.session_id)

    async def _notify_device_idle(self) -> None:
        """After farewell TTS, tell firmware to exit auto-listen and restore Idle."""
        log.info("session.device_idle", session_id=self.session_id)
        await self._put(
            Outbound(
                "json",
                abort_speaking(self.session_id, "conversation_ended"),
                _KEEPALIVE_GENERATION,
            )
        )
        await self._set_emotion("staticstate")
        await self._queue_json(llm_emotion(self.session_id, self._emotion), _KEEPALIVE_GENERATION)
        self._schedule_presence_broadcast()
        # If auto listen/start already arrived during farewell TTS, do not also
        # swallow the next wake. Otherwise swallow one late auto-continue.
        self._swallow_auto_listen = not self._ignored_auto_listen
        self._ignored_auto_listen = False
        self._awaiting_wake = False
        await self._reset_live_if_stale()

    async def _reset_live_if_stale(self) -> None:
        """Open a fresh Gemini Live session after farewell so context cannot loop."""
        if not self._live_stale:
            return
        if self.brain is None or self._closed:
            self._live_stale = False
            return
        reset = getattr(self.brain, "reset_conversation", None)
        try:
            if callable(reset):
                await reset()
            else:
                await self._reconnect_owner_live()
            self._live_stale = False
            log.info("session.live_reset", session_id=self.session_id)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "session.live_reset_failed",
                error=str(exc),
                session_id=self.session_id,
            )

    def _is_farewell(self, text: str) -> bool:
        normalized = _normalize_farewell(text)
        return normalized in _FAREWELL_PHRASES

    def _new_gate(self):
        return create_speech_gate(
            backend=self.settings.vad_backend,
            speech_threshold=self.settings.vad_speech_threshold,
            min_speech_ms=self.settings.vad_min_speech_ms,
            min_silence_ms=self.settings.vad_min_silence_ms,
            preroll_chunks=self.settings.vad_preroll_chunks,
            energy_speech_rms=self.settings.vad_energy_speech_rms,
            warmup_ms=self.settings.vad_warmup_ms,
            warmup_energy_rms=self.settings.vad_warmup_energy_rms,
        )

    async def run(self) -> None:
        SESSIONS_ACTIVE.inc()
        self._writer_task = asyncio.create_task(self._writer_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        try:
            await self._handshake()
            receive_task = asyncio.create_task(self._receive_loop())
            try:
                await self._discover_mcp()
                await receive_task
            finally:
                if not receive_task.done():
                    receive_task.cancel()
                    try:
                        await receive_task
                    except (asyncio.CancelledError, Exception):
                        pass
        except HandshakeError as exc:
            log.warning(
                "session.hello_rejected",
                session_id=self.session_id,
                device_id=self.device_id,
                reason=exc.reason,
                **exc.details,
            )
            self._close_code = exc.close_code
            self._close_reason = exc.reason[:123]
        except WebSocketDisconnect:
            log.info("session.disconnect", session_id=self.session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("session.error", session_id=self.session_id, error=str(exc))
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.state.close()
        await self._cancel_turn()
        if self.brain:
            await self.brain.close()
        self.mcp.cancel_pending()
        if self._keepalive_task:
            self._keepalive_task.cancel()
        await self.out_queue.put(None)
        if self._writer_task:
            try:
                await asyncio.wait_for(self._writer_task, timeout=2)
            except Exception:  # noqa: BLE001
                self._writer_task.cancel()
        if self.ws.application_state == WebSocketState.CONNECTED:
            try:
                await self.ws.close(
                    code=self._close_code,
                    reason=self._close_reason or None,
                )
            except Exception:  # noqa: BLE001
                pass
        SESSIONS_ACTIVE.dec()
        await self._notify_companion_presence()

    async def _handshake(self) -> None:
        try:
            raw = await asyncio.wait_for(
                self.ws.receive_text(), timeout=self.settings.hello_timeout_s
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise HandshakeError("hello timeout", close_code=1008) from exc
        self._last_rx = time.monotonic()
        try:
            hello = DeviceHello.model_validate_json(raw)
        except ValidationError as exc:
            raise HandshakeError("invalid hello") from exc
        if hello.transport != "websocket":
            raise HandshakeError(
                "unsupported transport",
                transport=hello.transport,
            )
        header_version = self.ws.headers.get("protocol-version")
        if hello.version != 1:
            raise HandshakeError(
                f"unsupported hello.version={hello.version}; this server speaks WebSocket protocol v1 raw Opus only",
                hello_version=hello.version,
                protocol_version_header=header_version,
                features_aec=hello.features.aec,
            )
        if hello.features.aec:
            raise HandshakeError(
                "features.aec requires binary protocol v2 timestamps; this server speaks v1 raw Opus only",
                hello_version=hello.version,
                protocol_version_header=header_version,
                features_aec=True,
            )
        params = hello.audio_params
        if params.format != "opus" or params.sample_rate != 16000 or params.channels != 1:
            raise HandshakeError(
                "unsupported uplink audio_params",
                audio_format=params.format,
                sample_rate=params.sample_rate,
                channels=params.channels,
            )
        if params.frame_duration != 60:
            log.warning("session.unexpected_frame_duration", value=params.frame_duration)
        await self._queue_json(server_hello(self.session_id))
        self.state.transition(SessionState.READY)
        store = getattr(self.ws.app.state, "locations", None)
        if store is not None:
            try:
                self.device_location = await store.get(self.device_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("session.location_load_failed", error=str(exc), session_id=self.session_id)
        log.info(
            "session.hello_ok",
            session_id=self.session_id,
            device_id=self.device_id,
            client_ip=self.client_ip,
            device_city=getattr(self.device_location, "city", None),
            wifi_ssid=getattr(self.device_location, "ssid", None),
            hello_version=hello.version,
            protocol_version_header=header_version,
            features_mcp=hello.features.mcp,
            features_aec=hello.features.aec,
            features_glyph_push=hello.features.glyph_push,
        )
        await self._notify_companion_presence()

    async def _discover_mcp(self) -> None:
        try:
            token = _bearer_token(self.authorization)
            await self.mcp.initialize(
                vision_url=self.settings.vision_url,
                vision_token=token,
            )
            await self.mcp.list_tools()
        except Exception as exc:  # noqa: BLE001
            log.warning("mcp.discover_failed", error=str(exc), session_id=self.session_id)
            self.mcp.apply_english_catalog()
        self.brain = self.brain_factory()
        try:
            await self.brain.configure_tools(self.router.gemini_tools(self.mcp))
        except Exception as exc:  # noqa: BLE001
            log.warning("brain.configure_failed", error=str(exc))
        await self._load_owner_memory()
        # Warm the Live socket now so the first wake is not a multi-second connect.
        try:
            await self.brain.ensure_connected()
        except Exception as exc:  # noqa: BLE001
            log.warning("brain.preconnect_failed", error=str(exc), session_id=self.session_id)
        # listen/start often arrives before MCP finishes; attach the brain now.
        if self.state.state == SessionState.LISTENING and not self._brain_ready:
            self._begin_task = asyncio.create_task(self._begin_brain(self.state.generation))
            log.info("session.brain_attached", session_id=self.session_id)

    async def _receive_loop(self) -> None:
        while not self._closed:
            message = await self.ws.receive()
            self._last_rx = time.monotonic()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") is not None:
                await self._on_json(message["text"])
            elif message.get("bytes") is not None:
                await self._on_binary(message["bytes"])

    async def _on_json(self, raw: str) -> None:
        try:
            data = __import__("orjson").loads(raw)
        except Exception:
            log.warning("session.malformed_json")
            return
        if not isinstance(data, dict) or "type" not in data:
            log.warning("session.missing_type")
            return
        msg_type = data["type"]
        if msg_type == "listen":
            try:
                msg = ListenMessage.model_validate(data)
            except ValidationError:
                return
            await self._on_listen(msg)
        elif msg_type == "abort":
            try:
                msg = AbortMessage.model_validate(data)
            except ValidationError:
                return
            await self._abort(reason=msg.reason)
        elif msg_type == "pong":
            try:
                PongMessage.model_validate(data)
            except ValidationError:
                return
            self._last_pong_monotonic = time.monotonic()
        elif msg_type == "mcp":
            try:
                msg = McpEnvelope.model_validate(data)
            except ValidationError:
                return
            self.mcp.on_message(msg.payload)
        else:
            log.info("session.unknown_type", type=msg_type)

    def _on_mcp_notification(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        if method != "notifications/phoe_lone.event":
            log.info(
                "session.mcp_notification_ignored",
                method=method,
                device_id=self.device_id,
                session_id=self.session_id,
            )
            return
        params = payload.get("params")
        if not isinstance(params, dict):
            return
        self._on_sensor_event(params)

    def _on_sensor_event(self, params: dict[str, Any]) -> None:
        event = params.get("event")
        if not isinstance(event, str) or event not in _SENSOR_EVENTS:
            return
        now = time.monotonic()
        last = self._sensor_event_last.get(event, 0.0)
        if now - last < _SENSOR_MIN_INTERVAL_S:
            return
        self._sensor_event_last[event] = now
        log.info(
            "session.phoe_lone_event",
            device_id=self.device_id,
            sensor_event=event,
            session_id=self.session_id,
        )
        if event == "fall":
            self._motion_inhibited_until = now + _FALL_INHIBIT_S
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._react_to_fall())
            except RuntimeError:
                pass
            return
        if event == "sleep":
            self._mark_departing("sleeping")
            self._schedule_presence_broadcast()
            return
        if event in {"pet", "pickup"}:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._credit_sensor_care(event))
                if not self._sensor_speech_blocked():
                    if event == "pet":
                        self._pet_task = loop.create_task(self._react_to_pet())
                    else:
                        self._pet_task = loop.create_task(self._react_to_pickup())
            except RuntimeError:
                pass
            return
        if self.state.state == SessionState.SPEAKING:
            return

    async def _on_listen(self, msg: ListenMessage) -> None:
        if msg.state == "start":
            mode = msg.mode or "auto"
            self._listen_mode = mode
            # After farewell, Xiaozhi auto-continues with listen/start. Ignore that
            # so abort can take the device Idle. The next start is a new wake.
            if mode != "manual":
                if self._awaiting_wake:
                    self._ignored_auto_listen = True
                    log.info(
                        "session.listen_ignored_until_wake",
                        session_id=self.session_id,
                        mode=mode,
                        reason="awaiting_wake",
                    )
                    return
                if self._swallow_auto_listen:
                    self._swallow_auto_listen = False
                    log.info(
                        "session.listen_ignored_until_wake",
                        session_id=self.session_id,
                        mode=mode,
                        reason="post_abort",
                    )
                    return
            await self._start_listen()
        elif msg.state == "stop":
            await self._stop_listen()
        elif msg.state == "detect":
            await self._abort(reason="wake_word_detected")
            await self._start_listen()

    async def _start_listen(self) -> None:
        self._awaiting_wake = False
        self._swallow_auto_listen = False
        self._ignored_auto_listen = False
        await self._cancel_turn()
        await self._reset_live_if_stale()
        gen = self.state.bump_generation()
        try:
            self._set_state(SessionState.LISTENING)
        except Exception:
            self.state.state = SessionState.LISTENING
            self._log_state(SessionState.LISTENING)
        self.codec.reset()
        self._utterance_bytes = 0
        self._listen_started_at = time.monotonic()
        self._pcm_started = False
        self._forwarded_seconds = 0.0
        self._forward_capped = False
        self._pcm_debug.clear()
        self._awaiting_tts_stop = False
        # Reuse Silero LSTM across listens — a new scorer is cold for ~1s.
        self._gate.reset(reset_scorer=False)
        self._emotion = "happy"
        self._brain_ready = False
        self._pcm_hold.clear()
        self._last_gate_key = None
        self._uplink_ignored_logged = False
        self._tts_played = False
        self._pending_music = None
        self._last_uplink = time.monotonic()
        log.info("session.listen_start", generation=gen, session_id=self.session_id)
        if self.brain:
            self._begin_task = asyncio.create_task(self._begin_brain(gen))
        else:
            log.info("session.brain_pending", session_id=self.session_id)

    async def _begin_brain(self, generation: int) -> None:
        if not self.brain or generation != self.state.generation:
            return
        try:
            await self.brain.begin_utterance()
        except Exception as exc:  # noqa: BLE001
            log.warning("session.begin_failed", error=str(exc), session_id=self.session_id)
            return
        if generation != self.state.generation:
            return
        self._brain_ready = True
        # Flush even if we already left LISTENING (hangover may have ended the turn).
        await self._flush_pcm_hold()

    async def _flush_pcm_hold(self) -> None:
        if not self.brain or not self._brain_ready:
            return
        while self._pcm_hold:
            chunk = self._pcm_hold.popleft()
            if self._forwarded_seconds >= self.settings.max_forwarded_audio_seconds:
                self._forward_capped = True
                self._pcm_hold.clear()
                return
            await self.brain.push_pcm(chunk)
            self._forwarded_seconds += _UPLINK_FRAME_SECONDS
            self._pcm_started = True
            AUDIO_GATE.labels(result="accepted").inc()

    async def _await_brain_ready(self) -> None:
        """Wait for background begin_utterance so held wake PCM is flushed before end."""
        if (
            self.brain
            and not self._brain_ready
            and (self._begin_task is None or self._begin_task.done())
        ):
            self._begin_task = asyncio.create_task(self._begin_brain(self.state.generation))
        task = self._begin_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        elif self._brain_ready:
            await self._flush_pcm_hold()

    def _log_gate(self, *, decision: str, reason: str, emitted: int) -> None:
        key = (decision, reason)
        changed = key != self._last_gate_key
        self._last_gate_key = key
        payload = {
            "decision": decision,
            "reason": reason,
            "rms": round(self._gate.last_rms, 1),
            "speech_prob": round(self._gate.last_prob, 3),
            "threshold": round(self._gate.last_threshold, 3),
            "emitted": emitted,
            "session_id": self.session_id,
        }
        if changed:
            log.info("session.gate", **payload)
        else:
            log.debug("session.gate", **payload)

    def _log_uplink_ignored(self, reason: str) -> None:
        FRAMES_DROPPED.labels(direction="uplink").inc()
        if not self._uplink_ignored_logged:
            self._uplink_ignored_logged = True
            log.info(
                "session.uplink_ignored",
                reason=reason,
                state=self.state.state.value,
                session_id=self.session_id,
            )
        else:
            log.debug(
                "session.uplink_ignored",
                reason=reason,
                state=self.state.state.value,
                session_id=self.session_id,
            )

    async def _forward_chunk(self, chunk: bytes) -> bool:
        """Forward one gated PCM frame, or hold it until Gemini is ready.

        Returns False if the utterance was ended (forward cap).
        """
        if self._forwarded_seconds >= self.settings.max_forwarded_audio_seconds:
            self._forward_capped = True
            log.info(
                "session.forward_capped",
                session_id=self.session_id,
                forwarded_s=round(self._forwarded_seconds, 2),
            )
            AUDIO_GATE.labels(result="dropped").inc()
            await self._end_utterance_now("forward_capped")
            return False
        if self.brain and self._brain_ready:
            await self.brain.push_pcm(chunk)
            self._forwarded_seconds += _UPLINK_FRAME_SECONDS
            self._pcm_started = True
            AUDIO_GATE.labels(result="accepted").inc()
            return True
        # Brain not ready yet — keep gated speech so wake audio is not lost.
        self._pcm_hold.append(chunk)
        self._pcm_started = True
        AUDIO_GATE.labels(result="accepted").inc()
        if not self._uplink_ignored_logged:
            # Reuse first-drop flag only for not_listening; brain hold is expected.
            log.debug(
                "session.pcm_held",
                held=len(self._pcm_hold),
                session_id=self.session_id,
            )
        return True

    async def _on_binary(self, packet: bytes) -> None:
        if self.state.state != SessionState.LISTENING:
            self._log_uplink_ignored("not_listening")
            return
        if time.monotonic() - self._listen_started_at > self.settings.max_utterance_seconds:
            await self._end_utterance_now("max_utterance")
            return
        if self._utterance_bytes + len(packet) > self.settings.max_utterance_bytes:
            await self._end_utterance_now("max_utterance_bytes")
            return
        self._utterance_bytes += len(packet)
        self._last_uplink = time.monotonic()
        try:
            pcm = self.codec.decode_uplink(packet)
        except CodecError:
            FRAMES_DROPPED.labels(direction="uplink").inc()
            log.info("session.uplink_decode_failed", session_id=self.session_id)
            return

        if pcm:
            self._pcm_debug.extend(pcm)

        if self._forward_capped:
            AUDIO_GATE.labels(result="dropped").inc()
            self._log_gate(decision="DROPPED", reason="capped", emitted=0)
            return

        emitted = self._gate.process(pcm)
        self._log_gate(
            decision=self._gate.last_decision,
            reason=self._gate.last_reason,
            emitted=len(emitted),
        )
        if not emitted:
            AUDIO_GATE.labels(result="dropped").inc()
            if self._gate.ever_opened and self._gate.last_reason == "hangover_expired":
                await self._end_utterance_now("hangover_expired")
            return

        for chunk in emitted:
            if not await self._forward_chunk(chunk):
                return

    def _save_debug_wav(self) -> None:
        pcm = bytes(self._pcm_debug)
        try:
            wav_bytes = _pcm16le_mono_to_wav_bytes(pcm)
            _DEBUG_WAV.write_bytes(wav_bytes)
            duration_s = (len(pcm) / 2) / 16000 if pcm else 0.0
            log.info(
                "session.debug_wav",
                path=str(_DEBUG_WAV.resolve()),
                pcm_bytes=len(pcm),
                wav_bytes=len(wav_bytes),
                duration_s=round(duration_s, 2),
                gate_accepted=self._gate.accepted_chunks,
                gate_dropped=self._gate.dropped_chunks,
                gate_opened=self._gate.ever_opened,
                last_prob=round(self._gate.last_prob, 3),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("session.debug_wav_failed", error=str(exc))

    async def _end_utterance_now(self, reason: str) -> None:
        """End the Gemini turn without waiting for client listen/stop.

        Xiaozhi leaves listening on tts/start, so we send that immediately to
        stop the ESP32 from streaming leftover static.
        """
        if self.state.state != SessionState.LISTENING:
            return
        log.info(
            "session.endpoint",
            reason=reason,
            session_id=self.session_id,
            accepted=self._gate.accepted_chunks,
            forwarded_s=round(self._forwarded_seconds, 2),
        )
        # Spec: device streams Opus until listen/stop, abort, or tts/start.
        await self._queue_json(tts(self.session_id, "start"))
        self._awaiting_tts_stop = True
        await self._stop_listen()

    async def _release_listening_interrupt(self, generation: int | None = None) -> None:
        """Send tts/stop so the ESP32 leaves Speaking even when there is no audio.

        Uses the current generation so abort/cancel cannot drop the stop frame.
        """
        if not self._awaiting_tts_stop:
            return
        self._awaiting_tts_stop = False
        await self._queue_json(tts(self.session_id, "stop"))

    async def _stop_listen(self) -> None:
        if self.state.state != SessionState.LISTENING:
            return
        await self._await_brain_ready()
        self._save_debug_wav()
        self._set_state(SessionState.THINKING)
        generation = self.state.generation
        self._turn_task = asyncio.create_task(self._run_turn(generation))

    async def _execute_turn(self, generation: int) -> None:
        speak_task: asyncio.Task | None = None
        if generation != self.state.generation:
            return

        if not self._pcm_started or not self._gate.ever_opened:
            log.info(
                "session.silence",
                session_id=self.session_id,
                reason="vad_no_speech",
                dropped=self._gate.dropped_chunks,
                accepted=self._gate.accepted_chunks,
                last_prob=round(self._gate.last_prob, 3),
                last_rms=round(self._gate.last_rms, 1),
                last_decision=self._gate.last_decision,
                last_reason=self._gate.last_reason,
                pcm_debug_bytes=len(self._pcm_debug),
            )
            if self.brain:
                await self.brain.cancel()
                await self.brain.finish_speakable()
            await self._release_listening_interrupt(generation)
            TURNS.labels(result="ok").inc()
            return

        if not self.brain:
            await self._speak(FALLBACK_BURMESE, generation, emotion="sad")
            return

        speak_task = asyncio.create_task(self._consume_speakable(generation))
        try:
            t0 = time.monotonic()
            result = await self.brain.end_utterance()
            STAGE_LATENCY.labels(stage="gemini").observe(time.monotonic() - t0)
            log.info(
                "session.gemini_raw",
                session_id=self.session_id,
                gemini_input=result.input_text,
                gemini_output=result.output_text,
                gemini_error=result.error,
                transient=result.transient_disconnect,
                function_calls=[c.name for c in result.function_calls],
            )
            if generation != self.state.generation:
                return

            if result.transient_disconnect or (
                result.error and is_transient_gemini_error(result.error)
            ):
                log.info("session.gemini_reconnecting", session_id=self.session_id)
                await self.brain.finish_speakable()
                await speak_task
                await self._release_listening_interrupt(generation)
                TURNS.labels(result="ok").inc()
                return

            user_text = " ".join((result.input_text or "").split()).strip()
            if user_text:
                await self._queue_json(stt(self.session_id, user_text), generation)
                await self._credit_interaction("voice")
                if self._is_farewell(user_text):
                    log.info(
                        "session.farewell",
                        session_id=self.session_id,
                        user_text=user_text,
                    )
                    await self._request_exit()

            self._inject_music_play(result, user_text)
            if result.function_calls:
                try:
                    result = await asyncio.wait_for(
                        self._handle_tools(result, generation, depth=0),
                        timeout=max(20.0, self.settings.mcp_timeout_s + 12.0),
                    )
                except TimeoutError:
                    log.warning("session.tools_timeout", session_id=self.session_id)
                    result = TurnResult(input_text=result.input_text, error="tools timeout")
            if generation != self.state.generation:
                return

            if result.transient_disconnect or (
                result.error and is_transient_gemini_error(result.error)
            ):
                log.info("session.gemini_reconnecting", session_id=self.session_id)
                await self.brain.finish_speakable()
                await speak_task
                TURNS.labels(result="ok").inc()
                return

            text = sanitize_for_tts(
                cap_text((result.output_text or "").strip(), self.settings.max_tts_chars)
            )
            log.info(
                "session.tts_sanitized",
                session_id=self.session_id,
                gemini_output=result.output_text,
                tts_string=text,
                gemini_error=result.error,
            )
            if result.error:
                self._emotion = "sad"
                await self.brain.enqueue_speakable(FALLBACK_BURMESE)
            elif not text and not self._tts_played:
                log.info("session.silence", session_id=self.session_id, reason="llm_empty")
                await self.brain.finish_speakable()
                await speak_task
                TURNS.labels(result="ok").inc()
                return

            await self.brain.finish_speakable()
            await speak_task
            TURNS.labels(result="ok").inc()
        finally:
            if speak_task is not None and not speak_task.done():
                if self.brain:
                    try:
                        await self.brain.finish_speakable()
                    except Exception:  # noqa: BLE001
                        pass
                speak_task.cancel()
                try:
                    await speak_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _run_turn(self, generation: int) -> None:
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                self._execute_turn(generation),
                timeout=self.settings.turn_timeout_s,
            )
        except TimeoutError:
            log.warning("session.turn_timeout", session_id=self.session_id, generation=generation)
            TURNS.labels(result="error").inc()
            if self.brain:
                await self.brain.cancel()
                try:
                    await self.brain.finish_speakable()
                except Exception:  # noqa: BLE001
                    pass
            await self._release_listening_interrupt(generation)
        except asyncio.CancelledError:
            TURNS.labels(result="cancelled").inc()
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("session.turn_failed", error=str(exc), session_id=self.session_id)
            TURNS.labels(result="error").inc()
            if generation == self.state.generation and not self._tts_played:
                await self._speak(FALLBACK_BURMESE, generation, emotion="sad")
        finally:
            TURN_LATENCY.observe(time.monotonic() - started)
            try:
                if generation == self.state.generation:
                    await self._play_pending_music(generation)
            except asyncio.CancelledError:
                log.warning(
                    "session.music_cancelled",
                    session_id=self.session_id,
                    generation=generation,
                )
            await self._release_listening_interrupt()
            if self.state.state != SessionState.CLOSED and (
                generation == self.state.generation or self._awaiting_wake
            ):
                self._set_state(SessionState.READY, force=True)
            if self._awaiting_wake and not self._closed:
                await self._notify_device_idle()
            await self._maybe_reconnect_owner_memory()

    async def _handle_tools(
        self,
        result: TurnResult,
        generation: int,
        *,
        depth: int = 0,
    ) -> TurnResult:
        assert self.brain
        if depth >= self.settings.max_tool_rounds:
            log.warning("session.tools_max_rounds", session_id=self.session_id, depth=depth)
            return TurnResult(input_text=result.input_text, error="too many tool rounds")
        calls = result.function_calls
        user_text = " ".join((result.input_text or "").split()).strip()
        if self._companion_user_text:
            user_text = self._companion_user_text
        responses: list[dict[str, Any]] = []
        device_moving: bool | None = None
        exited = False
        say_goodbye = ""
        for call in calls:
            if generation != self.state.generation:
                return result
            tool_name = canonical_tool_name(call.name)
            if tool_name in OTTO_MOTION_TOOLS:
                if (
                    tool_name != "self.otto.stop"
                    and time.monotonic() < self._motion_inhibited_until
                ):
                    log.info(
                        "session.otto_gated",
                        session_id=self.session_id,
                        name=tool_name,
                        user_text=user_text or None,
                        reason="fall_inhibit",
                    )
                    payload = {
                        "ok": False,
                        "skipped": True,
                        "reason": "fall_inhibit",
                        "note": "Ignored: motion inhibited after a fall.",
                    }
                    responses.append(
                        {
                            "name": call.name,
                            "call_id": call.call_id,
                            "response": self.router.as_function_response(payload),
                        }
                    )
                    continue
                if tool_name == "self.otto.stop" and not looks_like_noise_utterance(user_text):
                    # Only probe status when STT is non-empty but stop-intent is unclear.
                    if device_moving is None and not should_dispatch_otto_tool(
                        tool_name, user_text, device_moving=False
                    ):
                        device_moving = await self._otto_is_moving()
                if not should_dispatch_otto_tool(
                    tool_name, user_text, device_moving=device_moving
                ):
                    log.info(
                        "session.otto_gated",
                        session_id=self.session_id,
                        name=tool_name,
                        user_text=user_text or None,
                        reason="empty_or_no_intent",
                    )
                    payload = {
                        "ok": False,
                        "skipped": True,
                        "reason": "otto_gated",
                        "note": "Ignored: no clear stop/motion request in user speech.",
                    }
                    responses.append(
                        {
                            "name": call.name,
                            "call_id": call.call_id,
                            "response": self.router.as_function_response(payload),
                        }
                    )
                    continue
            payload = await self.router.dispatch(
                tool_name,
                call.arguments,
                self.mcp,
                self._set_emotion,
                on_exit=self._request_exit,
                context=self._tool_context(user_text=result.input_text),
            )
            if tool_name == "search_music":
                self._queue_music(payload)
                payload = music_payload_for_llm(payload)
            if payload.get("exit"):
                exited = True
                say_goodbye = str(payload.get("say_goodbye") or say_goodbye).strip()
            log.info(
                "session.tool_dispatch",
                session_id=self.session_id,
                name=tool_name,
                call_id=call.call_id,
                ok="error" not in payload,
                playback=payload.get("playback"),
            )
            responses.append(
                {
                    "name": call.name,
                    "call_id": call.call_id,
                    "response": self.router.as_function_response(payload),
                }
            )
        if exited:
            text = say_goodbye or (result.output_text or "").strip()
            if text:
                await self.brain.enqueue_speakable(text)
            log.info(
                "session.exit_turn_complete",
                session_id=self.session_id,
                say_goodbye=text or None,
            )
            return TurnResult(input_text=result.input_text, output_text=text)
        continued = await self.brain.continue_with_functions(responses)
        if continued.function_calls and generation == self.state.generation:
            return await self._handle_tools(continued, generation, depth=depth + 1)
        continued.input_text = continued.input_text or result.input_text
        return continued

    async def _otto_is_moving(self) -> bool:
        """Best-effort read of self.otto.get_status for stop gating."""
        if not self.mcp or "self.otto.get_status" not in self.mcp.tool_by_name:
            return False
        try:
            text = await self.mcp.call("self.otto.get_status", {})
            return parse_otto_moving(text)
        except (McpError, Exception) as exc:  # noqa: BLE001
            log.info("session.otto_status_failed", session_id=self.session_id, error=str(exc))
            return False

    async def _set_emotion(self, emotion: str) -> None:
        self._emotion = emotion if emotion in KNOWN_EMOTIONS else "neutral"

    @property
    def closed(self) -> bool:
        return self._closed

    def _companion_store(self):
        try:
            return getattr(self.ws.app.state, "companion_store", None)
        except Exception:  # noqa: BLE001
            return None

    def _companion_hub(self):
        try:
            return getattr(self.ws.app.state, "companion", None)
        except Exception:  # noqa: BLE001
            return None

    async def _load_owner_memory(self) -> None:
        store = self._companion_store()
        if store is None or self.brain is None:
            return
        try:
            memory = await store.get_memory(self.device_id, self.client_id)
        except Exception as exc:  # noqa: BLE001
            log.info("companion.memory_load_failed", error=str(exc), session_id=self.session_id)
            return
        await self.apply_owner_memory(memory, reconnect=False)

    async def apply_owner_memory(self, memory, *, reconnect: bool = True) -> None:
        from app.companion.life import owner_prompt_prefix

        if self.brain is None:
            return
        setter = getattr(self.brain, "set_owner_context", None)
        if callable(setter):
            setter(owner_prompt_prefix(memory))
        if not reconnect:
            return
        if self.state.state != SessionState.READY:
            self._owner_reconnect_pending = True
            return
        await self._reconnect_owner_live()

    async def _reconnect_owner_live(self) -> None:
        if self.brain is None or self._closed:
            self._owner_reconnect_pending = False
            return
        self._owner_reconnect_pending = False
        close = getattr(self.brain, "close", None)
        if callable(close):
            try:
                await close()
            except Exception as exc:  # noqa: BLE001
                log.info("companion.memory_reconnect_close_failed", error=str(exc))
        ensure = getattr(self.brain, "ensure_connected", None)
        if callable(ensure):
            try:
                await ensure()
            except Exception as exc:  # noqa: BLE001
                log.info("companion.memory_reconnect_failed", error=str(exc))

    async def _maybe_reconnect_owner_memory(self) -> None:
        if not self._owner_reconnect_pending:
            return
        if self._closed or self.state.state != SessionState.READY:
            return
        await self._reconnect_owner_live()

    async def _credit_interaction(self, kind: str) -> None:
        hub = self._companion_hub()
        if hub is None:
            return
        try:
            await hub.credit(self.device_id, self.client_id, kind)
        except Exception as exc:  # noqa: BLE001
            log.info("companion.care_credit_failed", error=str(exc), kind=kind)

    async def _credit_sensor_care(self, event: str) -> None:
        kind = _SENSOR_CARE_KINDS.get(event)
        if kind is None:
            return
        await self._credit_interaction(kind)

    def _pet_busy(self) -> bool:
        if self._closed or self.brain is None:
            return True
        if self.state.state != SessionState.READY:
            return True
        if self._companion_lock.locked():
            return True
        if self._turn_task is not None and not self._turn_task.done():
            return True
        return False

    def _sensor_speech_in_flight(self) -> bool:
        return self._pet_task is not None and not self._pet_task.done()

    def _sensor_speech_blocked(self) -> bool:
        if self._sensor_speech_in_flight():
            return True
        return time.monotonic() - self._last_sensor_speech < _SENSOR_SPEECH_GAP_S

    def _mark_sensor_speech(self) -> None:
        self._last_sensor_speech = time.monotonic()

    async def _react_to_pet(self) -> None:
        if self._pet_busy():
            return
        if time.monotonic() - self._last_sensor_speech < _SENSOR_SPEECH_GAP_S:
            return
        self._mark_sensor_speech()
        notify = getattr(self.brain, "notify_pet", None)
        send = getattr(self.brain, "send_text_turn", None)
        if not callable(notify) and not callable(send):
            return
        generation = self.state.generation
        self._set_state(SessionState.THINKING, force=True)
        speak_task = asyncio.create_task(self._consume_speakable(generation))
        try:
            if callable(notify):
                result = await notify()
            else:
                result = await send(PET_INTERNAL_EVENT)
            if generation != self.state.generation or self._closed:
                return
            if result.function_calls:
                keep = [
                    call
                    for call in result.function_calls
                    if not canonical_tool_name(call.name).startswith("self.otto.")
                    and canonical_tool_name(call.name) != "search_music"
                ]
                if keep:
                    result.function_calls = keep
                    try:
                        result = await self._handle_tools(result, generation, depth=0)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("session.pet_tools_failed", error=str(exc))
                if generation != self.state.generation:
                    return
            text = sanitize_for_tts(
                cap_text((result.output_text or "").strip(), self.settings.max_tts_chars)
            )
            if not text:
                text = _PET_BURMESE
                if self.brain:
                    await self.brain.enqueue_speakable(text)
            if self.brain:
                await self.brain.finish_speakable()
            await speak_task
            if not self._tts_played and generation == self.state.generation:
                await self._speak(text, generation, emotion="happy")
        except Exception as exc:  # noqa: BLE001
            log.info("session.pet_react_failed", error=str(exc), session_id=self.session_id)
        finally:
            if not speak_task.done():
                if self.brain:
                    try:
                        await self.brain.finish_speakable()
                    except Exception:  # noqa: BLE001
                        pass
                speak_task.cancel()
                try:
                    await speak_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self.state.state != SessionState.CLOSED and generation == self.state.generation:
                self._set_state(SessionState.READY, force=True)

    async def _react_to_pickup(self) -> None:
        if self._pet_busy():
            return
        if time.monotonic() - self._last_sensor_speech < _SENSOR_SPEECH_GAP_S:
            return
        self._mark_sensor_speech()
        generation = self.state.generation
        self._set_state(SessionState.THINKING, force=True)
        try:
            await self._speak(_PICKUP_BURMESE, generation, emotion="surprised")
        except Exception as exc:  # noqa: BLE001
            log.info("session.pickup_react_failed", error=str(exc), session_id=self.session_id)
        finally:
            if self.state.state != SessionState.CLOSED and generation == self.state.generation:
                self._set_state(SessionState.READY, force=True)

    async def _react_to_fall(self) -> None:
        await self._queue_json(
            alert(self.session_id, "Warning", "Fall detected", "sad"),
        )
        if self._pet_busy() or self._sensor_speech_in_flight():
            return
        if time.monotonic() - self._last_sensor_speech < _SENSOR_SPEECH_GAP_S:
            return
        self._mark_sensor_speech()
        generation = self.state.generation
        self._set_state(SessionState.THINKING, force=True)
        try:
            await self._speak(_FALL_BURMESE, generation, emotion="sad")
        except Exception as exc:  # noqa: BLE001
            log.info("session.fall_react_failed", error=str(exc), session_id=self.session_id)
        finally:
            if self.state.state != SessionState.CLOSED and generation == self.state.generation:
                self._set_state(SessionState.READY, force=True)

    async def _notify_companion_presence(self, *, offline: bool = False) -> None:
        hub = self._companion_hub()
        if hub is None:
            return
        try:
            await hub.push_presence(self.device_id, offline=offline)
        except Exception as exc:  # noqa: BLE001
            log.info("companion.presence_notify_failed", error=str(exc), session_id=self.session_id)

    def presence_snapshot(self) -> dict[str, Any]:
        online = not self._closed
        away = self._departing if online else None
        payload: dict[str, Any] = {
            "type": "presence",
            "online": online,
            "state": None if not online else self.state.state.value,
            "emotion": None if not online else self._emotion,
            "battery": self._battery,
            "charging": self._charging,
            "sleeping": away == "sleeping",
            "rebooting": away == "rebooting",
        }
        if not online:
            payload["hint"] = WAKE_HINT
        elif away == "sleeping":
            payload["hint"] = SLEEP_HINT
        elif away == "rebooting":
            payload["hint"] = REBOOT_HINT
        return payload

    async def refresh_status(self) -> None:
        if self._closed:
            return
        if self._status_at and time.monotonic() - self._status_at < 15.0:
            return
        text = ""
        try:
            if "self.battery.get_level" in self.mcp.tool_by_name:
                text = await self.mcp.call("self.battery.get_level", {})
            elif "self.get_device_status" in self.mcp.tool_by_name:
                text = await self.mcp.call("self.get_device_status", {})
        except (McpError, Exception) as exc:  # noqa: BLE001
            log.info("session.status_refresh_failed", session_id=self.session_id, error=str(exc))
            return
        if not text:
            return
        level, charging = parse_battery_reading(text)
        if level is not None:
            self._battery = level
        if charging is not None:
            self._charging = charging
        self._status_at = time.monotonic()

    async def companion_action(
        self, kind: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._closed:
            raise CompanionError("offline", "Mickey is offline.")
        payload = args or {}
        try:
            if kind == "stop":
                return await self._companion_stop()
            if kind == "dance":
                return await self._companion_dance(payload)
            async with self._companion_lock:
                if kind == "rps_react":
                    return await self._companion_rps_react(payload)
                if kind == "ttt_react":
                    return await self._companion_ttt_react(payload)
                if kind == "chat":
                    return await self._companion_chat(payload)
                if kind == "alarm_get":
                    return await self._companion_alarm_get()
                if kind == "alarm_set":
                    return await self._companion_alarm_set(payload)
                if kind == "alarm_cancel":
                    return await self._companion_alarm_cancel()
                if kind == "sleep":
                    return await self._companion_sleep(payload)
                if kind == "settings_get":
                    return await self._companion_settings_get()
                if kind == "settings_set":
                    return await self._companion_settings_set(payload)
                if kind == "reboot":
                    return await self._companion_reboot()
                if kind == "upgrade":
                    return await self._companion_upgrade()
                raise CompanionError("invalid", "Unknown companion command.")
        finally:
            await self._maybe_reconnect_owner_memory()

    def _motion_inhibited(self) -> bool:
        return time.monotonic() < self._motion_inhibited_until

    async def _apply_emotion(self, emotion: str) -> None:
        await self._set_emotion(emotion)
        await self._queue_json(llm_emotion(self.session_id, self._emotion))

    async def _companion_stop(self) -> dict[str, Any]:
        try:
            await self.mcp.call("self.otto.stop", {})
        except (McpError, Exception) as exc:  # noqa: BLE001
            log.info("companion.stop_mcp_failed", session_id=self.session_id, error=str(exc))
        if self.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            await self._abort(reason="companion_stop")
        return {"ok": True}

    async def _companion_dance(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.state.state == SessionState.THINKING:
            raise CompanionError("busy", "Mickey is thinking.")
        action = str(args.get("action") or "")
        motion = dance_payload(action)
        if self._motion_inhibited():
            return {"ok": True, "skipped": True, "reason": "fall_inhibit"}
        await self._apply_emotion("happy")
        try:
            await self.mcp.call("self.otto.action", motion)
        except McpError as exc:
            raise CompanionError("invalid", str(exc)) from exc
        return {"ok": True}

    async def _rps_home(self, inhibited: bool) -> None:
        if inhibited:
            return
        try:
            await self.mcp.call("self.otto.action", rps_recover_motion())
        except McpError as exc:
            log.info("companion.rps_motion_failed", session_id=self.session_id, error=str(exc))

    async def _companion_rps_react(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        on_reveal = args.get("on_reveal")
        generation = self.state.generation
        inhibited = self._motion_inhibited()
        revealed = False
        result: Any = None

        async def reveal_web() -> Any:
            nonlocal revealed, result
            if revealed:
                return result
            revealed = True
            if callable(on_reveal):
                result = await on_reveal()
            return result

        await self._apply_emotion("thinking")

        async def wind_up() -> None:
            if inhibited:
                return
            try:
                await self.mcp.call("self.otto.action", rps_think_motion())
            except McpError as exc:
                log.info("companion.rps_motion_failed", session_id=self.session_id, error=str(exc))

        try:
            await asyncio.gather(
                self._speak(rps_countdown_line(), generation, emotion="thinking"),
                wind_up(),
            )
        finally:
            if generation == self.state.generation:
                await reveal_web()

        def _finish(payload: dict[str, Any]) -> dict[str, Any]:
            if self.state.state != SessionState.CLOSED:
                self._set_state(SessionState.READY, force=True)
            return payload

        if generation != self.state.generation:
            return _finish({"ok": True, "aborted": True, "skipped": inhibited})

        if isinstance(result, dict):
            if result.get("aborted"):
                return _finish({"ok": True, "aborted": True, "skipped": inhibited})
            winner = result.get("winner")
            timed_out = bool(result.get("timeout"))
            match_over = result.get("phase") == "match_over"
        else:
            winner = str(args.get("winner") or "") or None
            timed_out = False
            match_over = False

        if winner not in {"player", "mickey", "draw"}:
            if timed_out:
                await self._speak(rps_timeout_line(), generation, emotion="confused")
            await self._rps_home(inhibited)
            return _finish({"ok": True, "timeout": timed_out, "skipped": inhibited})

        plan = rps_plan(str(winner), match_over=match_over)
        await self._apply_emotion(plan.end_emotion)

        async def react_body() -> None:
            if inhibited:
                return
            try:
                await self.mcp.call("self.otto.action", plan.motion)
            except McpError as exc:
                log.info("companion.rps_motion_failed", session_id=self.session_id, error=str(exc))
                return
            if plan.motion.get("action") == "sit":
                await asyncio.sleep(RPS_SIT_HOLD_S)
            if plan.motion.get("action") != "home":
                await self._rps_home(False)

        if generation == self.state.generation:
            await asyncio.gather(
                self._speak(plan.line, generation, emotion=plan.end_emotion),
                react_body(),
            )
            await self._apply_emotion(plan.end_emotion)
        return _finish({"ok": True, "skipped": inhibited})

    async def _companion_ttt_react(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        mode = str(args.get("mode") or "result")
        generation = self.state.generation
        inhibited = self._motion_inhibited()

        def _finish(payload: dict[str, Any]) -> dict[str, Any]:
            if self.state.state != SessionState.CLOSED:
                self._set_state(SessionState.READY, force=True)
            return payload

        if mode == "think":
            self._set_state(SessionState.THINKING, force=True)
            await self._apply_emotion("thinking")
            if not inhibited and generation == self.state.generation:
                try:
                    await self.mcp.call("self.otto.action", rps_think_motion())
                except McpError as exc:
                    log.info("companion.ttt_motion_failed", session_id=self.session_id, error=str(exc))
            return _finish({"ok": True, "skipped": inhibited})

        winner = str(args.get("winner") or "draw")
        plan = ttt_plan(winner)
        await self._apply_emotion(plan.end_emotion)

        async def react_body() -> None:
            if inhibited:
                return
            try:
                await self.mcp.call("self.otto.action", plan.motion)
            except McpError as exc:
                log.info("companion.ttt_motion_failed", session_id=self.session_id, error=str(exc))
                return
            if plan.motion.get("action") == "sit":
                await asyncio.sleep(RPS_SIT_HOLD_S)
            if plan.motion.get("action") != "home":
                await self._rps_home(False)

        if generation == self.state.generation:
            await asyncio.gather(
                self._speak(plan.line, generation, emotion=plan.end_emotion),
                react_body(),
            )
            await self._apply_emotion(plan.end_emotion)
        return _finish({"ok": True, "skipped": inhibited})

    async def _mcp_text(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        try:
            return await self.mcp.call(name, arguments or {})
        except McpError as exc:
            raise CompanionError("invalid", str(exc)) from exc

    async def _mcp_user_text(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        ensure = getattr(self.mcp, "ensure_user_tools", None)
        if callable(ensure):
            await ensure()
        return await self._mcp_text(name, arguments)

    def _mark_departing(self, reason: str) -> None:
        self._departing = reason
        hub = self._companion_hub()
        if hub is None:
            return
        if reason == "sleeping":
            hub.mark_asleep(self.device_id)
        elif reason == "rebooting":
            hub.mark_awake(self.device_id)

    def _upgrade_available(self) -> bool:
        version, url = self.settings.advertised_firmware()
        return can_upgrade(url, version)

    async def _companion_alarm_get(self) -> dict[str, Any]:
        text = await self._mcp_text("self.mickey.alarm.get", {})
        return parse_alarm_state(text)

    async def _companion_alarm_set(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = alarm_set_args(args)
        await self._mcp_text("self.mickey.alarm.set", payload)
        if payload.get("sleep_now"):
            self._mark_departing("sleeping")
            return {
                "type": "alarm.state",
                "set": True,
                "hour": payload["hour"],
                "minute": payload["minute"],
                "repeat": payload["repeat"],
            }
        return await self._companion_alarm_get()

    async def _companion_alarm_cancel(self) -> dict[str, Any]:
        await self._mcp_text("self.mickey.alarm.cancel", {})
        return parse_alarm_state("")

    async def _companion_sleep(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.state.state in {
            SessionState.LISTENING,
            SessionState.THINKING,
            SessionState.SPEAKING,
        }:
            await self._abort(reason="companion_sleep")
        payload: dict[str, Any] = {}
        if args:
            try:
                set_args = alarm_set_args(args)
            except CompanionError:
                set_args = None
            if set_args:
                payload = {"hour": set_args["hour"], "minute": set_args["minute"]}
        await self._mcp_text("self.mickey.sleep.now", payload)
        self._mark_departing("sleeping")
        if payload:
            return {
                "type": "alarm.state",
                "set": True,
                "hour": payload["hour"],
                "minute": payload["minute"],
                "repeat": True,
            }
        return {"type": "alarm.state", "set": True, "hour": None, "minute": None, "repeat": True}

    async def _companion_settings_get(self) -> dict[str, Any]:
        text = ""
        if "self.get_device_status" in self.mcp.tool_by_name:
            text = await self._mcp_text("self.get_device_status", {})
        state = parse_settings_state(
            text,
            firmware_version=self.settings.firmware_version,
            can_upgrade=self._upgrade_available(),
        )
        if "self.otto.get_trims" in self.mcp.tool_by_name:
            try:
                state["trims"] = parse_trims(await self._mcp_text("self.otto.get_trims", {}))
            except CompanionError:
                state["trims"] = {}
        try:
            info = await self._mcp_user_text("self.get_system_info", {})
            version = parse_firmware_version(info)
            if version:
                state["firmware_version"] = version
        except CompanionError:
            pass
        return state

    async def _companion_settings_set(self, args: dict[str, Any]) -> dict[str, Any]:
        calls = settings_patch_calls(args)
        for name, payload in calls:
            await self._mcp_text(name, payload)
        state = await self._companion_settings_get()
        if "volume" in args and args.get("volume") is not None:
            state["volume"] = int(args["volume"])
        if "brightness" in args and args.get("brightness") is not None:
            state["brightness"] = int(args["brightness"])
        if args.get("theme") in {"light", "dark"}:
            state["theme"] = args["theme"]
        if isinstance(args.get("trims"), dict):
            merged = dict(state.get("trims") or {})
            merged.update(args["trims"])
            state["trims"] = merged
        return state

    async def _companion_reboot(self) -> dict[str, Any]:
        await self._mcp_user_text("self.reboot", {})
        self._mark_departing("rebooting")
        return {"ok": True, "rebooting": True}

    async def _companion_upgrade(self) -> dict[str, Any]:
        version, url = self.settings.advertised_firmware()
        url = firmware_upgrade_url(url, version)
        await self._mcp_user_text("self.upgrade_firmware", {"url": url})
        self._mark_departing("rebooting")
        return {"ok": True, "upgrading": True}

    async def _companion_chat(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.state.state in {SessionState.THINKING, SessionState.SPEAKING}:
            raise CompanionError("busy", "Mickey is busy. Tap Stop first.")
        if self.brain is None:
            raise CompanionError("busy", "Mickey is still waking up. Wait a moment.")
        send = getattr(self.brain, "send_text_turn", None)
        if not callable(send):
            raise CompanionError("invalid", "Chat is not available.")
        text = str(args.get("text") or "").strip()
        if not text:
            raise CompanionError("invalid", "Type a message first.")
        if self._is_farewell(text):
            await self._request_exit()
        self._companion_user_text = text
        self._tts_played = False
        generation = self.state.generation
        self._set_state(SessionState.THINKING, force=True)
        speak_task = asyncio.create_task(self._consume_speakable(generation))
        try:
            result = await send(text)
            if generation != self.state.generation:
                return {"ok": True, "text": "", "emotion": self._emotion, "aborted": True}
            result.input_text = text
            if result.transient_disconnect or (
                result.error and is_transient_gemini_error(result.error)
            ):
                log.info("session.chat_gemini_reconnecting", session_id=self.session_id)
                raise CompanionError("busy", "Mickey lost that thought. Try again.")
            if result.function_calls:
                try:
                    result = await asyncio.wait_for(
                        self._handle_tools(result, generation, depth=0),
                        timeout=max(20.0, self.settings.mcp_timeout_s + 12.0),
                    )
                except TimeoutError:
                    log.warning("session.chat_tools_timeout", session_id=self.session_id)
                    result = TurnResult(input_text=text, error="tools timeout")
            if generation != self.state.generation:
                return {"ok": True, "text": "", "emotion": self._emotion, "aborted": True}
            reply = sanitize_for_tts(
                cap_text((result.output_text or "").strip(), self.settings.max_tts_chars)
            )
            if result.error:
                self._emotion = "sad"
                await self.brain.enqueue_speakable(FALLBACK_BURMESE)
                reply = sanitize_for_tts(FALLBACK_BURMESE)
            elif not reply and not self._tts_played:
                await self.brain.enqueue_speakable(FALLBACK_BURMESE)
                reply = sanitize_for_tts(FALLBACK_BURMESE)
            await self.brain.finish_speakable()
            await speak_task
            return {"ok": True, "text": reply, "emotion": self._emotion}
        finally:
            if not speak_task.done():
                if self.brain:
                    try:
                        await self.brain.finish_speakable()
                    except Exception:  # noqa: BLE001
                        pass
                speak_task.cancel()
                try:
                    await speak_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._companion_user_text = None
            if self.state.state != SessionState.CLOSED:
                self._set_state(SessionState.READY, force=True)
            if self._awaiting_wake and not self._closed:
                await self._notify_device_idle()

    async def _consume_speakable(self, generation: int) -> None:
        """Speak Gemini sentences as they are published — do not wait for the full turn."""
        started = False
        t0 = time.monotonic()
        try:
            async for sentence in self.brain.iter_speakable():
                if generation != self.state.generation:
                    return
                sentence = sanitize_for_tts(sentence)
                if not sentence:
                    continue
                if not started:
                    already_started = self._awaiting_tts_stop
                    self._awaiting_tts_stop = False
                    self._set_state(SessionState.SPEAKING, force=True)
                    await self._queue_json(llm_emotion(self.session_id, self._emotion), generation)
                    if not already_started:
                        await self._queue_json(tts(self.session_id, "start"), generation)
                    started = True
                    log.info(
                        "session.tts_first_sentence",
                        session_id=self.session_id,
                        tts_string=sentence,
                        wait_s=round(time.monotonic() - t0, 3),
                    )
                await self._queue_json(tts(self.session_id, "sentence_start", sentence), generation)
                log.info(
                    "session.tts_sentence",
                    session_id=self.session_id,
                    tts_string=sentence,
                )
                await self._stream_sentence(sentence, generation)
                self._tts_played = True
        except (TtsError, CodecError) as exc:
            log.warning("session.tts_failed", error=str(exc))
            await self._alert_tts_failed(generation)
        finally:
            STAGE_LATENCY.labels(stage="tts").observe(time.monotonic() - t0)
            if started and generation == self.state.generation:
                if self._pending_music is not None:
                    # Keep the Xiaozhi speaking window open for the music stream.
                    self._awaiting_tts_stop = True
                    return
                await self._queue_json(tts(self.session_id, "stop"), generation)

    async def _speak(self, text: str, generation: int, emotion: str = "happy") -> None:
        if generation != self.state.generation:
            return
        text = sanitize_for_tts(text)
        log.info(
            "session.tts_handoff",
            session_id=self.session_id,
            tts_string=text,
            emotion=emotion,
        )
        chunks = chunk_burmese(text) if text else []
        if not text or not chunks:
            log.info("session.tts_skip_empty", session_id=self.session_id)
            await self._release_listening_interrupt(generation)
            return

        already_started = self._awaiting_tts_stop
        self._awaiting_tts_stop = False
        self._set_state(SessionState.SPEAKING, force=True)
        await self._queue_json(llm_emotion(self.session_id, emotion), generation)
        if not already_started:
            await self._queue_json(tts(self.session_id, "start"), generation)

        t0 = time.monotonic()
        try:
            for sentence in chunks:
                if generation != self.state.generation:
                    return
                await self._queue_json(tts(self.session_id, "sentence_start", sentence), generation)
                log.info(
                    "session.tts_sentence",
                    session_id=self.session_id,
                    tts_string=sentence,
                )
                await self._stream_sentence(sentence, generation)
                self._tts_played = True
        except (TtsError, CodecError) as exc:
            log.warning("session.tts_failed", error=str(exc))
            await self._alert_tts_failed(generation)
        finally:
            STAGE_LATENCY.labels(stage="tts").observe(time.monotonic() - t0)
            if generation == self.state.generation:
                if self._pending_music is not None:
                    self._awaiting_tts_stop = True
                    return
                await self._queue_json(tts(self.session_id, "stop"), generation)

    async def _stream_sentence(self, sentence: str, generation: int) -> None:
        await pace_opus_stream(
            self._opus_packets_for_sentence(sentence),
            lambda packet: self._queue_bytes(packet, generation),
            should_continue=lambda: generation == self.state.generation and not self._closed,
        )

    async def _opus_packets_for_sentence(self, sentence: str):
        async for frame in self._pcm_frames_for_sentence(sentence):
            yield self.codec.encode_downlink(frame)

    async def _pcm_frames_for_sentence(self, sentence: str):
        timeout_s = self.settings.tts_timeout_s
        iter_mp3 = getattr(self.tts_client, "iter_mp3", None)
        if callable(iter_mp3):
            async for frame in iter_pcm_frames_from_mp3(iter_mp3(sentence), timeout_s=timeout_s):
                yield frame
            return
        mp3 = await self.tts_client.synthesize(sentence)
        pcm = await mp3_to_pcm24k(mp3, timeout_s=timeout_s)
        for frame in iter_pcm_frames(pcm):
            yield frame

    async def _pace_downlink(self, pcm: bytes, generation: int) -> None:
        packets = [self.codec.encode_downlink(frame) for frame in iter_pcm_frames(pcm)]
        await pace_opus_frames(
            packets,
            lambda packet: self._queue_bytes(packet, generation),
            should_continue=lambda: generation == self.state.generation and not self._closed,
        )

    def _inject_music_play(self, result: TurnResult, user_text: str) -> None:
        if not user_text or not is_music_play_request(user_text):
            return
        if any(call.name == "search_music" for call in result.function_calls):
            return
        query = music_search_query(user_text)
        result.function_calls.append(
            FunctionCall(
                name="search_music",
                arguments={"query": query, "play": True},
                call_id="music-intent",
            )
        )
        log.info(
            "session.music_intent",
            session_id=self.session_id,
            query=query,
            user_text=user_text,
        )

    def _queue_music(self, payload: dict[str, Any]) -> None:
        if payload.get("error") or payload.get("playback") != "queued":
            return
        url = str(payload.get("stream_url") or "").strip()
        if not url:
            return
        device = device_music_call(self.mcp.tool_by_name, payload)
        self._pending_music = PendingMusic(
            track=str(payload.get("track") or "song").strip() or "song",
            artist=str(payload.get("artist") or "").strip(),
            stream_url=url,
            source=str(payload.get("source") or "catalog"),
            preview=bool(payload.get("preview")),
            device_tool=device[0] if device else None,
            device_args=device[1] if device else None,
        )
        log.info(
            "session.music_queued",
            session_id=self.session_id,
            track=self._pending_music.track,
            artist=self._pending_music.artist,
            source=self._pending_music.source,
            device_tool=self._pending_music.device_tool,
        )

    async def _play_pending_music(self, generation: int) -> None:
        clip = self._pending_music
        self._pending_music = None
        if clip is None or generation != self.state.generation or self._closed:
            return
        if clip.device_tool:
            status = "completed"
            try:
                text = await self.mcp.call(clip.device_tool, clip.device_args or {})
                log.info(
                    "session.music_device",
                    session_id=self.session_id,
                    tool=clip.device_tool,
                    result=text,
                )
            except (McpError, ValueError) as exc:
                log.warning("session.music_device_failed", error=str(exc), tool=clip.device_tool)
                status = "failed"
            await self._notify_music_finished(clip, status, generation)
            return

        already_started = self._awaiting_tts_stop or self.state.state == SessionState.SPEAKING
        self._awaiting_tts_stop = False
        self._set_state(SessionState.SPEAKING, force=True)
        await self._queue_json(llm_emotion(self.session_id, "happy"), generation)
        if not already_started:
            await self._queue_json(tts(self.session_id, "start"), generation)
        label = clip.track if not clip.artist else f"{clip.track} — {clip.artist}"
        await self._queue_json(tts(self.session_id, "sentence_start", label), generation)
        status = "failed"
        try:
            await self._stream_music_url(clip.stream_url, generation, source=clip.source)
            self._tts_played = True
            status = "completed"
            log.info(
                "session.music_played",
                session_id=self.session_id,
                track=clip.track,
                source=clip.source,
                preview=clip.preview,
            )
        except asyncio.CancelledError:
            log.warning(
                "session.music_cancelled",
                session_id=self.session_id,
                track=clip.track,
                url=clip.stream_url,
            )
            raise
        except (CodecError, HttpGuardError, httpx.HTTPError, Exception) as exc:  # noqa: BLE001
            log.warning(
                "session.music_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                url=clip.stream_url,
                track=clip.track,
                source=clip.source,
            )
        if generation == self.state.generation:
            await self._notify_music_finished(clip, status, generation)
        if generation == self.state.generation:
            self._awaiting_tts_stop = True

    async def _notify_music_finished(self, clip: PendingMusic, status: str, generation: int) -> None:
        if self.brain is None or generation != self.state.generation or self._closed:
            return
        notify = getattr(self.brain, "notify_music_finished", None)
        if not callable(notify):
            return
        try:
            result = await notify(
                track=clip.track,
                artist=clip.artist,
                status=status,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("session.music_notify_failed", error=str(exc), track=clip.track)
            return
        if generation != self.state.generation:
            return
        if result.function_calls:
            keep = [
                call
                for call in result.function_calls
                if canonical_tool_name(call.name) != "search_music"
            ]
            if keep:
                result.function_calls = keep
                try:
                    result = await self._handle_tools(result, generation, depth=0)
                except Exception as exc:  # noqa: BLE001
                    log.warning("session.music_notify_tools_failed", error=str(exc))
            if generation != self.state.generation:
                return
        text = sanitize_for_tts(
            cap_text((result.output_text or "").strip(), self.settings.max_tts_chars)
        )
        if not text:
            text = _MUSIC_DONE_BURMESE
        self._awaiting_tts_stop = True
        await self._speak(text, generation, emotion="happy")

    async def _stream_music_url(self, url: str, generation: int, *, source: str = "") -> None:
        started = time.monotonic()
        frames = 0

        async def packets() -> AsyncIterator[bytes]:
            nonlocal frames
            async for frame in self._pcm_frames_for_music(url, source=source):
                if frames == 0:
                    log.info(
                        "session.music_first_frame",
                        session_id=self.session_id,
                        wait_s=round(time.monotonic() - started, 3),
                        url=url,
                    )
                frames += 1
                if frames == 1 or frames % 100 == 0:
                    log.info(
                        "session.music_frames",
                        session_id=self.session_id,
                        frames=frames,
                        elapsed_s=round(time.monotonic() - started, 3),
                    )
                yield self.codec.encode_downlink(frame)

        await pace_opus_stream(
            packets(),
            lambda packet: self._queue_bytes(packet, generation),
            should_continue=lambda: generation == self.state.generation and not self._closed,
        )
        if frames == 0:
            raise CodecError("music stream produced no frames")
        log.info(
            "session.music_stream_done",
            session_id=self.session_id,
            frames=frames,
            elapsed_s=round(time.monotonic() - started, 3),
            url=url,
        )

    async def _pcm_frames_for_music(
        self, url: str, *, source: str = ""
    ) -> AsyncIterator[bytes]:
        local_path = self._local_music_path(url, source=source)
        if local_path is not None:
            log.info(
                "session.music_local_file",
                session_id=self.session_id,
                path=str(local_path),
            )
            async for frame in iter_pcm_frames_from_file(
                local_path,
                timeout_s=8.0,
                first_frame_timeout_s=15.0,
                max_seconds=None,
            ):
                yield frame
            return
        if is_youtube_playback(url, source):
            assert_public_https(url)
            cmd = ytdlp_stream_cmd(url, self.settings)
            log.info(
                "session.music_ytdlp_pipe",
                session_id=self.session_id,
                url=url,
                bin=cmd[0],
            )
            async for frame in iter_pcm_frames_from_subprocess(
                cmd,
                timeout_s=self.settings.music_download_timeout_s,
                first_frame_timeout_s=max(20.0, self.settings.music_ytdlp_timeout_s),
                max_seconds=self._music_max_seconds(),
            ):
                yield frame
            return
        assert_public_https(url)
        client = getattr(self.ws.app.state, "http", None)
        if client is None:
            raise CodecError("HTTP client is not available")
        timeout = httpx.Timeout(
            connect=min(10.0, self.settings.music_download_timeout_s),
            read=self.settings.music_download_timeout_s,
            write=10.0,
            pool=10.0,
        )
        started = time.monotonic()
        log.info("session.music_download_start", session_id=self.session_id, url=url)
        try:
            async with client.stream(
                "GET",
                url,
                timeout=timeout,
                follow_redirects=True,
                headers={"Accept": "audio/*,*/*;q=0.8"},
            ) as response:
                content_type = response.headers.get("content-type", "")
                log.info(
                    "session.music_http",
                    session_id=self.session_id,
                    status=response.status_code,
                    content_type=content_type or None,
                    content_length=response.headers.get("content-length"),
                    final_url=str(response.url),
                )
                response.raise_for_status()
                body = response.aiter_bytes(64 * 1024)
                first = await anext(body, b"")
                if not first:
                    raise CodecError("empty music stream")
                max_bytes = self.settings.music_max_bytes

                async def chunks() -> AsyncIterator[bytes]:
                    total = len(first)
                    yield first
                    async for chunk in body:
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            log.warning(
                                "session.music_truncated",
                                session_id=self.session_id,
                                bytes=total,
                                limit=max_bytes,
                            )
                            return
                        yield chunk
                    log.info(
                        "session.music_http_eof",
                        session_id=self.session_id,
                        bytes=total,
                        elapsed_s=round(time.monotonic() - started, 3),
                    )

                input_format = _ffmpeg_input_format(content_type, first)
                if _music_stream_pipe_friendly(content_type, first):
                    log.info(
                        "session.music_decode",
                        session_id=self.session_id,
                        mode="pipe",
                        content_type=content_type or None,
                        input_format=input_format,
                        head_bytes=len(first),
                    )
                    async for frame in iter_pcm_frames_from_audio_stream(
                        chunks(),
                        timeout_s=8.0,
                        first_frame_timeout_s=15.0,
                        max_seconds=self._music_max_seconds(),
                        input_format=input_format,
                    ):
                        yield frame
                    return

                log.info(
                    "session.music_decode",
                    session_id=self.session_id,
                    mode="buffer",
                    content_type=content_type or None,
                    head_bytes=len(first),
                )
                audio = await self._buffer_audio(chunks())
                cap = self._music_max_seconds()
                decode_timeout = self.settings.tts_timeout_s
                if cap is not None:
                    decode_timeout = max(decode_timeout, cap)
                pcm = await media_to_pcm24k(
                    audio,
                    timeout_s=decode_timeout,
                    max_seconds=cap,
                )
                for frame in iter_pcm_frames(pcm):
                    yield frame
        except asyncio.CancelledError:
            log.info("session.music_http_cancelled", session_id=self.session_id, url=url)
            raise
        except (CodecError, HttpGuardError):
            raise
        except httpx.HTTPStatusError as exc:
            raise CodecError(f"music HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise CodecError(f"music HTTP timeout: {exc}") from exc
        except Exception as exc:
            log.warning(
                "session.music_http_failed",
                session_id=self.session_id,
                error=str(exc),
                error_type=type(exc).__name__,
                url=url,
                elapsed_s=round(time.monotonic() - started, 3),
            )
            raise

    def _music_max_seconds(self) -> float | None:
        """None means play to the end (MUSIC_MAX_SECONDS <= 0)."""
        cap = float(self.settings.music_max_seconds)
        if cap <= 0:
            return None
        return cap

    def _local_music_path(self, url: str, *, source: str = "") -> Path | None:
        if source != "local" and not Path(url).is_absolute():
            return None
        root = music_local_root(self.settings.music_local_dir)
        if root is None:
            return None
        return resolve_local_music_path(root, url)

    async def _buffer_audio(self, chunks: AsyncIterator[bytes]) -> bytes:
        parts: list[bytes] = []
        total = 0
        async for chunk in chunks:
            if not chunk:
                continue
            parts.append(chunk)
            total += len(chunk)
            if total >= 256_000 and (total // 65536) % 4 == 0:
                log.info("session.music_buffering", session_id=self.session_id, bytes=total)
        if not parts:
            raise CodecError("empty music stream")
        return b"".join(parts)

    async def _abort(self, reason: str | None = None) -> None:
        log.info("session.abort", reason=reason, session_id=self.session_id)
        needs_stop = self._awaiting_tts_stop or self.state.state == SessionState.SPEAKING
        await self._cancel_turn()
        if needs_stop:
            self._awaiting_tts_stop = False
            await self._queue_json(tts(self.session_id, "stop"))
        if self.state.state != SessionState.CLOSED:
            self._set_state(SessionState.READY, force=True)

    async def _cancel_turn(self) -> None:
        self.state.bump_generation()
        self._pending_music = None
        self._brain_ready = False
        self._pcm_hold.clear()
        if self._begin_task and not self._begin_task.done():
            self._begin_task.cancel()
            try:
                await self._begin_task
            except (asyncio.CancelledError, Exception):
                pass
        self._begin_task = None
        if self.brain:
            await self.brain.cancel()
        if self._turn_task and not self._turn_task.done():
            if self._turn_task is asyncio.current_task():
                # close() may run from inside the active turn; do not await ourselves.
                pass
            else:
                self._turn_task.cancel()
                try:
                    await self._turn_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._turn_task = None
        while True:
            try:
                self.out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _queue_json(self, payload: str, generation: int | None = None) -> None:
        gen = self.state.generation if generation is None else generation
        await self._put(Outbound("json", payload, gen))

    async def _queue_bytes(self, payload: bytes, generation: int) -> None:
        await self._put(Outbound("bytes", payload, generation))

    async def _put(self, item: Outbound) -> None:
        if self._closed:
            return
        await self.out_queue.put(item)

    def _should_send(self, item: Outbound) -> bool:
        if item.generation == _KEEPALIVE_GENERATION:
            return True
        return item.generation == self.state.generation

    async def _send_outbound(self, item: Outbound) -> bool:
        if self._closed:
            return False
        async with self._ws_lock:
            if self._closed:
                return False
            try:
                if item.kind == "bytes":
                    await self.ws.send_bytes(item.payload)  # type: ignore[arg-type]
                else:
                    await self.ws.send_text(item.payload)  # type: ignore[arg-type]
                return True
            except Exception:
                return False

    async def _writer_loop(self) -> None:
        while True:
            item = await self.out_queue.get()
            QUEUE_DEPTH.labels(queue="outbound").set(self.out_queue.qsize())
            if item is None:
                return
            if not self._should_send(item):
                continue
            if not await self._send_outbound(item):
                return

    async def _keepalive_loop(self) -> None:
        interval = self.settings.keepalive_interval_s
        idle_limit = self.settings.device_idle_timeout_s
        try:
            while not self._closed:
                await asyncio.sleep(interval)
                if (
                    self.state.state == SessionState.LISTENING
                    and self._last_uplink > 0
                    and time.monotonic() - self._last_uplink > self.settings.listen_idle_timeout_s
                ):
                    log.info("session.listen_idle", session_id=self.session_id)
                    await self._end_utterance_now("listen_idle")
                if self.ws.client_state != WebSocketState.CONNECTED:
                    log.info("session.socket_gone", session_id=self.session_id)
                    await self.close()
                    return
                idle_s = time.monotonic() - self._last_rx
                if idle_s > idle_limit:
                    hub = self._companion_hub()
                    has_viewers = bool(hub and hub.has_viewers(self.device_id))
                    if device_idle_exempt(self.state.state, has_viewers):
                        pass
                    else:
                        log.info(
                            "session.idle_timeout",
                            session_id=self.session_id,
                            state=self.state.state.value,
                            idle_s=round(idle_s, 1),
                        )
                        await self.close()
                        return
                ping = Outbound("json", keepalive(self.session_id), _KEEPALIVE_GENERATION)
                if not await self._send_outbound(ping):
                    await self.close()
                    return
        except asyncio.CancelledError:
            return


    async def _alert_tts_failed(self, generation: int) -> None:
        if generation != self.state.generation:
            return
        await self._queue_json(
            alert(self.session_id, "Warning", "Speech playback failed", "sad"),
            generation,
        )


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._by_device: dict[str, DeviceSession] = {}
        self._lock = asyncio.Lock()

    async def attach(self, session: DeviceSession) -> None:
        async with self._lock:
            previous = self._by_device.get(session.device_id)
            if previous is None and len(self._by_device) >= self.settings.max_concurrent_sessions:
                raise RuntimeError("too many sessions")
            self._by_device[session.device_id] = session
        hub = None
        try:
            hub = getattr(session.ws.app.state, "companion", None)
        except Exception:  # noqa: BLE001
            hub = None
        if hub is not None:
            hub.mark_awake(session.device_id)
        if previous and previous is not session:
            await previous.close()

    async def detach(self, session: DeviceSession) -> None:
        async with self._lock:
            if self._by_device.get(session.device_id) is session:
                self._by_device.pop(session.device_id, None)

    def get(self, device_id: str) -> DeviceSession | None:
        return self._by_device.get(device_id.lower())

    def online(self, device_id: str) -> bool:
        session = self.get(device_id)
        return session is not None and not session.closed

    def count(self) -> int:
        return len(self._by_device)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    prefix = "bearer "
    if value.lower().startswith(prefix):
        return value[len(prefix) :].strip()
    if " " not in value:
        return value
    return None
