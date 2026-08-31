from __future__ import annotations

from app.ai.gemini import GeminiLiveBrain, KeyPool, TurnResult, is_transient_gemini_error
from app.config import Settings


def test_detects_1011_internal_error() -> None:
    assert is_transient_gemini_error("1011 None. Internal error occurred.")
    assert is_transient_gemini_error(RuntimeError("1011 None. Internal error occurred."))
    assert is_transient_gemini_error("keepalive ping timeout")
    assert is_transient_gemini_error("go_away")


def test_non_transient_errors() -> None:
    assert not is_transient_gemini_error("permission denied")
    assert not is_transient_gemini_error("gemini timeout")
    assert not is_transient_gemini_error("")


def test_live_brain_builds_config() -> None:
    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    config = brain._build_live_config()
    assert config is not None
    assert config.response_modalities == ["AUDIO"]
    assert "Mickey" in (config.system_instruction or "")
    assert "OWNER MEMORY" not in (config.system_instruction or "")
    brain.set_owner_context("OWNER MEMORY\nOwner's spoken name: Thukha")
    with_mem = brain._build_live_config()
    assert "Thukha" in (with_mem.system_instruction or "")
    assert config.realtime_input_config is not None
    assert config.realtime_input_config.automatic_activity_detection.disabled is True
    assert config.realtime_input_config.turn_coverage == "TURN_INCLUDES_ALL_INPUT"
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    assert config.context_window_compression is not None
    assert config.session_resumption is None


def test_default_model_is_gemini_31_live() -> None:
    assert Settings.model_fields["gemini_model"].default == "gemini-3.1-flash-live-preview"
    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    assert settings.gemini_model == "gemini-3.1-flash-live-preview"


def test_ingest_finishes_on_generation_complete_with_text() -> None:
    from types import SimpleNamespace

    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    turn = TurnResult()
    generation_only = SimpleNamespace(
        input_transcription=SimpleNamespace(text=""),
        output_transcription=SimpleNamespace(text="hi"),
        turn_complete=False,
        generation_complete=True,
        interrupted=False,
        interim_input_transcription=None,
        model_turn=None,
    )
    assert brain._ingest_server_content(turn, generation_only) is True
    assert turn.output_text == "hi"


def test_ingest_waits_when_generation_complete_without_text() -> None:
    from types import SimpleNamespace

    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    turn = TurnResult()
    empty_generation = SimpleNamespace(
        input_transcription=SimpleNamespace(text=""),
        output_transcription=SimpleNamespace(text=""),
        turn_complete=False,
        generation_complete=True,
        interrupted=False,
        interim_input_transcription=None,
        model_turn=None,
    )
    assert brain._ingest_server_content(turn, empty_generation) is True

    turn_done = SimpleNamespace(
        input_transcription=SimpleNamespace(text="မင်္ဂလာပါ"),
        output_transcription=SimpleNamespace(text=""),
        turn_complete=True,
        generation_complete=True,
        interrupted=False,
        interim_input_transcription=None,
        model_turn=None,
    )
    assert brain._ingest_server_content(turn, turn_done) is True
    assert "မင်္ဂလာပါ" in turn.input_text


async def test_send_text_turn_skipped_when_cancelled() -> None:
    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    await brain.cancel()
    result = await brain.send_text_turn("မင်္ဂလာပါ")
    assert result.output_text == ""
    assert result.input_text == "မင်္ဂလာပါ"
    assert result.error is None


async def test_notify_music_finished_skipped_when_cancelled() -> None:
    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    await brain.cancel()
    result = await brain.notify_music_finished(track="Song", artist="A", status="completed")
    assert result.output_text == ""
    assert result.error is None


def _drain_speakable(brain: GeminiLiveBrain) -> list[str | None]:
    items: list[str | None] = []
    while not brain._speak_q.empty():
        items.append(brain._speak_q.get_nowait())
    return items


def test_flush_speakable_publishes_completed_sentences_before_turn_ends() -> None:
    from types import SimpleNamespace

    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    turn = TurnResult()
    brain._turn = turn

    first = SimpleNamespace(
        input_transcription=None,
        output_transcription=SimpleNamespace(text="မင်္ဂလာပါ။ ကျွန်တော်"),
        turn_complete=False,
        generation_complete=False,
        interrupted=False,
        interim_input_transcription=None,
        model_turn=None,
    )
    assert brain._ingest_server_content(turn, first) is False
    assert _drain_speakable(brain) == ["မင်္ဂလာပါ။"]

    more = SimpleNamespace(
        input_transcription=None,
        output_transcription=SimpleNamespace(text=" Mickey ပါ။ ဟယ်လို"),
        turn_complete=False,
        generation_complete=False,
        interrupted=False,
        interim_input_transcription=None,
        model_turn=None,
    )
    assert brain._ingest_server_content(turn, more) is False
    assert _drain_speakable(brain) == ["ကျွန်တော် Mickey ပါ။"]

    brain._flush_speakable(final=True, text=turn.output_text)
    assert _drain_speakable(brain) == ["ဟယ်လို"]


async def test_finish_speakable_flushes_remainder_and_closes_queue() -> None:
    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    brain._turn = TurnResult(output_text="ဟယ်လို")
    await brain.finish_speakable()
    assert await brain._speak_q.get() == "ဟယ်လို"
    assert await brain._speak_q.get() is None
    await brain.finish_speakable()
    assert brain._speak_q.empty()


def test_ingest_uses_interim_input_when_final_missing() -> None:
    from types import SimpleNamespace

    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    turn = TurnResult()
    sc = SimpleNamespace(
        input_transcription=None,
        output_transcription=None,
        turn_complete=False,
        generation_complete=False,
        interrupted=False,
        interim_input_transcription=SimpleNamespace(text="hello"),
        model_turn=None,
    )
    assert brain._ingest_server_content(turn, sc) is False
    assert turn.input_text == "hello"


async def test_receive_loop_keeps_socket_after_turn_complete() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    settings = Settings(
        environment="test",
        database_url="memory://",
        gemini_api_keys="k",
        gemini_model="gemini-3.1-flash-live-preview",
    )
    brain = GeminiLiveBrain(settings, KeyPool(settings.gemini_keys))
    receive_calls = {"n": 0}

    class _Session:
        async def receive(self):
            receive_calls["n"] += 1
            if receive_calls["n"] > 2:
                await asyncio.sleep(30)
                return
                yield  # pragma: no cover
            sc = SimpleNamespace(
                input_transcription=SimpleNamespace(text="hi"),
                output_transcription=SimpleNamespace(text="hello"),
                turn_complete=True,
                generation_complete=True,
                interrupted=False,
                interim_input_transcription=None,
                model_turn=None,
            )
            yield SimpleNamespace(
                server_content=sc,
                session_resumption_update=None,
                go_away=None,
                tool_call=None,
                usage_metadata=None,
            )

    brain._session = _Session()
    brain._ensure_session = AsyncMock()
    brain._recover_after_transient = AsyncMock(return_value=False)
    brain._turn = TurnResult()
    brain._start_receive_loop()
    await asyncio.sleep(0.05)
    assert receive_calls["n"] >= 2
    brain._recover_after_transient.assert_not_called()
    assert brain._session is not None
    await brain.cancel()
    if brain._receive_task is not None:
        brain._receive_task.cancel()
        try:
            await brain._receive_task
        except (asyncio.CancelledError, Exception):
            pass
