from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.audio.text import chunk_burmese, completed_burmese_sentences, sanitize_for_tts
from app.config import Settings
from app.observability.logging import get_logger
from app.observability.metrics import UPSTREAM_ERRORS

log = get_logger(__name__)

PET_INTERNAL_EVENT = (
    "INTERNAL EVENT: the owner is petting your head (touch). "
    "This is not microphone speech. Do not call otto motion tools. "
    "You MAY set_emotion happy. "
    "Reply with one short spoken Burmese sentence. Never reply empty."
)

_RECONNECT_DELAYS_S = (0.3, 0.8, 2.0)


def is_transient_gemini_error(exc: BaseException | str) -> bool:
    """True for Gemini Live WebSocket 1011 / internal disconnects that should reconnect."""
    text = str(exc).lower()
    if "1011" in text:
        return True
    if "internal error occurred" in text:
        return True
    if "keepalive ping timeout" in text:
        return True
    compact = text.replace(" ", "").replace("_", "")
    if "goaway" in compact:
        return True
    code = getattr(exc, "code", None)
    if code == 1011:
        return True
    return False


@dataclass
class FunctionCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass
class TurnResult:
    input_text: str = ""
    output_text: str = ""
    function_calls: list[FunctionCall] = field(default_factory=list)
    error: str | None = None
    transient_disconnect: bool = False


class Brain(Protocol):
    async def configure_tools(self, declarations: list[dict[str, Any]]) -> None: ...

    async def ensure_connected(self) -> None: ...

    async def begin_utterance(self) -> None: ...

    async def push_pcm(self, pcm: bytes) -> None: ...

    async def end_utterance(self) -> TurnResult: ...

    async def continue_with_functions(self, results: list[dict[str, Any]]) -> TurnResult: ...

    def iter_speakable(self) -> Any: ...

    async def finish_speakable(self) -> None: ...

    async def enqueue_speakable(self, text: str) -> None: ...

    async def notify_music_finished(
        self,
        *,
        track: str,
        artist: str = "",
        status: str = "completed",
    ) -> TurnResult: ...

    async def send_text_turn(self, user_text: str) -> TurnResult: ...

    async def notify_pet(self) -> TurnResult: ...

    def set_owner_context(self, prefix: str) -> None: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


class KeyPool:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.index = 0
        self._lock = asyncio.Lock()

    async def current(self) -> str:
        if not self.keys:
            raise RuntimeError("no Gemini API keys configured")
        return self.keys[self.index % len(self.keys)]

    async def rotate(self) -> str:
        async with self._lock:
            self.index = (self.index + 1) % max(len(self.keys), 1)
            return await self.current()


class GeminiLiveBrain:
    """Gemini Live native-audio session; we discard response audio and use transcriptions."""

    def __init__(self, settings: Settings, key_pool: KeyPool) -> None:
        self.settings = settings
        self.key_pool = key_pool
        self._session = None
        self._client = None
        self._connect_cm = None
        self._tools: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._receive_task: asyncio.Task | None = None
        self._turn: TurnResult | None = None
        self._turn_done = asyncio.Event()
        self._cancelled = False
        self._streaming = False
        self._activity_started = False
        self._resumption_handle: str | None = None
        self._pcm_sent_bytes = 0
        self._last_turn_complete = False
        self._awaiting_tool_followup = False
        self._speak_q: asyncio.Queue[str | None] = asyncio.Queue()
        self._published_tts: list[str] = []
        self._speak_closed = False
        self._owner_prefix = ""

    def set_owner_context(self, prefix: str) -> None:
        self._owner_prefix = (prefix or "").strip()

    async def configure_tools(self, declarations: list[dict[str, Any]]) -> None:
        self._tools = declarations
        await self.close()

    async def ensure_connected(self) -> None:
        """Open the Live socket early so the first listen/start is not a cold connect."""
        try:
            await self._ensure_session()
            self._start_receive_loop()
        except Exception as exc:  # noqa: BLE001
            UPSTREAM_ERRORS.labels(service="gemini").inc()
            log.warning("gemini.preconnect_failed", error=str(exc))
            await self._close_live_socket()

    def _build_live_config(self) -> Any:
        from google.genai import types

        from app.ai.prompts import SYSTEM_PROMPT

        instruction = SYSTEM_PROMPT
        if self._owner_prefix:
            instruction = f"{SYSTEM_PROMPT}\n\n{self._owner_prefix}"

        # Gemini 3.1 defaults to TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO.
        # With automatic VAD disabled that "audio activity" is empty, so the
        # model receives a turn with no PCM and replies that it heard nothing.
        realtime = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
            turn_coverage=types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
        )
        resumption = None
        if self._resumption_handle:
            resumption = types.SessionResumptionConfig(handle=self._resumption_handle)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=instruction,
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=["my-MM", "my"]
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=["my-MM", "my"]
            ),
            realtime_input_config=realtime,
            session_resumption=resumption,
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            tools=[types.Tool(function_declarations=self._tools)] if self._tools else None,
        )

    async def _ensure_session(self) -> None:
        if self._session is not None:
            return
        async with self._lock:
            if self._session is not None:
                return
            from google import genai

            key = await self.key_pool.current()
            self._client = genai.Client(api_key=key)
            config = self._build_live_config()
            cm = self._client.aio.live.connect(model=self.settings.gemini_model, config=config)
            self._session = await asyncio.wait_for(
                cm.__aenter__(),
                timeout=self.settings.gemini_connect_timeout_s,
            )
            self._connect_cm = cm
            log.info("gemini.connected", model=self.settings.gemini_model)

    async def begin_utterance(self) -> None:
        self._cancelled = False
        self._streaming = True
        self._activity_started = False
        self._pcm_sent_bytes = 0
        self._last_turn_complete = False
        self._awaiting_tool_followup = False
        self._turn = TurnResult()
        self._turn_done = asyncio.Event()
        self._speak_q = asyncio.Queue()
        self._published_tts = []
        self._speak_closed = False
        try:
            await self._ensure_session()
            self._start_receive_loop()
        except Exception as exc:  # noqa: BLE001
            UPSTREAM_ERRORS.labels(service="gemini").inc()
            log.warning("gemini.begin_failed", error=str(exc))
            await self._close_live_socket()
            try:
                await self.key_pool.rotate()
                await self._ensure_session()
                self._start_receive_loop()
            except Exception as exc2:  # noqa: BLE001
                self._streaming = False
                if self._turn is not None:
                    self._turn.error = str(exc2)
                self._turn_done.set()

    async def push_pcm(self, pcm: bytes) -> None:
        from google.genai import types

        if self._cancelled or not self._streaming or self._session is None or not pcm:
            return
        if self._turn and self._turn.transient_disconnect:
            return
        try:
            blob = types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
            if not self._activity_started:
                # 3.1 has no pre-speech server buffer: send a context chunk, then
                # open the activity window, then re-send that chunk inside it.
                await asyncio.wait_for(
                    self._session.send_realtime_input(audio=blob),
                    timeout=self.settings.gemini_send_timeout_s,
                )
                await asyncio.wait_for(
                    self._session.send_realtime_input(activity_start=types.ActivityStart()),
                    timeout=self.settings.gemini_send_timeout_s,
                )
                await asyncio.wait_for(
                    self._session.send_realtime_input(audio=blob),
                    timeout=self.settings.gemini_send_timeout_s,
                )
                self._activity_started = True
                self._pcm_sent_bytes += len(pcm)
                log.info("gemini.activity_start", pcm_bytes=len(pcm))
                return
            await asyncio.wait_for(
                self._session.send_realtime_input(audio=blob),
                timeout=self.settings.gemini_send_timeout_s,
            )
            self._pcm_sent_bytes += len(pcm)
        except Exception as exc:  # noqa: BLE001
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                return
            log.warning("gemini.push_failed", error=str(exc))

    async def end_utterance(self) -> TurnResult:
        from google.genai import types

        self._streaming = False
        turn = self._turn or TurnResult()
        if turn.transient_disconnect:
            return turn
        if not self._activity_started:
            # Gate never opened — silence, no Gemini turn.
            return TurnResult()
        if self._session is None:
            return (
                turn
                if turn.error or turn.transient_disconnect
                else TurnResult(error="gemini unavailable", input_text=turn.input_text)
            )
        try:
            await asyncio.wait_for(
                self._session.send_realtime_input(activity_end=types.ActivityEnd()),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info(
                "gemini.activity_end",
                pcm_sent_bytes=self._pcm_sent_bytes,
                pcm_sent_s=round(self._pcm_sent_bytes / 32000, 2),
            )
            await asyncio.wait_for(self._turn_done.wait(), timeout=25)
        except TimeoutError:
            UPSTREAM_ERRORS.labels(service="gemini").inc()
            log.info(
                "gemini.turn_timeout",
                gemini_input=turn.input_text,
                gemini_output=turn.output_text,
            )
            return TurnResult(error="gemini timeout", input_text=turn.input_text)
        except Exception as exc:  # noqa: BLE001
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                recovered = self._turn or TurnResult(input_text=turn.input_text)
                recovered.transient_disconnect = True
                recovered.error = None
                return recovered
            UPSTREAM_ERRORS.labels(service="gemini").inc()
            return TurnResult(error=str(exc), input_text=turn.input_text)
        finished = self._turn or TurnResult()
        log.info(
            "gemini.turn_complete",
            gemini_input=finished.input_text,
            gemini_output=finished.output_text,
            gemini_error=finished.error,
            transient=finished.transient_disconnect,
            function_calls=[c.name for c in finished.function_calls],
        )
        return finished

    async def continue_with_functions(self, results: list[dict[str, Any]]) -> TurnResult:
        from google.genai import types

        if self._session is None:
            return TurnResult(error="gemini unavailable")
        prior = self._turn.input_text if self._turn else ""
        self._turn = TurnResult(input_text=prior)
        self._turn_done = asyncio.Event()
        self._awaiting_tool_followup = True
        self._start_receive_loop()
        responses = []
        for item in results:
            payload = item.get("response") or {"result": item.get("result")}
            kwargs: dict[str, Any] = {"name": item["name"], "response": payload}
            if item.get("call_id"):
                kwargs["id"] = item["call_id"]
            responses.append(types.FunctionResponse(**kwargs))
        log.info(
            "gemini.tool_response",
            names=[item["name"] for item in results],
            call_ids=[item.get("call_id") for item in results],
        )
        try:
            await asyncio.wait_for(
                self._session.send_tool_response(function_responses=responses),
                timeout=8,
            )
            await asyncio.wait_for(self._turn_done.wait(), timeout=25)
        except TimeoutError:
            return TurnResult(error="gemini timeout", input_text=prior)
        except Exception as exc:  # noqa: BLE001
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                result = self._turn or TurnResult(input_text=prior)
                result.transient_disconnect = True
                result.error = None
                return result
            return TurnResult(error=str(exc), input_text=prior)
        finally:
            self._awaiting_tool_followup = False
        return self._turn or TurnResult()

    async def notify_music_finished(
        self,
        *,
        track: str,
        artist: str = "",
        status: str = "completed",
    ) -> TurnResult:
        """Tell Live that speaker playback ended; optional short spoken wrap-up."""
        from google.genai import types

        if self._cancelled:
            return TurnResult()
        try:
            await self._ensure_session()
        except Exception as exc:  # noqa: BLE001
            log.warning("gemini.music_notify_connect_failed", error=str(exc))
            return TurnResult(error=str(exc))
        if self._session is None:
            return TurnResult(error="gemini unavailable")

        label = track if not artist else f"{track} — {artist}"
        event = (
            f"INTERNAL EVENT: music playback {status}. "
            f"Title: {label}. The robot speaker has stopped. "
            "This is not user speech. You MAY call set_emotion. "
            "Do not call search_music or any other tool. "
            "You MUST reply with one short spoken Burmese sentence "
            "that the song has finished. Never reply empty."
        )
        self._streaming = False
        self._activity_started = False
        self._awaiting_tool_followup = False
        self._turn = TurnResult(input_text=f"[music_{status}] {label}")
        self._turn_done = asyncio.Event()
        self._speak_q = asyncio.Queue()
        self._published_tts = []
        self._speak_closed = False
        self._start_receive_loop()
        try:
            await asyncio.wait_for(
                self._session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text=event)],
                    ),
                    turn_complete=True,
                ),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info(
                "gemini.music_finished",
                status=status,
                track=track,
                artist=artist or None,
            )
            await asyncio.wait_for(self._turn_done.wait(), timeout=15)
        except TimeoutError:
            log.warning("gemini.music_notify_timeout", track=track, status=status)
            await self.finish_speakable()
            return TurnResult(input_text=self._turn.input_text if self._turn else "")
        except Exception as exc:  # noqa: BLE001
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                return TurnResult(transient_disconnect=True)
            log.warning("gemini.music_notify_failed", error=str(exc), track=track)
            return TurnResult(error=str(exc))
        finished = self._turn or TurnResult()
        await self.finish_speakable()
        log.info(
            "gemini.music_notify_complete",
            gemini_output=finished.output_text,
            function_calls=[c.name for c in finished.function_calls],
        )
        return finished

    async def send_text_turn(self, user_text: str) -> TurnResult:
        """Inject typed companion chat into the existing Live socket (no uplink PCM)."""
        from google.genai import types

        cleaned = " ".join((user_text or "").split()).strip()
        if self._cancelled:
            return TurnResult(input_text=cleaned)
        try:
            await self._ensure_session()
        except Exception as exc:  # noqa: BLE001
            log.warning("gemini.text_turn_connect_failed", error=str(exc))
            return TurnResult(error=str(exc), input_text=cleaned)
        if self._session is None:
            return TurnResult(error="gemini unavailable", input_text=cleaned)

        self._streaming = False
        self._activity_started = False
        self._awaiting_tool_followup = False
        self._turn = TurnResult(input_text=cleaned)
        self._turn_done = asyncio.Event()
        self._speak_q = asyncio.Queue()
        self._published_tts = []
        self._speak_closed = False
        self._start_receive_loop()
        try:
            await asyncio.wait_for(
                self._session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text=cleaned)],
                    ),
                    turn_complete=True,
                ),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info("gemini.text_turn_sent", chars=len(cleaned))
            await asyncio.wait_for(self._turn_done.wait(), timeout=25)
        except TimeoutError:
            log.warning("gemini.text_turn_timeout")
            await self.finish_speakable()
            return TurnResult(input_text=cleaned, error="gemini timeout")
        except Exception as exc:  # noqa: BLE001
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                result = TurnResult(input_text=cleaned, transient_disconnect=True)
                return result
            log.warning("gemini.text_turn_failed", error=str(exc))
            return TurnResult(error=str(exc), input_text=cleaned)
        finished = self._turn or TurnResult()
        finished.input_text = cleaned
        log.info(
            "gemini.text_turn_complete",
            gemini_output=finished.output_text,
            function_calls=[c.name for c in finished.function_calls],
        )
        return finished

    async def notify_pet(self) -> TurnResult:
        """Owner head-touch from firmware; same Live socket, no uplink PCM."""
        return await self.send_text_turn(PET_INTERNAL_EVENT)

    async def cancel(self) -> None:
        self._cancelled = True
        self._streaming = False
        self._activity_started = False
        if self._turn_done and not self._turn_done.is_set():
            self._turn_done.set()
        if not self._speak_closed:
            self._speak_closed = True
            try:
                self._speak_q.put_nowait(None)
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        await self.cancel()
        task = self._receive_task
        self._receive_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await asyncio.sleep(0)
            except Exception:  # noqa: BLE001
                pass
        await self._close_live_socket()

    def _start_receive_loop(self) -> None:
        if self._receive_task is None or self._receive_task.done():
            self._receive_task = asyncio.create_task(self._receive_loop())

    async def _close_live_socket(self) -> None:
        async with self._lock:
            cm = self._connect_cm
            self._session = None
            self._connect_cm = None
            self._client = None
        if cm is None:
            return
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    async def _try_reconnect(self) -> bool:
        for attempt, delay in enumerate(_RECONNECT_DELAYS_S):
            try:
                if attempt:
                    await asyncio.sleep(delay)
                    await self.key_pool.rotate()
                await self._ensure_session()
                log.info("gemini.reconnected", attempt=attempt + 1)
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("gemini.reconnect_failed", attempt=attempt + 1, error=str(exc))
                await self._close_live_socket()
        return False

    async def _recover_after_transient(self, exc: BaseException, *, restart_receive: bool) -> bool:
        log.warning("gemini.transient_disconnect", error=str(exc))
        UPSTREAM_ERRORS.labels(service="gemini").inc()
        self._streaming = False
        self._activity_started = False
        if self._turn is not None:
            self._turn.transient_disconnect = True
            self._turn.error = None
        if self._turn_done and not self._turn_done.is_set():
            self._turn_done.set()
        await self._close_live_socket()
        recovered = await self._try_reconnect()
        if recovered and restart_receive:
            self._start_receive_loop()
        return recovered

    def _ingest_server_content(self, turn: TurnResult, sc: Any) -> bool:
        """Accumulate transcriptions; return True when the turn should finish."""
        input_delta = ""
        output_delta = ""
        if sc.input_transcription and sc.input_transcription.text:
            input_delta = sc.input_transcription.text
            turn.input_text += input_delta
        interim = getattr(sc, "interim_input_transcription", None)
        interim_text = getattr(interim, "text", None) if interim is not None else None
        if interim_text and not turn.input_text:
            # 3.1 often withholds final input transcription until turn_complete.
            turn.input_text = interim_text
        if sc.output_transcription and sc.output_transcription.text:
            output_delta = sc.output_transcription.text
            turn.output_text += output_delta
        model_turn = getattr(sc, "model_turn", None)
        if model_turn is not None and not output_delta:
            for part in getattr(model_turn, "parts", None) or []:
                text = getattr(part, "text", None)
                if text:
                    turn.output_text += text
        complete = bool(sc.turn_complete)
        generation_complete = bool(getattr(sc, "generation_complete", False))
        interrupted = bool(getattr(sc, "interrupted", False))
        # Gemini assumes we play native audio; turn_complete is delayed until that
        # fictional playback ends. We use Edge TTS, so finish when generation is done
        # and we already have output text (or a later tool_call will finish the wait).
        ready = complete or generation_complete or interrupted
        self._flush_speakable(final=ready, text=turn.output_text)
        if input_delta or output_delta or complete or generation_complete or interrupted:
            log.debug(
                "gemini.raw_event",
                gemini_input_delta=input_delta or None,
                gemini_output_delta=output_delta or None,
                turn_complete=complete,
                generation_complete=generation_complete,
                interrupted=interrupted,
            )
        # Native audio in model_turn is intentionally ignored (Edge TTS speaks instead).
        return ready

    def _flush_speakable(self, *, final: bool, text: str | None = None) -> None:
        """Publish completed Burmese sentences so Edge TTS can start before the turn ends."""
        if self._speak_closed:
            return
        if text is None:
            text = (self._turn.output_text if self._turn else "") or ""
        cleaned = sanitize_for_tts(text)
        chunks = chunk_burmese(cleaned) if cleaned else []
        if not final:
            chunks = completed_burmese_sentences(cleaned)
        for sentence in chunks:
            if sentence in self._published_tts:
                continue
            self._published_tts.append(sentence)
            self._speak_q.put_nowait(sentence)

    async def iter_speakable(self):
        while True:
            item = await self._speak_q.get()
            if item is None:
                return
            yield item

    async def enqueue_speakable(self, text: str) -> None:
        cleaned = sanitize_for_tts(text)
        if not cleaned or self._speak_closed:
            return
        if cleaned not in self._published_tts:
            self._published_tts.append(cleaned)
            await self._speak_q.put(cleaned)

    async def finish_speakable(self) -> None:
        self._flush_speakable(final=True)
        if not self._speak_closed:
            self._speak_closed = True
            await self._speak_q.put(None)

    async def _handle_response(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            log.info(
                "gemini.usage",
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                response_tokens=getattr(usage, "response_token_count", None)
                or getattr(usage, "candidates_token_count", None),
                total_tokens=getattr(usage, "total_token_count", None),
            )

        update = getattr(response, "session_resumption_update", None)
        if update is not None and getattr(update, "new_handle", None):
            self._resumption_handle = update.new_handle

        go_away = getattr(response, "go_away", None)
        if go_away is not None:
            log.warning(
                "gemini.go_away",
                time_left=getattr(go_away, "time_left", None),
            )
            await self._recover_after_transient(
                RuntimeError("gemini go_away"), restart_receive=False
            )
            return

        sc = getattr(response, "server_content", None)
        if sc is not None and (
            getattr(sc, "turn_complete", False) or getattr(sc, "generation_complete", False)
        ):
            # Treat generation_complete like turn_complete for the receive loop so we
            # keep the Live socket after we stop waiting for fictional playback.
            self._last_turn_complete = True

        turn = self._turn or TurnResult()
        if sc is not None and self._ingest_server_content(turn, sc):
            self._turn = turn
            self._turn_done.set()

        tool_call = getattr(response, "tool_call", None)
        if tool_call and getattr(tool_call, "function_calls", None):
            for fc in tool_call.function_calls:
                args = fc.args if isinstance(fc.args, dict) else {}
                turn.function_calls.append(
                    FunctionCall(
                        name=fc.name,
                        arguments=args,
                        call_id=getattr(fc, "id", None),
                    )
                )
            log.info(
                "gemini.raw_tool_call",
                gemini_input=turn.input_text,
                gemini_output=turn.output_text,
                function_calls=[c.name for c in turn.function_calls],
            )
            self._turn = turn
            self._flush_speakable(final=True, text=turn.output_text)
            self._turn_done.set()

    async def _receive_loop(self) -> None:
        while not self._cancelled:
            try:
                await self._ensure_session()
                assert self._session is not None
                self._last_turn_complete = False
                async for response in self._session.receive():
                    if self._cancelled:
                        continue
                    await self._handle_response(response)
                    if self._session is None:
                        break
                else:
                    # google-genai's receive() always stops after turn_complete.
                    # That is a finished model turn, not a socket death — loop
                    # receive() again on the same Live connection.
                    if (
                        self._last_turn_complete
                        or (self._turn_done and self._turn_done.is_set())
                        or self._awaiting_tool_followup
                    ):
                        log.info("gemini.receive_turn_complete")
                        continue
                    log.warning("gemini.receive_ended")
                    if not await self._recover_after_transient(
                        RuntimeError("gemini receive ended"), restart_receive=False
                    ):
                        if self._turn is not None and not self._turn.transient_disconnect:
                            self._turn.error = "gemini disconnected"
                        if self._turn_done and not self._turn_done.is_set():
                            self._turn_done.set()
                        return
                    continue
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("gemini.receive_failed", error=str(exc))
                if is_transient_gemini_error(exc):
                    recovered = await self._recover_after_transient(exc, restart_receive=False)
                    if recovered:
                        continue
                    if self._turn is not None:
                        self._turn.error = str(exc)
                        self._turn.transient_disconnect = False
                    if self._turn_done and not self._turn_done.is_set():
                        self._turn_done.set()
                    return
                UPSTREAM_ERRORS.labels(service="gemini").inc()
                if self._turn:
                    self._turn.error = str(exc)
                if self._turn_done and not self._turn_done.is_set():
                    self._turn_done.set()
                await self._close_live_socket()
                return
