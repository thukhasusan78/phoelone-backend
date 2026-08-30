from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import Settings
from app.observability.logging import get_logger
from app.tools.http import SafeHttp
from app.tools.local_music import LocalTrack, music_local_root, scan_local_music

log = get_logger(__name__)

DEVICE_PLAY_TOOLS = (
    "self.online_music.play_music",
    "self.music.play_song",
)

_SONG_HINTS = (
    "သီချင်း",
    "တေးသီချင်း",
    "တေးဂီတ",
    "ဂီတ",
    "music",
    "song",
    "songs",
)
_PLAY_VERBS = ("ဖွင့်", "နားထောင်", "play", "playing", "တီး")
_STOP_HINTS = ("ရပ်", "stop", "ပိတ်")
_GENERIC_MYANMAR = (
    "မြန်မာ",
    "myanmar",
    "burmese",
    "myanmar song",
    "myanmar songs",
    "myanmar music",
    "burmese song",
    "burmese music",
    "မြန်မာသီချင်း",
    "မြန်မာ သီချင်း",
)
_MYANMAR_FALLBACK_QUERIES = ("Myanmar song", "Burmese music")
_TOKEN_RE = re.compile(r"[\u1000-\u109Fa-z0-9]+", re.IGNORECASE)
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "googlevideo.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
_GENERIC_TOKENS = frozenset(
    {"song", "songs", "music", "myanmar", "burmese", "မြန်မာ", "သီချင်း"}
)
_GENERIC_FILLERS = frozenset(
    {
        "a",
        "an",
        "the",
        "some",
        "any",
        "please",
        "me",
        "my",
        "play",
        "playing",
        "listen",
        "something",
    }
)


def is_music_play_request(text: str) -> bool:
    """True when the user is asking to hear music, including 'can't you play songs?'."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = raw.casefold()
    compact = re.sub(r"\s+", "", folded)
    has_song = any(hint in raw or hint in folded for hint in _SONG_HINTS)
    has_play = any(verb in raw or verb in folded for verb in _PLAY_VERBS)
    if not (has_song and has_play):
        return False
    if any(stop in raw or stop in folded for stop in _STOP_HINTS):
        if "ဖွင့်မရ" in compact or "can't" in folded or "cannot" in folded:
            return True
        return False
    return True


def music_search_query(text: str) -> str:
    """Turn a spoken play request into a catalog search string."""
    cleaned = (text or "").strip()
    cleaned = re.sub(
        r"(hello|hi|hey|simbia|ဖိုးလုန်း|mickey|မစ်ကီ|ဟယ်လို)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"(မရဘူးလား|မရဘူး|ရမလား|လား|ဗျာ|ခင်ဗျာ)", " ", cleaned)
    # Strip play verbs / song nouns so "ဂျိုးလေး သီချင်းဖွင့်ပြ" → "ဂျိုးလေး".
    cleaned = re.sub(
        r"(သီချင်း|တေးသီချင်း|တေးဂီတ|ဂီတ|music|songs?)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(ဖွင့်ပြ|ဖွင့်ပါ|ဖွင့်|နားထောင်|play(?:ing)?|တီး)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip()
    folded = cleaned.casefold()
    compact = re.sub(r"\s+", "", folded)
    generic = any(token in folded or token in compact for token in _GENERIC_MYANMAR)
    leftover = _tokens(cleaned)
    distinctive = {
        t for t in leftover if t not in _GENERIC_TOKENS and t not in _GENERIC_FILLERS and len(t) >= 2
    }
    if generic or not cleaned or not distinctive:
        return "Myanmar song"
    return cleaned


def _expand_queries(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return list(_MYANMAR_FALLBACK_QUERIES)
    queries = [q]
    # Named artists (Joe Lay + Burmese title) must not be diluted with "Myanmar song".
    ascii_names = [w for w in _ascii_words(q) if w not in _GENERIC_TOKENS and len(w) >= 3]
    if len(ascii_names) >= 1:
        return queries
    if _is_generic_myanmar_query(q) or re.search(r"[\u1000-\u109F]", q):
        for extra in _MYANMAR_FALLBACK_QUERIES:
            if extra.casefold() not in {item.casefold() for item in queries}:
                queries.append(extra)
    return queries[:3]


def _tokens(text: str) -> set[str]:
    return {m.group(0).casefold() for m in _TOKEN_RE.finditer(text or "") if m.group(0)}


def is_youtube_playback(url: str, source: str = "") -> bool:
    """True when audio must be fetched by yt-dlp, not a plain HTTP GET."""
    if (source or "").casefold() == "youtube":
        return True
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    if host in _YOUTUBE_HOSTS:
        return True
    return any(host.endswith(f".{h}") for h in ("googlevideo.com", "youtube.com"))


def _ytdlp_auth_args(settings: Settings) -> list[str]:
    clients = (settings.music_ytdlp_player_clients or "android,ios,tv").replace(" ", "")
    args = ["--extractor-args", f"youtube:player_client={clients}"]
    cookies = (settings.music_ytdlp_cookies or "").strip()
    if cookies:
        cookie_path = Path(cookies)
        if cookie_path.is_file():
            args.extend(["--cookies", str(cookie_path)])
        else:
            log.warning("music.ytdlp_cookies_missing", path=cookies)
    return args


def ytdlp_stream_cmd(url: str, settings: Settings) -> list[str]:
    """Download/stream a YouTube watch URL through yt-dlp stdout (no googlevideo handoff)."""
    bin_path = (settings.music_ytdlp_bin or "yt-dlp").strip() or "yt-dlp"
    return [
        bin_path,
        url,
        "-f",
        "bestaudio[ext=webm]/bestaudio[acodec=opus]/bestaudio/best",
        "-o",
        "-",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        *_ytdlp_auth_args(settings),
    ]


def _ascii_words(text: str) -> list[str]:
    """Whitespace/punctuation-delimited ASCII words (not substrings of compounds)."""
    return [m.group(0).casefold() for m in _ASCII_WORD_RE.finditer(text or "")]


def relevance_score(query: str, track: str, artist: str = "", *extra: str) -> float:
    """
    Token-overlap score in [0, 1] between the search query and track metadata.

    ASCII names match only as whole words: "Joe" matches "Joe Lay" but not
    "BukJoe858". Queries with two or more distinctive Latin name tokens require
    at least two whole-word hits so a lone "Joe" cannot queue a random track.
    """
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    focused = q_tokens - _GENERIC_TOKENS
    use = focused if focused else q_tokens
    hay_text = " ".join([track, artist, *extra])
    hay_ascii = set(_ascii_words(hay_text))
    hay_all = _tokens(hay_text)
    if not hay_all and not hay_ascii:
        return 0.0

    ascii_q = [t for t in use if t.isascii() and t.isalnum()]
    other_q = [t for t in use if t not in set(ascii_q)]
    ascii_matched = 0
    other_matched = 0
    for token in ascii_q:
        if token in hay_ascii:
            ascii_matched += 1
    for token in other_q:
        if token in hay_all:
            other_matched += 1
            continue
        # Compact Burmese has no spaces; allow containment only for long script tokens.
        if len(token) >= 4 and any(
            (token in h or h in token) for h in hay_all if len(h) >= 4 and not h.isascii()
        ):
            other_matched += 1

    distinctive_ascii = [t for t in ascii_q if len(t) >= 3]
    if len(distinctive_ascii) >= 2 and ascii_matched < 2:
        return 0.0

    matched = ascii_matched + other_matched
    if matched == 0:
        return 0.0
    return matched / len(use)


def _is_generic_myanmar_query(query: str) -> bool:
    folded = (query or "").casefold()
    compact = re.sub(r"\s+", "", folded)
    return any(token in folded or token in compact for token in _GENERIC_MYANMAR)


def _is_generic_music_query(query: str) -> bool:
    """True when the user did not name a specific title or artist."""
    q = (query or "").strip()
    if not q:
        return True
    if _is_generic_myanmar_query(q):
        return True
    stripped = re.sub(
        r"(သီချင်း|တေးသီချင်း|တေးဂီတ|ဂီတ|music|songs?)",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(
        r"(ဖွင့်ပြ|ဖွင့်ပါ|ဖွင့်|နားထောင်|play(?:ing)?|တီး)",
        " ",
        stripped,
        flags=re.IGNORECASE,
    )
    leftover = _tokens(stripped)
    distinctive = {
        t for t in leftover if t not in _GENERIC_TOKENS and t not in _GENERIC_FILLERS and len(t) >= 2
    }
    return not distinctive


@dataclass(frozen=True)
class MusicMatch:
    track: str
    artist: str
    album: str | None
    stream_url: str | None
    source: str
    duration_s: float | None
    preview: bool
    artwork_url: str | None = None
    score: float = 0.0

    def as_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "track": self.track,
            "artist": self.artist,
            "album": self.album,
            "source": self.source,
            "preview": self.preview,
        }
        if self.duration_s is not None:
            payload["duration_s"] = round(self.duration_s, 1)
        if self.score:
            payload["score"] = round(self.score, 2)
        return payload


class MusicTool:
    name = "search_music"
    declaration = {
        "name": "search_music",
        "description": (
            "Search for a song and play it on the robot speaker. "
            "ALWAYS call this with play=true when the user asks to hear music, "
            "including Myanmar/Burmese songs, a named title, or "
            "'can't you play songs?'. "
            "Prefer the artist/title the user named (e.g. Joe Lay). "
            "If they did not name a title or artist (e.g. 'play a song'), "
            "use query 'Myanmar song'. "
            "Never say music is unsupported. "
            "The server streams audio after you announce the title; "
            "do not hum, sing, or invent lyrics. "
            "If playback=unavailable, say you could not find that song — "
            "do not announce a different foreign track."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Song, artist, or album name"},
                "play": {
                    "type": "boolean",
                    "description": "True to play the best match on the speaker",
                },
            },
            "required": ["query"],
        },
    }

    def __init__(self, http: SafeHttp, settings: Settings | None = None) -> None:
        self.http = http
        self.settings = settings or Settings()
        self._catalog: list[dict[str, Any]] | None = None
        self._ytdlp_lock = asyncio.Lock()
        self._log_ytdlp_config()

    def _log_ytdlp_config(self) -> None:
        enabled = bool(self.settings.music_ytdlp_enabled)
        bin_path = (self.settings.music_ytdlp_bin or "yt-dlp").strip() or "yt-dlp"
        resolved = shutil.which(bin_path) if enabled else None
        log.info(
            "music.ytdlp_config",
            enabled=enabled,
            bin=bin_path,
            resolved=resolved,
        )
        if enabled and not resolved:
            log.warning(
                "music.ytdlp_unavailable",
                bin=bin_path,
                hint="Install yt-dlp on PATH or set MUSIC_YTDLP_BIN",
            )

    async def __call__(self, query: str, play: bool = False, **_: Any) -> dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {
                "query": query,
                "play_requested": bool(play),
                "matches": [],
                "playback": "unavailable",
                "note": "Need a song or artist name.",
            }
        matches = await self._search(q)
        chosen = _pick_playable(matches, q, self.settings.music_relevance_threshold) if play else None
        playback = "search_only"
        note = "Describe the match in one short Burmese sentence. Do not play or hum."
        if play:
            if chosen is not None:
                playback = "queued"
                kind = "a short preview" if chosen.preview else "the track"
                note = (
                    f"Announce that you will play {kind} of "
                    f"{chosen.track} by {chosen.artist}. "
                    "The server streams the audio after you speak. "
                    "Do not hum, sing, or read a URL."
                )
            else:
                playback = "unavailable"
                note = (
                    "No relevant playable stream was found. Say you could not find "
                    "that song. Do not announce a different foreign track."
                )
        result: dict[str, Any] = {
            "query": q,
            "play_requested": bool(play),
            "playback": playback,
            "matches": [m.as_public_dict() for m in matches[:3]],
            "note": note,
        }
        if chosen is not None:
            result["stream_url"] = chosen.stream_url
            result["track"] = chosen.track
            result["artist"] = chosen.artist
            result["source"] = chosen.source
            result["preview"] = chosen.preview
            if chosen.duration_s is not None:
                result["duration_s"] = round(chosen.duration_s, 1)
        return result

    async def _search(self, query: str) -> list[MusicMatch]:
        # Layer A: auto-discovered local files + optional HTTPS catalog overlay.
        local = self._search_local(query)
        if local:
            log.info(
                "music.search",
                query=query,
                full=len(local),
                preview=0,
                sources=sorted({m.source for m in local}),
            )
            return local
        # Generic "play a song" never falls through to iTunes/YouTube.
        if _is_generic_music_query(query):
            log.info(
                "music.search",
                query=query,
                full=0,
                preview=0,
                sources=[],
                reason="generic_local_only",
            )
            return []

        queries = _expand_queries(query)
        tasks: list[asyncio.Task] = []
        queried: set[str] = {"itunes", "deezer", "audius"}
        for q in queries:
            tasks.append(asyncio.create_task(self._search_itunes(q), name=f"itunes:{q}"))
            tasks.append(asyncio.create_task(self._search_deezer(q), name=f"deezer:{q}"))
            if self.settings.jamendo_client_id:
                tasks.append(asyncio.create_task(self._search_jamendo(q), name=f"jamendo:{q}"))
                queried.add("jamendo")
            tasks.append(asyncio.create_task(self._search_audius(q), name=f"audius:{q}"))
        if _is_generic_myanmar_query(query) or re.search(r"[\u1000-\u109F]", query):
            tasks.append(
                asyncio.create_task(
                    self._search_itunes("Myanmar", country="MM"),
                    name="itunes:mm",
                )
            )
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        by_key: dict[tuple[str, str], MusicMatch] = {}
        ordered: list[MusicMatch] = []
        for result in gathered:
            if isinstance(result, BaseException):
                log.warning("music.provider_failed", error=str(result))
                continue
            for match in result:
                scored = MusicMatch(
                    track=match.track,
                    artist=match.artist,
                    album=match.album,
                    stream_url=match.stream_url,
                    source=match.source,
                    duration_s=match.duration_s,
                    preview=match.preview,
                    artwork_url=match.artwork_url,
                    score=relevance_score(query, match.track, match.artist, match.album or ""),
                )
                key = (scored.track.casefold(), scored.artist.casefold())
                existing = by_key.get(key)
                if existing is None:
                    by_key[key] = scored
                    ordered.append(scored)
                    continue
                # Prefer higher score; on ties prefer full track over preview.
                better = scored.score > existing.score + 1e-6
                same = abs(scored.score - existing.score) <= 1e-6
                upgrade = same and existing.preview and not scored.preview and scored.stream_url
                if better or upgrade:
                    idx = ordered.index(existing)
                    ordered[idx] = scored
                    by_key[key] = scored

        threshold = self.settings.music_relevance_threshold
        relevant = [m for m in ordered if m.score >= threshold and m.stream_url]
        # Generic Myanmar queries: do not treat weak English keyword hits as relevant.
        if _is_generic_myanmar_query(query):
            relevant = [m for m in relevant if m.score >= max(threshold, 0.5)]

        ytdlp_attempted = False
        ytdlp_hit = False
        relevant_full = [m for m in relevant if not m.preview]
        if self.settings.music_ytdlp_enabled:
            queried.add("youtube")
            # 30s store previews must not block YouTube — they are not the song.
            if not relevant_full:
                ytdlp_attempted = True
                yt = await self._search_youtube(query)
                if yt:
                    ytdlp_hit = True
                    relevant = yt
                    ordered = yt + ordered
                else:
                    log.warning("music.ytdlp_no_result", query=query)
        else:
            log.info("music.ytdlp_skipped", reason="disabled", query=query)

        playable_full = [m for m in relevant if m.stream_url and not m.preview]
        playable_preview = [m for m in relevant if m.stream_url and m.preview]
        # Keep low-score metadata for LLM "matches" display only (not playable).
        rest = [m for m in ordered if m not in playable_full and m not in playable_preview]
        ranked = sorted(playable_full, key=lambda m: m.score, reverse=True)
        ranked += sorted(playable_preview, key=lambda m: m.score, reverse=True)
        ranked += sorted(rest, key=lambda m: m.score, reverse=True)
        playable_sources = sorted(
            {m.source for m in ranked if m.stream_url and m.score >= threshold}
        )
        log.info(
            "music.search",
            query=query,
            full=len(playable_full),
            preview=len(playable_preview),
            sources=playable_sources,
            providers=sorted(queried),
            ytdlp_enabled=bool(self.settings.music_ytdlp_enabled),
            ytdlp_attempted=ytdlp_attempted,
            ytdlp_hit=ytdlp_hit,
            threshold=threshold,
        )
        return ranked

    def _load_catalog(self) -> list[dict[str, Any]]:
        if self._catalog is not None:
            return self._catalog
        path = (self.settings.music_catalog_path or "").strip()
        if not path:
            self._catalog = []
            return self._catalog
        try:
            raw = Path(path).read_text(encoding="utf-8")
            data = json.loads(raw)
            tracks = data.get("tracks") if isinstance(data, dict) else data
            if not isinstance(tracks, list):
                self._catalog = []
                return self._catalog
            self._catalog = [t for t in tracks if isinstance(t, dict)]
        except Exception as exc:  # noqa: BLE001
            log.warning("music.catalog_load_failed", path=path, error=str(exc))
            self._catalog = []
        return self._catalog

    def _local_tracks(self) -> list[LocalTrack]:
        root = music_local_root(self.settings.music_local_dir)
        if root is None:
            return []
        return scan_local_music(root)

    def _search_local(self, query: str) -> list[MusicMatch]:
        threshold = max(0.45, self.settings.music_relevance_threshold)
        generic = _is_generic_music_query(query)
        hits: list[MusicMatch] = []

        for track in self._local_tracks():
            if generic:
                score = 1.0
            else:
                score = relevance_score(query, track.title, track.artist, track.search_text)
                q_flat = re.sub(r"\s+", "", query.casefold())
                for alias in (track.title, track.artist, track.stem):
                    a_flat = re.sub(r"\s+", "", alias.casefold())
                    if a_flat and (a_flat in q_flat or q_flat in a_flat):
                        score = max(score, 0.95)
                if score < threshold:
                    continue
            hits.append(
                MusicMatch(
                    track=track.title,
                    artist=track.artist,
                    album=None,
                    stream_url=str(track.path),
                    source="local",
                    duration_s=None,
                    preview=False,
                    artwork_url=None,
                    score=score,
                )
            )

        hits.extend(self._search_catalog_json(query, threshold=threshold, generic=generic))
        if not hits:
            return []
        if generic:
            hits.sort(key=lambda m: (m.track.casefold(), m.artist.casefold()))
        else:
            hits.sort(key=lambda m: m.score, reverse=True)
        return hits

    def _search_catalog_json(
        self,
        query: str,
        *,
        threshold: float,
        generic: bool,
    ) -> list[MusicMatch]:
        entries = self._load_catalog()
        if not entries or generic:
            return []
        hits: list[MusicMatch] = []
        for item in entries:
            track = str(item.get("track") or item.get("title") or "").strip()
            artist = str(item.get("artist") or "").strip()
            url = str(item.get("stream_url") or item.get("url") or "").strip()
            if not track or not url or not url.startswith("https://"):
                continue
            aliases = item.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []
            alias_text = " ".join(str(a) for a in aliases)
            score = relevance_score(query, track, artist, alias_text)
            q_flat = re.sub(r"\s+", "", query.casefold())
            for alias in [track, artist, *aliases]:
                a_flat = re.sub(r"\s+", "", str(alias).casefold())
                if a_flat and (a_flat in q_flat or q_flat in a_flat):
                    score = max(score, 0.95)
            if score < threshold:
                continue
            duration = item.get("duration_s")
            duration_s = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
            hits.append(
                MusicMatch(
                    track=track,
                    artist=artist,
                    album=str(item.get("album") or "").strip() or None,
                    stream_url=url,
                    source="catalog",
                    duration_s=duration_s,
                    preview=False,
                    artwork_url=str(item.get("artwork_url") or "").strip() or None,
                    score=score,
                )
            )
        return hits

    def _ytdlp_cmd(self, query: str) -> list[str]:
        bin_path = (self.settings.music_ytdlp_bin or "yt-dlp").strip() or "yt-dlp"
        return [
            bin_path,
            f"ytsearch1:{query}",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            *_ytdlp_auth_args(self.settings),
            "--print",
            "%(title)s",
            "--print",
            "%(uploader,channel,creator)s",
            "--print",
            "%(duration)s",
            "--print",
            "%(webpage_url)s",
        ]

    async def _search_youtube(self, query: str) -> list[MusicMatch]:
        """Last-resort yt-dlp search; gated by MUSIC_YTDLP_ENABLED."""
        q = (query or "").strip()
        if not q:
            return []
        if not self.settings.music_ytdlp_enabled:
            log.info("music.ytdlp_skipped", reason="disabled", query=q)
            return []
        timeout = max(8.0, float(self.settings.music_ytdlp_timeout_s))
        cmd = self._ytdlp_cmd(q)
        log.info(
            "music.ytdlp_start",
            query=q,
            bin=cmd[0],
            timeout_s=timeout,
            clients=self.settings.music_ytdlp_player_clients,
            cookies=bool((self.settings.music_ytdlp_cookies or "").strip()),
        )
        async with self._ytdlp_lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                log.warning("music.ytdlp_missing", bin=cmd[0], query=q)
                return []
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                proc.kill()
                await proc.wait()
                log.warning("music.ytdlp_timeout", query=q, timeout_s=timeout)
                return []
        stderr_text = (stderr or b"")[:800].decode("utf-8", errors="replace")
        if proc.returncode not in (0, None):
            bot = "not a bot" in stderr_text.casefold()
            log.warning(
                "music.ytdlp_failed",
                query=q,
                code=proc.returncode,
                bot_check=bot,
                stderr=stderr_text,
                hint=(
                    "YouTube bot-check: set MUSIC_YTDLP_COOKIES to a Netscape cookies.txt "
                    "exported from a logged-in browser, or use a residential IP."
                    if bot
                    else None
                ),
            )
            if not stdout:
                return []
        text = (stdout or b"").decode("utf-8", errors="replace").strip()
        if not text:
            log.warning("music.ytdlp_empty", query=q, code=proc.returncode, stderr=stderr_text)
            return []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # --print title, uploader, duration, webpage_url (no -g / googlevideo URL).
        meta: list[str] = []
        watch_url = ""
        for ln in lines:
            if "youtube.com/watch" in ln or "youtu.be/" in ln:
                watch_url = ln
                continue
            if ln.startswith("https://") or ln.startswith("http://"):
                if not watch_url:
                    watch_url = ln
                continue
            if len(meta) < 3:
                meta.append(ln)
        title = (meta[0] if meta else q).strip() or q
        artist = (meta[1] if len(meta) > 1 else "").strip() or "YouTube"
        duration_s = None
        if len(meta) > 2:
            try:
                duration_s = float(meta[2])
            except ValueError:
                duration_s = None
        if not watch_url.startswith("https://"):
            log.warning("music.ytdlp_no_watch_url", query=q, lines=lines[:6])
            return []
        score = max(
            relevance_score(q, title, artist),
            0.6 if not _is_generic_myanmar_query(q) else 0.55,
        )
        return [
            MusicMatch(
                track=title,
                artist=artist,
                album=None,
                stream_url=watch_url,
                source="youtube",
                duration_s=duration_s,
                preview=False,
                artwork_url=None,
                score=score,
            )
        ]

    async def _search_itunes(self, query: str, country: str | None = None) -> list[MusicMatch]:
        params: dict[str, Any] = {
            "term": query,
            "entity": "song",
            "media": "music",
            "limit": 5,
        }
        if country:
            params["country"] = country
        data = await self.http.get_json(
            "https://itunes.apple.com/search",
            params=params,
        )
        matches: list[MusicMatch] = []
        for item in (data.get("results") or [])[:5]:
            track = str(item.get("trackName") or "").strip()
            if not track:
                continue
            millis = item.get("trackTimeMillis")
            preview = bool(item.get("previewUrl"))
            duration = None
            if preview:
                duration = 30.0
            elif isinstance(millis, (int, float)) and millis > 0:
                duration = float(millis) / 1000.0
            matches.append(
                MusicMatch(
                    track=track,
                    artist=str(item.get("artistName") or "").strip(),
                    album=str(item.get("collectionName") or "").strip() or None,
                    stream_url=str(item.get("previewUrl") or "").strip() or None,
                    source="itunes",
                    duration_s=duration,
                    preview=preview,
                    artwork_url=str(item.get("artworkUrl100") or "").strip() or None,
                )
            )
        return matches

    async def _search_deezer(self, query: str) -> list[MusicMatch]:
        data = await self.http.get_json(
            "https://api.deezer.com/search",
            params={"q": query, "limit": 5},
        )
        matches: list[MusicMatch] = []
        for item in (data.get("data") or [])[:5]:
            track = str(item.get("title") or "").strip()
            if not track:
                continue
            artist = ""
            artist_obj = item.get("artist")
            if isinstance(artist_obj, dict):
                artist = str(artist_obj.get("name") or "").strip()
            album = None
            album_obj = item.get("album")
            if isinstance(album_obj, dict):
                album = str(album_obj.get("title") or "").strip() or None
            preview = str(item.get("preview") or "").strip() or None
            duration = item.get("duration")
            duration_s = 30.0 if preview else None
            if not preview and isinstance(duration, (int, float)) and duration > 0:
                duration_s = float(duration)
            matches.append(
                MusicMatch(
                    track=track,
                    artist=artist,
                    album=album,
                    stream_url=preview,
                    source="deezer",
                    duration_s=duration_s,
                    preview=bool(preview),
                    artwork_url=(
                        str(album_obj.get("cover_medium") or "").strip() or None
                        if isinstance(album_obj, dict)
                        else None
                    ),
                )
            )
        return matches

    async def _search_jamendo(self, query: str) -> list[MusicMatch]:
        data = await self.http.get_json(
            "https://api.jamendo.com/v3.0/tracks/",
            params={
                "client_id": self.settings.jamendo_client_id,
                "format": "json",
                "limit": 5,
                "search": query,
                "audioformat": "mp32",
                "boost": "popularity_total",
            },
        )
        matches: list[MusicMatch] = []
        for item in (data.get("results") or [])[:5]:
            track = str(item.get("name") or "").strip()
            if not track:
                continue
            audio = str(item.get("audio") or "").strip() or None
            duration = item.get("duration")
            duration_s = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
            matches.append(
                MusicMatch(
                    track=track,
                    artist=str(item.get("artist_name") or "").strip(),
                    album=str(item.get("album_name") or "").strip() or None,
                    stream_url=audio,
                    source="jamendo",
                    duration_s=duration_s,
                    preview=False,
                    artwork_url=str(item.get("album_image") or "").strip() or None,
                )
            )
        return matches

    async def _audius_host(self) -> str | None:
        cached = getattr(self, "_audius_api_host", None)
        if cached:
            return cached
        data = await self.http.get_json("https://api.audius.co")
        hosts = data.get("data") if isinstance(data, dict) else None
        if not isinstance(hosts, list) or not hosts:
            return None
        host = str(hosts[0]).rstrip("/")
        if not host.startswith("https://"):
            return None
        self._audius_api_host = host
        return host

    async def _search_audius(self, query: str) -> list[MusicMatch]:
        host = await self._audius_host()
        if not host:
            return []
        data = await self.http.get_json(
            f"{host}/v1/tracks/search",
            params={"query": query, "app_name": "phoe_lone"},
        )
        matches: list[MusicMatch] = []
        for item in (data.get("data") or [])[:5]:
            track = str(item.get("title") or "").strip()
            track_id = str(item.get("id") or "").strip()
            if not track or not track_id:
                continue
            artist = ""
            user = item.get("user")
            if isinstance(user, dict):
                artist = str(user.get("name") or "").strip()
            duration = item.get("duration")
            duration_s = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
            matches.append(
                MusicMatch(
                    track=track,
                    artist=artist,
                    album=None,
                    stream_url=f"{host}/v1/tracks/{track_id}/stream?app_name=phoe_lone",
                    source="audius",
                    duration_s=duration_s,
                    preview=False,
                    artwork_url=None,
                )
            )
        return matches


def _pick_playable(
    matches: list[MusicMatch],
    query: str = "",
    threshold: float = 0.6,
) -> MusicMatch | None:
    """Prefer a relevant full-length stream; never return an unrelated full track."""
    relevant = [m for m in matches if m.stream_url and m.score >= threshold]
    if _is_generic_music_query(query):
        relevant = [m for m in relevant if m.score >= max(threshold, 0.5)]
        local_pool = [m for m in relevant if m.source == "local" and not m.preview]
        if local_pool:
            return random.choice(local_pool)
        return None
    for match in relevant:
        if not match.preview:
            return match
    for match in relevant:
        return match
    # Local / catalog / youtube already scored; if listed first with stream, allow.
    for match in matches:
        if match.stream_url and match.source in {"local", "catalog", "youtube"} and match.score >= threshold:
            return match
    return None


def music_payload_for_llm(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip stream URLs so Gemini does not read them aloud."""
    cleaned = dict(payload)
    cleaned.pop("stream_url", None)
    return cleaned


def device_music_call(
    tool_by_name: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Map a resolved search to a firmware music MCP tool, if the device has one."""
    if payload.get("source") == "local":
        return None
    stream_url = str(payload.get("stream_url") or "").strip()
    if not stream_url.startswith("https://"):
        return None
    for name in DEVICE_PLAY_TOOLS:
        tool = tool_by_name.get(name)
        if not tool:
            continue
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        args: dict[str, Any] = {}
        mapping = {
            "url": stream_url,
            "song_name": payload.get("track"),
            "song": payload.get("track"),
            "name": payload.get("track"),
            "artist_name": payload.get("artist"),
            "artist": payload.get("artist"),
        }
        for key, value in mapping.items():
            if key in properties and value:
                args[key] = value
        required = schema.get("required") or []
        if any(key not in args for key in required):
            continue
        if args:
            return name, args
    return None
