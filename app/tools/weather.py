from __future__ import annotations

import ipaddress
from typing import Any

from app.observability.logging import get_logger
from app.tools.http import SafeHttp

log = get_logger(__name__)

_HERE_ALIASES = frozenset(
    {
        "",
        "here",
        "current",
        "current location",
        "my location",
        "local",
        "near me",
        "nearby",
    }
)
# Gemini infers these from Myanmar context; only keep them if the user named them.
_DEFAULT_GUESSES = frozenset({"yangon", "rangoon", "ရန်ကုန်"})
_YANGON_MENTIONS = ("yangon", "rangoon", "ရန်ကုန်")


def _is_unconfirmed_default(location: str, user_text: str | None) -> bool:
    loc = location.strip().casefold()
    if loc not in _DEFAULT_GUESSES:
        return False
    text = (user_text or "").casefold()
    return not any(token in text for token in _YANGON_MENTIONS)


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


class WeatherTool:
    name = "search_weather"
    declaration = {
        "name": "search_weather",
        "description": (
            "Get a weather forecast for a location. Omit location to use the device's "
            "current location. Return structured facts; the model will speak Burmese."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or place name. Omit when the user means here/now.",
                },
                "date": {"type": "string", "description": "Optional ISO date (YYYY-MM-DD)"},
            },
            "required": [],
        },
    }

    def __init__(self, http: SafeHttp, default_location: str = "") -> None:
        self.http = http
        self.default_location = (default_location or "").strip()

    async def __call__(
        self,
        location: str | None = None,
        date: str | None = None,
        client_ip: str | None = None,
        user_text: str | None = None,
        device_city: str | None = None,
        device_latitude: float | None = None,
        device_longitude: float | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        named = (location or "").strip()
        if named.lower() in _HERE_ALIASES or _is_unconfirmed_default(named.lower(), user_text):
            if named and named.lower() not in _HERE_ALIASES:
                log.info(
                    "weather.ignore_guessed_city",
                    location=location,
                    user_text=user_text,
                )
            named = ""

        if named:
            return await self._forecast_city(named, date=date, resolved_from="city")

        if device_latitude is not None and device_longitude is not None:
            return await self._forecast_coords(
                float(device_latitude),
                float(device_longitude),
                date=date,
                place_name=device_city or "current location",
                resolved_from="device_wifi",
            )
        if device_city:
            return await self._forecast_city(device_city, date=date, resolved_from="device")
        if self.default_location:
            return await self._forecast_city(
                self.default_location,
                date=date,
                resolved_from="config",
            )
        return await self._forecast_ip(client_ip, date=date)

    async def _forecast_city(self, city: str, *, date: str | None, resolved_from: str) -> dict[str, Any]:
        geo = await self.http.get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
        )
        results = geo.get("results") or []
        if not results:
            return {"error": f"location not found: {city}", "need_city": True}
        place = results[0]
        return await self._forecast_coords(
            place["latitude"],
            place["longitude"],
            date=date,
            place_name=place.get("name"),
            country=place.get("country"),
            resolved_from=resolved_from,
        )

    async def _forecast_ip(self, client_ip: str | None, *, date: str | None) -> dict[str, Any]:
        if not client_ip or _is_private_ip(client_ip):
            return {"error": "current location unavailable", "need_city": True}
        geo = await self.http.get_json(f"https://ipwho.is/{client_ip}")
        if not geo.get("success"):
            return {"error": "current location unavailable", "need_city": True}
        latitude = geo.get("latitude")
        longitude = geo.get("longitude")
        if latitude is None or longitude is None:
            return {"error": "current location unavailable", "need_city": True}
        return await self._forecast_coords(
            latitude,
            longitude,
            date=date,
            place_name=geo.get("city") or geo.get("region"),
            country=geo.get("country"),
            resolved_from="ip",
        )

    async def _forecast_coords(
        self,
        latitude: float,
        longitude: float,
        *,
        date: str | None,
        place_name: str | None,
        resolved_from: str,
        country: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
        }
        data = await self.http.get_json("https://api.open-meteo.com/v1/forecast", params=params)
        payload = {
            "location": place_name,
            "country": country,
            "date": date,
            "current": data.get("current"),
            "daily": data.get("daily"),
            "resolved_from": resolved_from,
        }
        log.info(
            "weather.resolved",
            resolved_from=resolved_from,
            place=place_name,
        )
        return payload
