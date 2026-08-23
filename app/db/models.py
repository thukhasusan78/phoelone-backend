from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


def normalize_mac(value: str) -> str:
    cleaned = value.strip().lower().replace("-", ":")
    return cleaned


def hash_token(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_match(token: str, token_hash: str, pepper: str) -> bool:
    expected = hash_token(token, pepper)
    return hmac.compare_digest(expected, token_hash)


@dataclass
class DeviceRecord:
    device_id: str
    client_id: str
    serial_number: str | None
    status: str
    locale: str
    token_hash: str
    token_version: int
    created_at: datetime
    last_seen_at: datetime | None
    last_user_agent: str | None = None
    id: int | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class DeviceRepository(Protocol):
    async def get(self, device_id: str, client_id: str) -> DeviceRecord | None: ...

    async def upsert(self, record: DeviceRecord) -> DeviceRecord: ...

    async def list_devices(self) -> list[DeviceRecord]: ...

    async def set_status(self, device_id: str, client_id: str, status: str) -> None: ...

    async def touch(
        self,
        device_id: str,
        client_id: str,
        *,
        user_agent: str | None = None,
    ) -> None: ...
