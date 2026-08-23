from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.observability.logging import get_logger
from app.tools.http import SafeHttp

log = get_logger(__name__)

_REDIS_PREFIX = "device_loc:"
_REDIS_TTL_S = 7 * 24 * 3600


@dataclass
class DeviceLocationHint:
    city: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ssid: str | None = None
    bssid: str | None = None
    source: str = "ota"


def extract_ota_location(body: dict[str, Any] | None) -> DeviceLocationHint:
    """Pull Wi-Fi / location fields from Xiaozhi OTA GetSystemInfoJson.

    Stock firmware (wifi_board.cc GetBoardJson) sends ssid, rssi, channel, ip, mac.
    It does **not** send GPS or BSSID. Some forks add bssid/city/lat/lon; we accept those.
    """
    body = body or {}
    board = body.get("board") if isinstance(body.get("board"), dict) else {}
    ssid = _str(board.get("ssid") or body.get("ssid"))
    bssid = _mac(board.get("bssid") or board.get("wifi_bssid") or board.get("ap_mac"))
    city = _str(board.get("city") or board.get("location") or body.get("city"))
    latitude = _num(board.get("latitude") or board.get("lat") or body.get("latitude"))
    longitude = _num(
        board.get("longitude") or board.get("lon") or board.get("lng") or body.get("longitude")
    )
    return DeviceLocationHint(
        city=city,
        country=_str(board.get("country")),
        latitude=latitude,
        longitude=longitude,
        ssid=ssid,
        bssid=bssid,
        source="ota",
    )


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mac(value: Any) -> str | None:
    text = _str(value)
    if not text:
        return None
    cleaned = text.lower().replace("-", ":")
    parts = cleaned.split(":")
    if len(parts) != 6:
        return None
    return cleaned


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def refine_with_wifi(http: SafeHttp, hint: DeviceLocationHint) -> DeviceLocationHint:
    """If firmware sent a BSSID, resolve coords via Mozilla Location Service."""
    if hint.latitude is not None and hint.longitude is not None:
        return hint
    if not hint.bssid:
        return hint
    try:
        payload = {
            "wifiAccessPoints": [
                {"macAddress": hint.bssid},
            ]
        }
        geo = await http.post_json(
            "https://location.services.mozilla.com/v1/geolocate?key=test",
            payload,
        )
        loc = geo.get("location") or {}
        lat = loc.get("lat")
        lng = loc.get("lng")
        if lat is None or lng is None:
            return hint
        hint.latitude = float(lat)
        hint.longitude = float(lng)
        hint.source = "wifi_bssid"
        reverse = await http.get_json(
            "https://geocoding-api.open-meteo.com/v1/reverse",
            params={"latitude": hint.latitude, "longitude": hint.longitude, "language": "en"},
        )
        results = reverse.get("results") or []
        if results:
            hint.city = results[0].get("name") or hint.city
            hint.country = results[0].get("country") or hint.country
        log.info(
            "location.wifi_bssid",
            bssid=hint.bssid,
            city=hint.city,
            latitude=hint.latitude,
            longitude=hint.longitude,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("location.wifi_bssid_failed", error=str(exc), bssid=hint.bssid)
    return hint


class DeviceLocationStore:
    def __init__(self, redis=None) -> None:
        self.redis = redis
        self._mem: dict[str, dict[str, Any]] = {}

    async def put(self, device_id: str, hint: DeviceLocationHint) -> None:
        payload = asdict(hint)
        self._mem[device_id] = payload
        if self.redis is None:
            return
        try:
            await self.redis.setex(
                f"{_REDIS_PREFIX}{device_id}",
                _REDIS_TTL_S,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("location.store_failed", error=str(exc))

    async def get(self, device_id: str) -> DeviceLocationHint | None:
        payload = self._mem.get(device_id)
        if payload is None and self.redis is not None:
            try:
                raw = await self.redis.get(f"{_REDIS_PREFIX}{device_id}")
                if raw:
                    payload = json.loads(raw)
                    self._mem[device_id] = payload
            except Exception as exc:  # noqa: BLE001
                log.warning("location.load_failed", error=str(exc))
        if not payload:
            return None
        return DeviceLocationHint(**{k: payload.get(k) for k in DeviceLocationHint.__dataclass_fields__})
