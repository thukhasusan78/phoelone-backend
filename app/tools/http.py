from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

from app.observability.logging import get_logger

log = get_logger(__name__)

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}


class HttpGuardError(Exception):
    pass


def assert_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HttpGuardError("only HTTPS is allowed")
    host = (parsed.hostname or "").lower()
    if not host or host in BLOCKED_HOSTS or host.endswith(".internal"):
        raise HttpGuardError("blocked host")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HttpGuardError("private IP blocked")
    except ValueError:
        return


class SafeHttp:
    def __init__(self, client: httpx.AsyncClient, timeout_s: float = 8.0) -> None:
        self.client = client
        self.timeout_s = timeout_s

    async def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict | list:
        assert_public_https(url)
        response = await self.client.get(url, params=params, headers=headers, timeout=self.timeout_s)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise HttpGuardError("response too large")
        return response.json()

    async def get_text(self, url: str, params: dict | None = None) -> str:
        assert_public_https(url)
        response = await self.client.get(url, params=params, timeout=self.timeout_s)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise HttpGuardError("response too large")
        return response.text

    async def post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        assert_public_https(url)
        response = await self.client.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise HttpGuardError("response too large")
        return response.json()
