from __future__ import annotations

import httpx
import pytest

from app.companion.tiktok import (
    SOCIALBLADE_URL,
    TIKTOK_PROFILE_URL,
    TikTokStats,
    TikTokStatsError,
    clear_cache,
    fetch_tiktok_stats,
    format_count,
    parse_socialblade_html,
    parse_tiktok_html,
)

TIKTOK_HTML = """
<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">
{"__DEFAULT_SCOPE__":{"webapp.user-detail":{"userInfo":{"statsV2":{
  "followerCount":"9721","heartCount":"75300"
}}}}}
</script>
"""

SOCIALBLADE_HTML = """
<script id="__NEXT_DATA__">
{"props":{"pageProps":{"trpcState":{"data":[
  {"date":"2026-08-01T00:00:00.000Z","followers":8000,"likes":60000},
  {"date":"2026-09-01T00:00:00.000Z","followers":9721,"likes":75300}
]}}}}
</script>
"""


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_cache()
    yield
    clear_cache()


def test_format_count() -> None:
    assert format_count(500) == "500"
    assert format_count(9721) == "9.72K"
    assert format_count(24500) == "24.5K"
    assert format_count(1_000_000) == "1.0M"


def test_parse_tiktok_rehydration_blob() -> None:
    assert parse_tiktok_html(TIKTOK_HTML) == (9721, 75300)
    assert parse_tiktok_html("<html></html>") is None


def test_parse_socialblade_next_data() -> None:
    assert parse_socialblade_html(SOCIALBLADE_HTML) == (9721, 75300)
    assert parse_socialblade_html("<html></html>") is None


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "socialblade.com" in url:
        return httpx.Response(200, text=SOCIALBLADE_HTML)
    if "tiktok.com" in url:
        return httpx.Response(200, text="<html>waf</html>")
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_fetch_uses_socialblade_when_tiktok_blocked() -> None:
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        stats = await fetch_tiktok_stats(http, now=10.0)
        again = await fetch_tiktok_stats(http, now=11.0)
    assert stats == TikTokStats(9721, 75300)
    assert again == stats


@pytest.mark.asyncio
async def test_fetch_prefers_tiktok_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "tiktok.com" in str(request.url):
            return httpx.Response(200, text=TIKTOK_HTML)
        raise AssertionError("socialblade should not be called")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        stats = await fetch_tiktok_stats(http)
    assert stats == TikTokStats(9721, 75300)


@pytest.mark.asyncio
async def test_fetch_raises_when_empty() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>"))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(TikTokStatsError):
            await fetch_tiktok_stats(http)

    assert TIKTOK_PROFILE_URL.startswith("https://")
    assert SOCIALBLADE_URL.startswith("https://")
