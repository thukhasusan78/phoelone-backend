from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.tools.music import (
    MusicTool,
    device_music_call,
    is_music_play_request,
    is_youtube_playback,
    music_payload_for_llm,
    music_search_query,
    relevance_score,
    ytdlp_stream_cmd,
)
from tests.unit.test_tools import DummyHttp


def _settings(**kwargs) -> Settings:
    kwargs.setdefault("database_url", "memory://")
    kwargs.setdefault("music_ytdlp_enabled", False)
    kwargs.setdefault("music_local_dir", "")
    return Settings(**kwargs)


async def test_search_only_omits_stream_queue() -> None:
    music = MusicTool(DummyHttp(), _settings())  # type: ignore[arg-type]
    result = await music(query="Artist", play=False)
    assert result["playback"] == "search_only"
    assert "stream_url" not in result
    assert result["matches"][0]["track"] == "Song"


async def test_play_without_preview_is_unavailable() -> None:
    class NoPreview(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "itunes" in url:
                return {"results": [{"trackName": "Song", "artistName": "Artist"}]}
            return await super().get_json(url, params, headers)

    music = MusicTool(NoPreview(), _settings())  # type: ignore[arg-type]
    result = await music(query="Artist", play=True)
    assert result["playback"] == "unavailable"
    assert "stream_url" not in result


async def test_deezer_fills_in_when_itunes_empty() -> None:
    class DeezerOnly(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "itunes" in url:
                return {"results": []}
            if "api.deezer.com/search" in url:
                return {
                    "data": [
                        {
                            "title": "Hello",
                            "preview": "https://cdnt-preview.dzcdn.net/hello.mp3",
                            "artist": {"name": "Adele"},
                            "album": {"title": "25", "cover_medium": "https://example.com/c.jpg"},
                        }
                    ]
                }
            return await super().get_json(url, params, headers)

    music = MusicTool(DeezerOnly(), _settings())  # type: ignore[arg-type]
    result = await music(query="hello", play=True)
    assert result["playback"] == "queued"
    assert result["source"] == "deezer"
    assert result["track"] == "Hello"
    assert result["preview"] is True


async def test_jamendo_full_track_preferred_over_preview() -> None:
    class Both(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "itunes" in url:
                return {
                    "results": [
                        {
                            "trackName": "Sunset",
                            "artistName": "Band",
                            "previewUrl": "https://audio-ssl.itunes.apple.com/p.m4a",
                        }
                    ]
                }
            if "jamendo.com" in url:
                return {
                    "results": [
                        {
                            "name": "Sunset",
                            "artist_name": "Band",
                            "audio": "https://prod-1.storage.jamendo.com/download/track/1/mp32/",
                            "duration": 180,
                        }
                    ]
                }
            return await super().get_json(url, params, headers)

    settings = _settings(jamendo_client_id="cid")
    music = MusicTool(Both(), settings)  # type: ignore[arg-type]
    result = await music(query="sunset", play=True)
    assert result["source"] == "jamendo"
    assert result["preview"] is False


async def test_unrelated_full_track_not_preferred_for_myanmar_query() -> None:
    """Do not play random Jamendo full tracks when the query is Myanmar song."""

    class Mixed(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "itunes" in url:
                return {
                    "results": [
                        {
                            "trackName": "Tayat Tot Ngo Par",
                            "artistName": "Aye Myat Nandar",
                            "previewUrl": "https://audio-ssl.itunes.apple.com/p.m4a",
                        }
                    ]
                }
            if "jamendo.com" in url:
                return {
                    "results": [
                        {
                            "name": "River Morning",
                            "artist_name": "Open Music",
                            "audio": "https://prod-1.storage.jamendo.com/download/track/9/mp32/",
                            "duration": 180,
                        }
                    ]
                }
            return await super().get_json(url, params, headers)

    settings = _settings(jamendo_client_id="cid")
    music = MusicTool(Mixed(), settings)  # type: ignore[arg-type]
    result = await music(query="Myanmar song", play=True)
    assert result["playback"] == "unavailable"
    assert "stream_url" not in result


async def test_generic_play_a_song_skips_remote_apis() -> None:
    class TrackingHttp(DummyHttp):
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get_json(self, url: str, params=None, headers=None):
            self.urls.append(url)
            return await super().get_json(url, params, headers)

    http = TrackingHttp()
    music = MusicTool(http, _settings())  # type: ignore[arg-type]
    result = await music(query="play a song", play=True)
    assert result["playback"] == "unavailable"
    assert http.urls == []


async def test_bukjoe_substring_does_not_queue_and_falls_to_ytdlp(monkeypatch) -> None:
    class BukJoeHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "jamendo.com" in url:
                return {
                    "results": [
                        {
                            "name": "Reality Check Snippet",
                            "artist_name": "BukJoe858",
                            "audio": "https://prod-1.storage.jamendo.com/download/track/9/mp32/",
                            "duration": 40,
                        }
                    ]
                }
            if url.rstrip("/") == "https://api.audius.co":
                return {"data": ["https://discovery.audius.co"]}
            return {"results": [], "data": []}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (
                b"Joe Lay - Nay Yaung Pyauk Tae Nway\nJoe Lay\n210\n"
                b"https://www.youtube.com/watch?v=abc123\n"
            ), b""

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    settings = _settings(jamendo_client_id="cid", music_ytdlp_enabled=True)
    music = MusicTool(BukJoeHttp(), settings)  # type: ignore[arg-type]
    result = await music(query="Joe Lay နေရောင်ပျောက်တဲ့နွေ", play=True)
    assert result["playback"] == "queued"
    assert result["source"] == "youtube"
    assert result["artist"] == "Joe Lay"


async def test_bukjoe_without_ytdlp_is_unavailable() -> None:
    class BukJoeHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "jamendo.com" in url:
                return {
                    "results": [
                        {
                            "name": "Reality Check Snippet",
                            "artist_name": "BukJoe858",
                            "audio": "https://prod-1.storage.jamendo.com/download/track/9/mp32/",
                            "duration": 40,
                        }
                    ]
                }
            if url.rstrip("/") == "https://api.audius.co":
                return {"data": ["https://discovery.audius.co"]}
            return {"results": [], "data": []}

    music = MusicTool(BukJoeHttp(), _settings(jamendo_client_id="cid"))  # type: ignore[arg-type]
    result = await music(query="Joe Lay နေရောင်ပျောက်တဲ့နွေ", play=True)
    assert result["playback"] == "unavailable"
    assert "stream_url" not in result


async def test_joe_lay_unrelated_jamendo_unavailable() -> None:
    class JoeLayHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "jamendo.com" in url:
                return {
                    "results": [
                        {
                            "name": "Mil palabras",
                            "artist_name": "C.Muela",
                            "audio": "https://prod-1.storage.jamendo.com/download/track/404340/mp32/",
                            "duration": 200,
                        }
                    ]
                }
            if "itunes" in url or "deezer" in url or "audius" in url:
                return {"results": [], "data": []}
            return await super().get_json(url, params, headers)

    settings = _settings(jamendo_client_id="cid")
    music = MusicTool(JoeLayHttp(), settings)  # type: ignore[arg-type]
    result = await music(query="Joe Lay", play=True)
    assert result["playback"] == "unavailable"


async def test_local_catalog_wins_for_joe_lay(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        """
        {"tracks": [{
          "track": "ချစ်တဲ့သူ",
          "artist": "Joe Lay",
          "aliases": ["ဂျိုးလေး", "Joe Lay"],
          "stream_url": "https://cdn.example.com/joe-lay.mp3",
          "duration_s": 200
        }]}
        """,
        encoding="utf-8",
    )
    settings = _settings(
        jamendo_client_id="cid",
        music_catalog_path=str(catalog),
        music_local_dir="",
    )
    music = MusicTool(DummyHttp(), settings)  # type: ignore[arg-type]
    result = await music(query="ဂျိုးလေး သီချင်း", play=True)
    assert result["playback"] == "queued"
    assert result["source"] == "catalog"
    assert result["artist"] == "Joe Lay"
    assert result["stream_url"] == "https://cdn.example.com/joe-lay.mp3"


async def test_ytdlp_last_resort(monkeypatch) -> None:
    settings = _settings(
        jamendo_client_id="",
        music_ytdlp_enabled=True,
        music_ytdlp_bin="yt-dlp",
    )
    music = MusicTool(DummyHttp(), settings)  # type: ignore[arg-type]

    class FakeProc:
        returncode = 0

        async def communicate(self):
            out = (
                b"Joe Lay - Hit Song\n"
                b"Joe Lay\n"
                b"210\n"
                b"https://www.youtube.com/watch?v=abc123\n"
            )
            return out, b""

        def kill(self):
            return None

        async def wait(self):
            return 0

    class EmptyHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "audius.co" == url.rstrip("/").split("/")[-1] or url.rstrip("/") == "https://api.audius.co":
                return {"data": ["https://discovery.audius.co"]}
            return {"results": [], "data": []}

    music = MusicTool(EmptyHttp(), settings)  # type: ignore[arg-type]
    captured: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    result = await music(query="Joe Lay", play=True)
    assert result["playback"] == "queued"
    assert result["source"] == "youtube"
    assert "youtube.com/watch" in result["stream_url"]
    assert any("--extractor-args" in a for a in captured[0])
    joined = " ".join(str(a) for a in captured[0])
    assert "player_client=android,ios,tv" in joined
    assert "--skip-download" in captured[0]


async def test_itunes_preview_does_not_block_ytdlp(monkeypatch) -> None:
    class PreviewHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "itunes" in url:
                return {
                    "results": [
                        {
                            "trackName": "Rangdi Julie",
                            "artistName": "Suraj Lovely & Sweta Singh",
                            "previewUrl": "https://audio-ssl.itunes.apple.com/p.m4a",
                        }
                    ]
                }
            if url.rstrip("/") == "https://api.audius.co":
                return {"data": ["https://discovery.audius.co"]}
            return {"results": [], "data": []}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (
                b"Joe Lay - Nay Yaung\nJoe Lay\n200\n"
                b"https://www.youtube.com/watch?v=def456\n"
            ), b""

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    music = MusicTool(
        PreviewHttp(),
        _settings(jamendo_client_id="", music_ytdlp_enabled=True),
    )  # type: ignore[arg-type]
    result = await music(query="Joe Lay", play=True)
    assert result["source"] == "youtube"


async def test_audius_full_track_used_when_relevant() -> None:
    class AudiusHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if url.rstrip("/") == "https://api.audius.co":
                return {"data": ["https://discovery.audius.co"]}
            if "itunes" in url:
                return {
                    "results": [
                        {
                            "trackName": "Clip",
                            "artistName": "Pop",
                            "previewUrl": "https://audio-ssl.itunes.apple.com/p.m4a",
                        }
                    ]
                }
            if "audius.co/v1/tracks/search" in url:
                return {
                    "data": [
                        {
                            "id": "abc123",
                            "title": "Long Tune",
                            "user": {"name": "Indie"},
                            "duration": 200,
                        }
                    ]
                }
            return await super().get_json(url, params, headers)

    music = MusicTool(AudiusHttp(), _settings())  # type: ignore[arg-type]
    result = await music(query="Long Tune", play=True)
    assert result["source"] == "audius"
    assert result["preview"] is False
    assert result["track"] == "Long Tune"
    assert "/tracks/abc123/stream" in result["stream_url"]


def test_llm_payload_strips_stream_url() -> None:
    cleaned = music_payload_for_llm(
        {"playback": "queued", "stream_url": "https://example.com/a.m4a", "track": "Song"}
    )
    assert "stream_url" not in cleaned
    assert cleaned["track"] == "Song"


def test_device_music_call_maps_online_player() -> None:
    tools = {
        "self.online_music.play_music": {
            "name": "self.online_music.play_music",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "song_name": {"type": "string"}},
                "required": ["url"],
            },
        }
    }
    mapped = device_music_call(
        tools,
        {"stream_url": "https://example.com/a.mp3", "track": "Song", "artist": "Artist"},
    )
    assert mapped is not None
    name, args = mapped
    assert name == "self.online_music.play_music"
    assert args["url"] == "https://example.com/a.mp3"
    assert args["song_name"] == "Song"


def test_device_music_call_maps_play_song() -> None:
    tools = {
        "self.music.play_song": {
            "name": "self.music.play_song",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "song_name": {"type": "string"},
                    "artist_name": {"type": "string"},
                },
                "required": ["song_name"],
            },
        }
    }
    mapped = device_music_call(tools, {"track": "Song", "artist": "Artist", "stream_url": "https://x"})
    assert mapped == ("self.music.play_song", {"song_name": "Song", "artist_name": "Artist"})


def test_myanmar_play_request_is_detected() -> None:
    assert is_music_play_request("Hello Simbiaတုန်း မြန်မာ သီချင်းဖွင့်မရဘူးလား?")
    assert is_music_play_request("play a song")
    assert is_music_play_request("သီချင်း ဖွင့်ပါ")
    assert not is_music_play_request("သီချင်း ရပ်လိုက်")
    assert not is_music_play_request("ရာသီဥတု ဘယ်လိုလဲ")
    assert music_search_query("Hello Simbiaတုန်း မြန်မာ သီချင်းဖွင့်မရဘူးလား?") == "Myanmar song"
    assert music_search_query("play a song") == "Myanmar song"
    assert music_search_query("play some music") == "Myanmar song"
    assert music_search_query("သီချင်း ဖွင့်ပါ") == "Myanmar song"
    assert music_search_query("play never gonna give you up") == "never gonna give you up"
    assert music_search_query("hey Mickey play a song") == "Myanmar song"
    assert "ဂျိုးလေး" in music_search_query("ဂျိုးလေး သီချင်းဖွင့်ပြ ဂျိုးလေး")


def test_relevance_score_rejects_unrelated() -> None:
    assert relevance_score("Joe Lay", "Mil palabras", "C.Muela") == 0.0
    assert relevance_score("Joe Lay", "Joe Lay Hit", "Joe Lay") >= 0.6
    assert relevance_score("Myanmar song", "River Morning", "Open Music") < 0.5
    assert (
        relevance_score(
            "Joe Lay နေရောင်ပျောက်တဲ့နွေ",
            "Reality Check Snippet",
            "BukJoe858",
        )
        == 0.0
    )
    assert relevance_score("Joe Lay", "Something", "Joe Walsh") == 0.0


def test_youtube_playback_pipes_watch_url() -> None:
    assert is_youtube_playback("https://www.youtube.com/watch?v=abc", "youtube")
    assert is_youtube_playback("https://youtu.be/abc", "")
    assert is_youtube_playback(
        "https://rr4---sn-npoeenl7.googlevideo.com/videoplayback?id=1",
        "",
    )
    assert not is_youtube_playback("https://cdn.example.com/a.mp3", "jamendo")
    cmd = ytdlp_stream_cmd(
        "https://www.youtube.com/watch?v=abc",
        _settings(music_ytdlp_enabled=True),
    )
    assert cmd[cmd.index("-o") + 1] == "-"
    assert "-g" not in cmd
    assert "--skip-download" not in cmd
    assert "--no-progress" in cmd


def test_ytdlp_env_bool_parsing() -> None:
    assert Settings.parse_ytdlp_enabled("true") is True
    assert Settings.parse_ytdlp_enabled("True") is True
    assert Settings.parse_ytdlp_enabled("1") is True
    assert Settings.parse_ytdlp_enabled("YES") is True
    assert Settings.parse_ytdlp_enabled("false") is False
    assert Settings.parse_ytdlp_enabled("0") is False
    assert Settings.parse_ytdlp_enabled("  True  ") is True


def test_music_stream_pipe_friendly() -> None:
    from app.sessions.session import _ffmpeg_input_format, _music_stream_pipe_friendly

    mp3_head = b"ID3" + b"\x00" * 16
    assert _music_stream_pipe_friendly("audio/mpeg", mp3_head) is True
    assert _ffmpeg_input_format("audio/mpeg", mp3_head) == "mp3"
    m4a_head = b"\x00\x00\x00\x18ftypM4A "
    assert _music_stream_pipe_friendly("audio/mp4", m4a_head) is False
    assert _music_stream_pipe_friendly("application/octet-stream", m4a_head) is False
    assert _music_stream_pipe_friendly("application/octet-stream", mp3_head) is True
