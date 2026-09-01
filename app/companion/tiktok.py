from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from app.observability.logging import get_logger
from app.tools.http import assert_public_https

log = get_logger(__name__)

TIKTOK_PROFILE_URL = "https://www.tiktok.com/@thukhatech"
SOCIALBLADE_URL = "https://socialblade.com/tiktok/user/thukhatech"
CACHE_TTL_S = 60.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_cache: tuple[float, int, int] | None = None


class TikTokStatsError(Exception):
    """Live stats could not be fetched from TikTok or the fallback proxy."""


@dataclass(frozen=True)
class TikTokStats:
    followers: int
    likes: int

    def formatted(self) -> dict[str, str]:
        return {
            "followers": format_count(self.followers),
            "likes": format_count(self.likes),
        }

    def to_json(self) -> dict:
        return {
            "ok": True,
            "followers": self.followers,
            "likes": self.likes,
            "formatted": self.formatted(),
        }


def format_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1000:
        text = f"{count / 1000:.2f}".rstrip("0").rstrip(".")
        return f"{text}K"
    return str(count)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if re.fullmatch(r"\d+", cleaned):
            return int(cleaned)
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMB])", cleaned, re.I)
        if match:
            number = float(match.group(1))
            factor = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2).upper()]
            return int(number * factor)
    return None


def _walk_stats(obj: object) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    if isinstance(obj, dict):
        followers = _as_int(obj.get("followers") or obj.get("followerCount"))
        likes = _as_int(
            obj.get("likes") or obj.get("heartCount") or obj.get("heart") or obj.get("hearts")
        )
        if followers and likes is not None and followers > 0:
            found.append((str(obj.get("date") or ""), followers, likes))
        nested_stats = obj.get("statsV2") or obj.get("stats")
        if isinstance(nested_stats, dict):
            found.extend(_walk_stats(nested_stats))
        for value in obj.values():
            if value is nested_stats:
                continue
            found.extend(_walk_stats(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_walk_stats(value))
    return found


def parse_tiktok_html(html: str) -> tuple[int, int] | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if tag and tag.string:
        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            data = None
        if data:
            detail = (data.get("__DEFAULT_SCOPE__") or {}).get("webapp.user-detail") or {}
            info = detail.get("userInfo") or {}
            for block in (info.get("statsV2"), info.get("stats"), info):
                if not isinstance(block, dict):
                    continue
                followers = _as_int(block.get("followerCount") or block.get("followers"))
                likes = _as_int(
                    block.get("heartCount") or block.get("heart") or block.get("likes")
                )
                if followers and likes is not None:
                    return followers, likes
    soup_followers = soup.select_one('[data-e2e="followers-count"]')
    soup_likes = soup.select_one('[data-e2e="likes-count"]')
    if soup_followers and soup_likes:
        followers = _as_int(soup_followers.get_text())
        likes = _as_int(soup_likes.get_text())
        if followers and likes is not None:
            return followers, likes
    return None


def parse_socialblade_html(html: str) -> tuple[int, int] | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            data = None
        if data:
            snapshots = _walk_stats(data)
            if snapshots:
                snapshots.sort()
                _date, followers, likes = snapshots[-1]
                return followers, likes
    follower_match = re.search(r'"followers"\s*:\s*(\d+)', html)
    likes_match = re.search(r'"likes"\s*:\s*(\d+)', html)
    if follower_match and likes_match:
        return int(follower_match.group(1)), int(likes_match.group(1))
    return None


def clear_cache() -> None:
    global _cache
    _cache = None


async def _download(url: str, http: httpx.AsyncClient | None) -> str:
    assert_public_https(url)
    if http is not None:
        response = await http.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=20.0,
        )
        response.raise_for_status()
        return response.text
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome") as session:
        response = await session.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=20,
        )
        response.raise_for_status()
        return response.text


async def fetch_tiktok_stats(
    http: httpx.AsyncClient | None = None,
    *,
    now: float | None = None,
) -> TikTokStats:
    global _cache
    stamp = time.monotonic() if now is None else now
    if _cache is not None and stamp - _cache[0] < CACHE_TTL_S:
        return TikTokStats(_cache[1], _cache[2])

    parsed: tuple[int, int] | None = None
    try:
        tiktok_html = await _download(TIKTOK_PROFILE_URL, http)
        parsed = parse_tiktok_html(tiktok_html)
        if parsed:
            log.info("tiktok.stats_source", source="tiktok")
    except Exception as exc:  # noqa: BLE001
        log.info("tiktok.fetch_failed", source="tiktok", error=str(exc))

    if parsed is None:
        try:
            blade_html = await _download(SOCIALBLADE_URL, http)
            parsed = parse_socialblade_html(blade_html)
            if parsed:
                log.info("tiktok.stats_source", source="socialblade")
        except Exception as exc:  # noqa: BLE001
            log.info("tiktok.fetch_failed", source="socialblade", error=str(exc))

    if parsed is None:
        raise TikTokStatsError("live TikTok stats unavailable")

    followers, likes = parsed
    _cache = (stamp, followers, likes)
    log.info("tiktok.stats", followers=followers, likes=likes)
    return TikTokStats(followers, likes)
