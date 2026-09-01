from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.ai.gemini import FunctionCall
from app.audio.opus import DOWNLINK_FRAME_SAMPLES
from app.config import Settings
from app.main import create_app
from app.protocol.state import SessionState
from tests.activation import ota_and_bind
from tests.fakes import FakeBrain, QuietCodec, SilentCodec, SpeechThenQuietCodec


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="memory://",
        allow_auto_provision=True,
        auth_pepper="pepper",
        gemini_api_keys="k",
        public_http_origin="http://testserver",
        public_ws_origin="ws://testserver",
        vad_backend="energy",
        vad_min_speech_ms=60.0,
        vad_min_silence_ms=800.0,
        vad_preroll_chunks=0,
        vad_energy_speech_rms=200.0,
        max_forwarded_audio_seconds=8.0,
    )


def _recv_json(ws):
    while True:
        payload = json.loads(ws.receive_text())
        if payload.get("type") != "ping":
            return payload


HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


async def _fake_pcm(_mp3: bytes, timeout_s: float = 45.0) -> bytes:
    return b"\x00\x00" * 100


from tests.activation import ota_and_bind


def _open_session(client):
    _, token = ota_and_bind(client, client_id="cid")
    return client.websocket_connect(
        "/xiaozhi/v1/",
        headers={
            "Authorization": f"Bearer {token}",
            "Device-Id": "aa:bb:cc:dd:ee:ff",
            "Client-Id": "cid",
        },
    )


def _handshake_and_listen(ws, frames: int = 3, send_stop: bool = True) -> str:
    ws.send_text(json.dumps(HELLO))
    hello = _recv_json(ws)
    session_id = hello["session_id"]
    init = _recv_json(ws)
    ws.send_text(
        json.dumps(
            {
                "type": "mcp",
                "session_id": session_id,
                "payload": {"jsonrpc": "2.0", "id": init["payload"]["id"], "result": {}},
            }
        )
    )
    listed = _recv_json(ws)
    ws.send_text(
        json.dumps(
            {
                "type": "mcp",
                "session_id": session_id,
                "payload": {
                    "jsonrpc": "2.0",
                    "id": listed["payload"]["id"],
                    "result": {"tools": []},
                },
            }
        )
    )
    _listen(ws, session_id, frames=frames, send_stop=send_stop)
    return session_id


def _listen(ws, session_id: str, frames: int = 3, send_stop: bool = True) -> None:
    ws.send_text(
        json.dumps(
            {
                "session_id": session_id,
                "type": "listen",
                "state": "start",
                "mode": "auto",
            }
        )
    )
    for _ in range(frames):
        ws.send_bytes(b"\x00\x01")
    if send_stop:
        ws.send_text(json.dumps({"session_id": session_id, "type": "listen", "state": "stop"}))


def _collect_until_tts_stop(ws, limit: int = 20) -> list[dict]:
    seen = []
    for _ in range(limit):
        message = ws.receive()
        if message["type"] == "websocket.send" and message.get("bytes"):
            continue
        text = message.get("text")
        if not text:
            continue
        payload = json.loads(text)
        if payload.get("type") == "ping":
            continue
        seen.append(payload)
        if payload.get("type") == "tts" and payload.get("state") == "stop":
            break
    return seen


def test_full_turn_stt_tts_ordering() -> None:
    application = create_app(_settings())
    brain = FakeBrain(input_text="မင်္ဂလာပါ", output_text="မင်္ဂလာပါ။")

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            with _open_session(client) as ws:
                _handshake_and_listen(ws)
                seen = _collect_until_tts_stop(ws)
                types = [p["type"] for p in seen]
                assert "stt" in types
                assert "llm" in types
                assert types.count("tts") >= 3
                stt_msg = next(p for p in seen if p["type"] == "stt")
                assert stt_msg["text"] == "မင်္ဂလာပါ"
                assert brain.begun and brain.ended
                assert brain.pcm_bytes > 0


def test_tts_strips_gemini_control_tags() -> None:
    application = create_app(_settings())
    brain = FakeBrain(output_text="**" + "မင်္ဂလာပါ" + "** 😊 <ctrl46>")
    tts = _FakeTts()

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = tts
            with _open_session(client) as ws:
                _handshake_and_listen(ws)
                seen = _collect_until_tts_stop(ws)
                sentence = next(
                    p for p in seen if p.get("type") == "tts" and p.get("state") == "sentence_start"
                )
                assert sentence["text"] == "မင်္ဂလာပါ"
                assert all("<ctrl46>" not in (p.get("text") or "") for p in seen)
                assert tts.spoken == ["မင်္ဂလာပါ"]


def test_noise_does_not_speak() -> None:
    application = create_app(_settings())
    brain = FakeBrain(output_text="မင်္ဂလာပါ။")

    with patch("app.sessions.session.create_codec", return_value=QuietCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            with _open_session(client) as ws:
                _handshake_and_listen(ws, frames=8)
                import time

                time.sleep(0.25)
                assert application.state.tts.spoken == []
                assert brain.pcm_bytes == 0


def test_transient_disconnect_does_not_speak_sorry() -> None:
    application = create_app(_settings())
    brain = FakeBrain(transient_disconnect=True)
    tts = _FakeTts()

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = tts
            with _open_session(client) as ws:
                _handshake_and_listen(ws)
                import time

                time.sleep(0.25)
                assert tts.spoken == []


def test_forward_cap_ends_turn_without_listen_stop() -> None:
    settings = _settings()
    settings.max_forwarded_audio_seconds = 0.12  # two 60 ms frames
    settings.vad_min_silence_ms = 5000.0
    application = create_app(settings)
    brain = FakeBrain(input_text="မင်္ဂလာပါ", output_text="မင်္ဂလာပါ။")

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            with _open_session(client) as ws:
                _handshake_and_listen(ws, frames=8, send_stop=False)
                seen = _collect_until_tts_stop(ws)
                assert any(p.get("type") == "tts" and p.get("state") == "start" for p in seen)
                assert brain.ended is True
                assert application.state.tts.spoken == ["မင်္ဂလာပါ။"]


def test_hangover_ends_turn_without_listen_stop() -> None:
    settings = _settings()
    settings.vad_min_silence_ms = 60.0
    settings.vad_preroll_chunks = 0
    application = create_app(settings)
    brain = FakeBrain(input_text="မင်္ဂလာပါ", output_text="မင်္ဂလာပါ။")

    with patch(
        "app.sessions.session.create_codec", return_value=SpeechThenQuietCodec(speech_frames=2)
    ), patch("app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            with _open_session(client) as ws:
                _handshake_and_listen(ws, frames=6, send_stop=False)
                seen = _collect_until_tts_stop(ws)
                starts = [p for p in seen if p.get("type") == "tts" and p.get("state") == "start"]
                assert len(starts) == 1
                assert brain.ended is True
                assert application.state.tts.spoken == ["မင်္ဂလာပါ။"]


def test_slow_begin_still_forwards_wake_pcm() -> None:
    """listen/start must not block the receive loop while Gemini connects."""
    application = create_app(_settings())
    brain = FakeBrain(
        input_text="မင်္ဂလာပါ",
        output_text="မင်္ဂလာပါ။",
        begin_delay_s=0.4,
    )

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            with _open_session(client) as ws:
                _handshake_and_listen(ws, frames=5)
                seen = _collect_until_tts_stop(ws, limit=40)
                assert brain.begun is True
                assert brain.pcm_bytes > 0
                assert any(p.get("type") == "tts" and p.get("state") == "stop" for p in seen)
                assert application.state.tts.spoken == ["မင်္ဂလာပါ။"]


def test_tool_only_turn_still_sends_tts_stop() -> None:
    """Hangover tts/start must be paired with tts/stop even if Gemini only calls tools."""
    settings = _settings()
    settings.vad_min_silence_ms = 60.0
    settings.vad_preroll_chunks = 0
    application = create_app(settings)
    brain = FakeBrain(
        input_text="အသံတိုးနေတယ်",
        output_text="",
        calls=[FunctionCall(name="set_emotion", arguments={"emotion": "happy"}, call_id="c1")],
    )

    with patch(
        "app.sessions.session.create_codec", return_value=SpeechThenQuietCodec(speech_frames=2)
    ), patch("app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            with _open_session(client) as ws:
                _handshake_and_listen(ws, frames=6, send_stop=False)
                seen = _collect_until_tts_stop(ws)
                tts_states = [p.get("state") for p in seen if p.get("type") == "tts"]
                assert "start" in tts_states
                assert "stop" in tts_states
                assert brain.function_results
                assert application.state.tts.spoken == []


def test_farewell_exits_after_tts_stop() -> None:
    application = create_app(_settings())
    brain = FakeBrain(
        input_text="goodbye",
        output_text="ဘိုင်း။",
        calls=[
            FunctionCall(
                name="handle_exit_intent",
                arguments={"say_goodbye": "ဘိုင်း"},
                call_id="exit1",
            )
        ],
    )
    tts = _FakeTts()

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = tts
            with _open_session(client) as ws:
                session_id = _handshake_and_listen(ws)
                seen = _collect_until_tts_stop(ws)
                assert any(p.get("type") == "tts" and p.get("state") == "stop" for p in seen)
                assert tts.spoken == ["ဘိုင်း"]

                after_stop = []
                for _ in range(10):
                    message = ws.receive()
                    if message["type"] == "websocket.send" and message.get("bytes"):
                        continue
                    text = message.get("text")
                    if not text:
                        continue
                    payload = json.loads(text)
                    if payload.get("type") == "ping":
                        continue
                    after_stop.append(payload)
                    if payload.get("type") == "abort":
                        break
                assert any(p.get("type") == "abort" for p in after_stop)
                abort_msg = next(p for p in after_stop if p.get("type") == "abort")
                assert abort_msg.get("reason") == "conversation_ended"

                import time

                time.sleep(0.25)
                session = application.state.sessions.get("aa:bb:cc:dd:ee:ff")
                assert session is not None
                assert session.state.state == SessionState.READY
                assert session._awaiting_wake is False
                assert session._live_stale is False
                assert session._companion_lock.locked() is False
                assert brain.conversation_resets == 1
                assert brain.cancelled is False

                ws.send_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "type": "listen",
                            "state": "start",
                            "mode": "auto",
                        }
                    )
                )
                time.sleep(0.15)
                assert session.state.state == SessionState.READY
                assert session._companion_lock.locked() is False

                brain.input_text = "မင်္ဂလာပါ"
                brain.output_text = "မင်္ဂလာပါ။"
                brain.calls = []
                ws.send_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "type": "listen",
                            "state": "detect",
                        }
                    )
                )
                for _ in range(3):
                    ws.send_bytes(b"\x00\x01")
                ws.send_text(
                    json.dumps({"session_id": session_id, "type": "listen", "state": "stop"})
                )
                second = _collect_until_tts_stop(ws)
                assert any(p.get("type") == "tts" and p.get("state") == "stop" for p in second)
                stt_msg = next(p for p in second if p.get("type") == "stt")
                assert stt_msg["text"] == "မင်္ဂလာပါ"
                assert tts.spoken == ["ဘိုင်း", "မင်္ဂလာပါ။"]
                assert brain.conversation_resets == 1
                assert session._awaiting_wake is False

                brain.input_text = "how are you"
                brain.output_text = "ကောင်းပါတယ်။"
                ws.send_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "type": "listen",
                            "state": "start",
                            "mode": "auto",
                        }
                    )
                )
                for _ in range(3):
                    ws.send_bytes(b"\x00\x01")
                ws.send_text(
                    json.dumps({"session_id": session_id, "type": "listen", "state": "stop"})
                )
                third = _collect_until_tts_stop(ws)
                assert any(p.get("type") == "tts" and p.get("state") == "stop" for p in third)
                assert not any(p.get("type") == "abort" for p in third)
                stt_third = next(p for p in third if p.get("type") == "stt")
                assert stt_third["text"] == "how are you"
                assert tts.spoken == ["ဘိုင်း", "မင်္ဂလာပါ။", "ကောင်းပါတယ်။"]
                assert session._awaiting_wake is False
                assert session.state.state == SessionState.READY


def test_music_play_streams_opus_after_announcement() -> None:
    application = create_app(_settings())
    brain = FakeBrain(
        input_text="play never gonna give you up",
        output_text="ကဲ နားထောင်လိုက်မယ်နော်။",
        calls=[
            FunctionCall(
                name="search_music",
                arguments={"query": "never gonna give you up", "play": True},
                call_id="m1",
            )
        ],
    )

    class _StubMusic:
        name = "search_music"

        async def __call__(self, query: str, play: bool = False, **_):
            return {
                "query": query,
                "play_requested": True,
                "playback": "queued",
                "stream_url": "https://audio-ssl.itunes.apple.com/preview.m4a",
                "track": "Never Gonna Give You Up",
                "artist": "Rick Astley",
                "source": "itunes",
                "preview": True,
                "matches": [{"track": "Never Gonna Give You Up", "artist": "Rick Astley"}],
                "note": "announce",
            }

    async def _fake_download(self, url: str) -> bytes:
        assert url.startswith("https://")
        return b"fake-audio"

    async def _fake_media(media: bytes, timeout_s: float = 45.0, max_seconds=None) -> bytes:
        return b"\x00\x00" * DOWNLINK_FRAME_SAMPLES * 3

    async def _fake_music_frames(self, url: str, *, source: str = ""):
        assert url.startswith("https://")
        frame = b"\x00\x00" * DOWNLINK_FRAME_SAMPLES
        for _ in range(3):
            yield frame

    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ), patch(
        "app.sessions.session.DeviceSession._pcm_frames_for_music",
        new=_fake_music_frames,
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            application.state.tool_router.host["search_music"] = _StubMusic()
            with _open_session(client) as ws:
                _handshake_and_listen(ws)
                seen: list[dict] = []
                audio_frames = 0
                for _ in range(60):
                    message = ws.receive()
                    if message["type"] == "websocket.send" and message.get("bytes"):
                        audio_frames += 1
                        continue
                    text = message.get("text")
                    if not text:
                        continue
                    payload = json.loads(text)
                    if payload.get("type") == "ping":
                        continue
                    seen.append(payload)
                    if payload.get("type") == "tts" and payload.get("state") == "stop":
                        break
                labels = [
                    p.get("text")
                    for p in seen
                    if p.get("type") == "tts" and p.get("state") == "sentence_start"
                ]
                assert any("Never Gonna Give You Up" in (label or "") for label in labels)
                assert any("ပြီးသွား" in (label or "") for label in labels)
                assert audio_frames >= 3
                assert brain.function_results
                assert brain.music_finished == {
                    "track": "Never Gonna Give You Up",
                    "artist": "Rick Astley",
                    "status": "completed",
                }


def test_myanmar_play_request_injects_search_music() -> None:
    application = create_app(_settings())
    brain = FakeBrain(
        input_text="Hello Simbiaတုန်း မြန်မာ သီချင်းဖွင့်မရဘူးလား?",
        output_text="ကဲ နားထောင်လိုက်မယ်နော်။",
        calls=[FunctionCall(name="set_emotion", arguments={"emotion": "happy"}, call_id="e1")],
    )

    class _StubMusic:
        name = "search_music"
        queries: list[str] = []

        async def __call__(self, query: str, play: bool = False, **_):
            self.queries.append(query)
            return {
                "query": query,
                "play_requested": True,
                "playback": "queued",
                "stream_url": "https://audio-ssl.itunes.apple.com/preview.m4a",
                "track": "Myanmar Song",
                "artist": "Artist",
                "source": "itunes",
                "preview": True,
                "matches": [{"track": "Myanmar Song", "artist": "Artist"}],
                "note": "announce",
            }

    async def _fake_music_frames(self, url: str, *, source: str = ""):
        frame = b"\x00\x00" * DOWNLINK_FRAME_SAMPLES
        for _ in range(3):
            yield frame

    stub = _StubMusic()
    with patch("app.sessions.session.create_codec", return_value=SilentCodec()), patch(
        "app.sessions.session.mp3_to_pcm24k", new=AsyncMock(side_effect=_fake_pcm)
    ), patch(
        "app.sessions.session.DeviceSession._pcm_frames_for_music",
        new=_fake_music_frames,
    ):
        with TestClient(application) as client:
            application.state.brain_factory = lambda: brain
            application.state.tts = _FakeTts()
            application.state.tool_router.host["search_music"] = stub
            with _open_session(client) as ws:
                _handshake_and_listen(ws)
                seen = _collect_until_tts_stop(ws, limit=40)
                labels = [
                    p.get("text")
                    for p in seen
                    if p.get("type") == "tts" and p.get("state") == "sentence_start"
                ]
                assert stub.queries == ["Myanmar song"]
                assert any("Myanmar Song" in (label or "") for label in labels)
                names = [item["name"] for item in brain.function_results]
                assert "search_music" in names


class _FakeTts:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        return b"fake"
