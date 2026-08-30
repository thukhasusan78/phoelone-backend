from __future__ import annotations

from app.ai.tool_router import ToolRouter, canonical_tool_name
from app.config import Settings
from app.mcp.client import McpClient
from app.tools.knowledge import KnowledgeTool
from app.tools.music import MusicTool
from app.tools.news import NewsTool
from app.tools.weather import WeatherTool


class DummyHttp:
    async def get_json(self, url: str, params=None, headers=None):
        if "geocoding" in url:
            if "reverse" in url:
                return {"results": [{"name": "Mandalay", "country": "Myanmar"}]}
            name = str((params or {}).get("name") or "Yangon")
            places = {
                "yangon": ("Yangon", 16.8, 96.1),
                "mandalay": ("Mandalay", 21.95, 96.08),
            }
            key = name.strip().lower()
            label, lat, lon = places.get(key, (name.title(), 16.8, 96.1))
            return {
                "results": [
                    {"name": label, "country": "Myanmar", "latitude": lat, "longitude": lon}
                ]
            }
        if "open-meteo.com/v1/forecast" in url:
            return {"current": {"temperature_2m": 32}, "daily": {}}
        if "itunes" in url:
            return {
                "results": [
                    {
                        "trackName": "Song",
                        "artistName": "Artist",
                        "collectionName": "Album",
                        "previewUrl": "https://audio-ssl.itunes.apple.com/preview.m4a",
                        "artworkUrl100": "https://example.com/art.jpg",
                    }
                ]
            }
        if "api.deezer.com/search" in url:
            return {"data": []}
        if "wikipedia.org/w/rest.php/v1/search/page" in url:
            return {
                "pages": [
                    {
                        "title": "Yangon",
                        "key": "Yangon",
                        "description": "Largest city in Myanmar",
                        "excerpt": "Yangon is ...",
                    }
                ]
            }
        if "wikipedia.org/api/rest_v1/page/summary" in url:
            return {"extract": "Yangon is the largest city in Myanmar."}
        return {}

    async def get_text(self, url: str, params=None):
        return "<rss><title>News</title><title>Headline One</title></rss>"

    async def post_json(self, url: str, payload, headers=None):
        return {"answer": "ok", "results": [{"title": "A", "url": "https://example.com", "content": "c"}]}


async def test_host_weather_and_music() -> None:
    http = DummyHttp()
    weather = WeatherTool(http)  # type: ignore[arg-type]
    music = MusicTool(http, Settings(database_url="memory://", music_ytdlp_enabled=False))  # type: ignore[arg-type]
    result = await weather(location="Yangon", user_text="ရန်ကုန် ရာသီဥတု")
    assert result["current"]["temperature_2m"] == 32
    songs = await music(query="Artist", play=True)
    assert songs["matches"][0]["track"] == "Song"
    assert songs["play_requested"] is True
    assert songs["playback"] == "queued"
    assert songs["stream_url"].startswith("https://")
    assert songs["source"] == "itunes"
    news = NewsTool(http, Settings(database_url="memory://", tavily_key=""))  # type: ignore[arg-type]
    headlines = await news(topic="Yangon")
    assert headlines["query"] == "Yangon"
    assert headlines["headlines"][0]["title"] == "Headline One"


async def test_search_web_wikipedia_fallback() -> None:
    http = DummyHttp()
    knowledge = KnowledgeTool(http, Settings(database_url="memory://", tavily_key=""))  # type: ignore[arg-type]
    result = await knowledge(query="Yangon")
    assert result["source"] == "wikipedia"
    assert "Yangon" in (result["answer"] or "")


async def test_weather_uses_ip_when_location_omitted() -> None:
    class GeoHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "ipwho.is" in url:
                return {
                    "success": True,
                    "city": "Mandalay",
                    "country": "Myanmar",
                    "latitude": 21.95,
                    "longitude": 96.08,
                }
            return await super().get_json(url, params, headers)

    http = GeoHttp()
    weather = WeatherTool(http)  # type: ignore[arg-type]
    result = await weather(client_ip="8.8.8.8")
    assert result["location"] == "Mandalay"
    assert result["resolved_from"] == "ip"
    assert result["current"]["temperature_2m"] == 32


async def test_weather_ignores_guessed_yangon_without_user_mention() -> None:
    class GeoHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "ipwho.is" in url:
                return {
                    "success": True,
                    "city": "Naypyidaw",
                    "country": "Myanmar",
                    "latitude": 19.76,
                    "longitude": 96.08,
                }
            if "geocoding" in url:
                raise AssertionError("guessed Yangon must not be geocoded")
            return await super().get_json(url, params, headers)

    http = GeoHttp()
    weather = WeatherTool(http)  # type: ignore[arg-type]
    result = await weather(
        location="Yangon",
        client_ip="8.8.8.8",
        user_text="ဒီနေ့ ရန်သူရူး တို့ အခြေအနေဘယ်လိုရှိလဲ?",
    )
    assert result["resolved_from"] == "ip"
    assert result["location"] == "Naypyidaw"


async def test_weather_keeps_yangon_when_user_names_it() -> None:
    http = DummyHttp()
    weather = WeatherTool(http)  # type: ignore[arg-type]
    result = await weather(location="Yangon", user_text="ရန်ကုန် ရာသီဥတု")
    assert result["resolved_from"] == "city"
    assert result["location"] == "Yangon"


async def test_weather_uses_config_default_before_ip() -> None:
    class GeoHttp(DummyHttp):
        async def get_json(self, url: str, params=None, headers=None):
            if "ipwho.is" in url:
                raise AssertionError("config default must skip IP geolocation")
            return await super().get_json(url, params, headers)

    weather = WeatherTool(GeoHttp(), default_location="Mandalay")  # type: ignore[arg-type]
    result = await weather(
        location="Yangon",
        client_ip="8.8.8.8",
        user_text="ဒီနေ့ ရာသီဥတု ဘယ်လိုလဲ",
    )
    assert result["resolved_from"] == "config"
    assert result["location"] == "Mandalay"


async def test_weather_uses_device_coords() -> None:
    weather = WeatherTool(DummyHttp(), default_location="Yangon")  # type: ignore[arg-type]
    result = await weather(device_city="Home", device_latitude=21.97, device_longitude=96.08)
    assert result["resolved_from"] == "device_wifi"
    assert result["location"] == "Home"


async def test_weather_private_ip_needs_city() -> None:
    http = DummyHttp()
    weather = WeatherTool(http)  # type: ignore[arg-type]
    result = await weather(client_ip="192.168.1.1")
    assert result["need_city"] is True


async def test_handle_exit_intent_sets_exit_flag() -> None:
    settings = Settings(database_url="memory://", tavily_key="x")
    http = DummyHttp()
    router = ToolRouter(
        settings,
        WeatherTool(http),  # type: ignore[arg-type]
        NewsTool(http, settings),  # type: ignore[arg-type]
        KnowledgeTool(http, settings),  # type: ignore[arg-type]
        MusicTool(http),  # type: ignore[arg-type]
    )
    mcp = McpClient("s", lambda _: None)
    exited: list[bool] = []

    async def on_exit() -> None:
        exited.append(True)

    payload = await router.dispatch(
        "handle_exit_intent",
        {"say_goodbye": "ဘိုင်း"},
        mcp,
        lambda _: None,
        on_exit=on_exit,
    )
    assert payload["exit"] is True
    assert exited == [True]


async def test_router_unknown_device_tool() -> None:
    settings = Settings(database_url="memory://", tavily_key="x")
    http = DummyHttp()
    router = ToolRouter(
        settings,
        WeatherTool(http),  # type: ignore[arg-type]
        NewsTool(http, settings),  # type: ignore[arg-type]
        KnowledgeTool(http, settings),  # type: ignore[arg-type]
        MusicTool(http),  # type: ignore[arg-type]
    )

    async def send(_):
        return None

    mcp = McpClient("s", send)
    emotions: list[str] = []

    async def set_emotion(value: str) -> None:
        emotions.append(value)

    result = await router.dispatch("self.missing", {}, mcp, set_emotion)
    assert "error" in result
    await router.dispatch("set_emotion", {"emotion": "happy"}, mcp, set_emotion)
    assert emotions == ["happy"]


def test_canonical_tool_name_adds_self_prefix() -> None:
    assert canonical_tool_name("otto.action") == "self.otto.action"
    assert canonical_tool_name("self.otto.action") == "self.otto.action"
    assert canonical_tool_name("mickey.alarm.set") == "self.mickey.alarm.set"
    assert canonical_tool_name("self.mickey.sleep.now") == "self.mickey.sleep.now"
    assert canonical_tool_name("search_music") == "search_music"


def test_function_response_marks_internal_and_unwraps_mcp_json() -> None:
    wrapped = ToolRouter.as_function_response(
        {"result": '{"ok": true, "action": "swing", "speed": 500}'}
    )
    assert "result" in wrapped
    body = wrapped["result"]
    assert "INTERNAL" in body
    assert "Do not read" in body
    assert "swing" in body or "ok" in body
