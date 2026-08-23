from __future__ import annotations

import secrets
from datetime import datetime, timezone

from app.config import Settings
from app.db.models import DeviceRecord, DeviceRepository, hash_token, normalize_mac, tokens_match


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthService:
    def __init__(self, repo: DeviceRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    def _new_token(self) -> str:
        return secrets.token_urlsafe(32)

    async def provision(
        self,
        device_id: str,
        client_id: str,
        *,
        serial_number: str | None = None,
        locale: str = "my-MM",
        rotate: bool = True,
    ) -> tuple[DeviceRecord, str]:
        device_id = normalize_mac(device_id)
        existing = await self.repo.get(device_id, client_id)
        token = self._new_token() if rotate or existing is None else ""
        token_hash = (
            hash_token(token, self.settings.auth_pepper)
            if token
            else (existing.token_hash if existing else "")
        )
        now = datetime.now(timezone.utc)
        record = DeviceRecord(
            device_id=device_id,
            client_id=client_id,
            serial_number=serial_number or (existing.serial_number if existing else None),
            status="active",
            locale=locale,
            token_hash=token_hash,
            token_version=(existing.token_version + 1) if existing and rotate else (
                existing.token_version if existing else 1
            ),
            created_at=existing.created_at if existing else now,
            last_seen_at=now,
        )
        saved = await self.repo.upsert(record)
        return saved, token

    async def disable(self, device_id: str, client_id: str) -> None:
        await self.repo.set_status(normalize_mac(device_id), client_id, "disabled")

    async def issue_or_get_token(
        self,
        device_id: str,
        client_id: str,
        *,
        serial_number: str | None = None,
        locale: str = "my-MM",
        user_agent: str | None = None,
    ) -> str:
        device_id = normalize_mac(device_id)
        existing = await self.repo.get(device_id, client_id)
        if existing is None:
            if not self.settings.allow_auto_provision:
                raise AuthError("device is not provisioned", 403)
            _, token = await self.provision(
                device_id,
                client_id,
                serial_number=serial_number,
                locale=locale,
                rotate=True,
            )
            return token
        if not existing.is_active:
            raise AuthError("device is disabled", 403)
        if (
            serial_number
            and existing.serial_number
            and serial_number != existing.serial_number
        ):
            raise AuthError("serial mismatch", 403)
        await self.repo.touch(device_id, client_id, user_agent=user_agent)
        _, token = await self.provision(
            device_id,
            client_id,
            serial_number=serial_number or existing.serial_number,
            locale=locale,
            rotate=True,
        )
        return token

    async def authenticate_ws(
        self,
        device_id: str,
        client_id: str,
        authorization: str | None,
    ) -> DeviceRecord:
        device_id = normalize_mac(device_id)
        record = await self.repo.get(device_id, client_id)
        if record is None or not record.is_active:
            raise AuthError("unauthorized", 403)
        token = _parse_bearer(authorization)
        if not token or not tokens_match(token, record.token_hash, self.settings.auth_pepper):
            raise AuthError("unauthorized", 403)
        await self.repo.touch(device_id, client_id)
        return record


def _parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    prefix = "bearer "
    if value.lower().startswith(prefix):
        return value[len(prefix) :].strip()
    if " " not in value:
        return value
    return None
