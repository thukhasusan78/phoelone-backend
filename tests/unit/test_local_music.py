from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from app.config import Settings
from app.tools.local_music import (
    invalidate_local_music_cache,
    music_local_root,
    parse_local_filename,
    resolve_local_music_path,
    scan_local_music,
)
from app.tools.music import MusicTool, device_music_call
from tests.unit.test_tools import DummyHttp


def _settings(**kwargs) -> Settings:
    kwargs.setdefault("database_url", "memory://")
    kwargs.setdefault("music_ytdlp_enabled", False)
    kwargs.setdefault("music_catalog_path", "")
    return Settings(**kwargs)


def _write_wav(path: Path, *, seconds: float = 0.06) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000
    frames = int(sample_rate * seconds)
    pcm = b"\x00\x01" * frames
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def test_parse_artist_title_latin() -> None:
    artist, title = parse_local_filename(Path("Joe Lay - Chit Tae Thu.mp3"))
    assert artist == "Joe Lay"
    assert title == "Chit Tae Thu"


def test_parse_artist_title_burmese() -> None:
    artist, title = parse_local_filename(Path("ဂျိုးလေး - နေရောင်ပျောက်တဲ့နွေ.mp3"))
    assert artist == "ဂျိုးလေး"
    assert title == "နေရောင်ပျောက်တဲ့နွေ"


def test_parse_underscores() -> None:
    artist, title = parse_local_filename(Path("Joe_Lay_-_Chit_Tae_Thu.mp3"))
    assert artist == "Joe Lay"
    assert title == "Chit Tae Thu"


def test_parse_track_number_uses_folder_artist(tmp_path: Path) -> None:
    root = tmp_path / "Joe Lay"
    root.mkdir()
    path = root / "01 - Intro.mp3"
    artist, title = parse_local_filename(path, root=tmp_path)
    assert artist == "Joe Lay"
    assert title == "Intro"


def test_parse_title_only_uses_folder_artist(tmp_path: Path) -> None:
    root = tmp_path / "Joe Lay"
    root.mkdir()
    path = root / "Chit Tae Thu.mp3"
    artist, title = parse_local_filename(path, root=tmp_path)
    assert artist == "Joe Lay"
    assert title == "Chit Tae Thu"


def test_scan_discovers_files(tmp_path: Path) -> None:
    invalidate_local_music_cache()
    _write_wav(tmp_path / "Joe Lay - Chit Tae Thu.wav")
    _write_wav(tmp_path / "ဂျိုးလေး - နေရောင်ပျောက်တဲ့နွေ.wav")
    tracks = scan_local_music(tmp_path, force=True)
    assert len(tracks) == 2
    titles = {t.title for t in tracks}
    assert "Chit Tae Thu" in titles
    assert "နေရောင်ပျောက်တဲ့နွေ" in titles


def test_scan_ignores_hidden_files(tmp_path: Path) -> None:
    invalidate_local_music_cache()
    _write_wav(tmp_path / "Joe Lay - Visible.wav")
    _write_wav(tmp_path / ".hidden - Secret.wav")
    tracks = scan_local_music(tmp_path, force=True)
    assert len(tracks) == 1
    assert tracks[0].title == "Visible"


def test_resolve_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    _write_wav(outside)
    assert resolve_local_music_path(root, outside) is None
    assert resolve_local_music_path(root, "../outside.wav") is None


async def test_generic_query_queues_local_file(tmp_path: Path) -> None:
    invalidate_local_music_cache()
    _write_wav(tmp_path / "Joe Lay - Chit Tae Thu.wav")
    _write_wav(tmp_path / "Other Artist - Other Song.wav")
    music = MusicTool(
        DummyHttp(),
        _settings(music_local_dir=str(tmp_path)),
    )  # type: ignore[arg-type]
    for query in ("Myanmar song", "play a song", "song", "သီချင်း ဖွင့်ပါ"):
        result = await music(query=query, play=True)
        assert result["playback"] == "queued", query
        assert result["source"] == "local", query
        assert Path(result["stream_url"]).is_file()


async def test_specific_joe_lay_matches_local(tmp_path: Path) -> None:
    invalidate_local_music_cache()
    _write_wav(tmp_path / "Joe Lay - Chit Tae Thu.wav")
    _write_wav(tmp_path / "Other Artist - Other Song.wav")
    music = MusicTool(
        DummyHttp(),
        _settings(music_local_dir=str(tmp_path)),
    )  # type: ignore[arg-type]
    result = await music(query="Joe Lay", play=True)
    assert result["playback"] == "queued"
    assert result["source"] == "local"
    assert result["track"] == "Chit Tae Thu"
    assert result["artist"] == "Joe Lay"


async def test_specific_burmese_artist_matches_local(tmp_path: Path) -> None:
    invalidate_local_music_cache()
    _write_wav(tmp_path / "ဂျိုးလေး - နေရောင်ပျောက်တဲ့နွေ.wav")
    music = MusicTool(
        DummyHttp(),
        _settings(music_local_dir=str(tmp_path)),
    )  # type: ignore[arg-type]
    result = await music(query="ဂျိုးလေး", play=True)
    assert result["playback"] == "queued"
    assert result["source"] == "local"
    assert result["artist"] == "ဂျိုးလေး"


def test_device_music_call_skips_local() -> None:
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
        {
            "stream_url": "/data/local_music/song.mp3",
            "track": "Song",
            "artist": "Artist",
            "source": "local",
        },
    )
    assert mapped is None


def test_music_local_root_relative() -> None:
    root = music_local_root("data/local_music")
    assert root is not None
    assert root.name == "local_music"


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg"),
    reason="ffmpeg required",
)
async def test_iter_pcm_frames_from_file(tmp_path: Path) -> None:
    from app.audio.opus import DOWNLINK_FRAME_SAMPLES, iter_pcm_frames_from_file

    wav = tmp_path / "Joe Lay - Test.wav"
    _write_wav(wav, seconds=0.36)
    capped = [frame async for frame in iter_pcm_frames_from_file(wav, max_seconds=0.12)]
    full = [frame async for frame in iter_pcm_frames_from_file(wav, max_seconds=None)]
    assert len(capped) >= 1
    assert len(capped[0]) == DOWNLINK_FRAME_SAMPLES * 2
    assert len(full) > len(capped)
