from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.auth.token_wrap import unwrap_token, wrap_token
from app.config import Settings
from app.db.models import DeviceRecord, DeviceRepository, hash_token, normalize_mac, tokens_match
from app.observability.logging import get_logger

log = get_logger(__name__)


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class OtaAuthResult:
    token: str
    activation: dict[str, Any] | None


class AuthService:
    def __init__(self, repo: DeviceRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    def _new_token(self) -> str:
        return secrets.token_urlsafe(32)

    def _new_code(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def normalize_code(value: str) -> str:
        digits = "".join(ch for ch in value.strip() if ch.isdigit())
        return digits

    def _activation_payload(self, code: str, challenge: str) -> dict[str, Any]:
        return {
            "message": self.settings.activation_message,
            "code": code,
            "challenge": challenge,
            "timeout_ms": self.settings.activation_timeout_ms,
        }

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _code_reusable(self, existing: DeviceRecord, now: datetime) -> bool:
        if existing.status != "pending" or not existing.activation_code:
            return False
        expires = self._aware(existing.activation_expires_at)
        return expires is not None and expires > now

    async def _allocate_code(self) -> str:
        for _ in range(32):
            code = self._new_code()
            if await self.repo.get_by_activation_code(code) is None:
                return code
        raise AuthError("could not allocate activation code", 503)

    async def provision(
        self,
        device_id: str,
        client_id: str,
        *,
        serial_number: str | None = None,
        locale: str = "my-MM",
        rotate: bool = True,
        status: str = "active",
    ) -> tuple[DeviceRecord, str]:
        device_id = normalize_mac(device_id)
        existing = await self.repo.get(device_id, client_id)
        pepper = self.settings.auth_pepper
        issuing = rotate or existing is None
        if issuing:
            token = self._new_token()
            token_hash = hash_token(token, pepper)
            token_ciphertext = wrap_token(token, pepper)
        else:
            token_hash = existing.token_hash if existing else ""
            token_ciphertext = existing.token_ciphertext if existing else None
            token = unwrap_token(token_ciphertext, pepper) or ""
        now = datetime.now(timezone.utc)
        bound = status == "active"
        record = DeviceRecord(
            device_id=device_id,
            client_id=client_id,
            serial_number=serial_number or (existing.serial_number if existing else None),
            status=status,
            locale=locale,
            token_hash=token_hash,
            token_version=(existing.token_version + 1) if existing and rotate else (
                existing.token_version if existing else 1
            ),
            created_at=existing.created_at if existing else now,
            last_seen_at=now,
            last_user_agent=existing.last_user_agent if existing else None,
            activation_code=None if bound else (existing.activation_code if existing else None),
            activation_challenge=(
                None if bound else (existing.activation_challenge if existing else None)
            ),
            activation_expires_at=(
                None if bound else (existing.activation_expires_at if existing else None)
            ),
            token_ciphertext=token_ciphertext,
        )
        saved = await self.repo.upsert(record)
        return saved, token

    async def disable(self, device_id: str, client_id: str) -> None:
        await self.repo.set_status(normalize_mac(device_id), client_id, "disabled")

    async def begin_activation(
        self,
        device_id: str,
        client_id: str,
        *,
        serial_number: str | None = None,
        locale: str = "my-MM",
        user_agent: str | None = None,
    ) -> tuple[DeviceRecord, str, str, str]:
        device_id = normalize_mac(device_id)
        existing = await self.repo.get(device_id, client_id)
        if existing is not None and existing.status == "disabled":
            raise AuthError("device is disabled", 403)
        now = datetime.now(timezone.utc)
        reuse = existing is not None and self._code_reusable(existing, now)
        if reuse and existing is not None and existing.activation_code:
            code = existing.activation_code
        else:
            code = await self._allocate_code()
        challenge = (
            existing.activation_challenge
            if reuse and existing is not None and existing.activation_challenge
            else str(uuid.uuid4())
        )
        expires = (
            self._aware(existing.activation_expires_at)
            if reuse and existing is not None
            else now + timedelta(seconds=self.settings.activation_ttl_s)
        )
        pepper = self.settings.auth_pepper
        reused_token = (
            unwrap_token(existing.token_ciphertext, pepper)
            if reuse and existing is not None
            else None
        )
        if reused_token and existing is not None:
            token = reused_token
            token_hash = existing.token_hash
            token_ciphertext = existing.token_ciphertext
            token_version = existing.token_version
        else:
            token = self._new_token()
            token_hash = hash_token(token, pepper)
            token_ciphertext = wrap_token(token, pepper)
            token_version = (existing.token_version + 1) if existing else 1
        record = DeviceRecord(
            device_id=device_id,
            client_id=client_id,
            serial_number=serial_number or (existing.serial_number if existing else None),
            status="pending",
            locale=locale,
            token_hash=token_hash,
            token_version=token_version,
            created_at=existing.created_at if existing else now,
            last_seen_at=now,
            last_user_agent=(
                user_agent[:256]
                if user_agent
                else (existing.last_user_agent if existing else None)
            ),
            activation_code=code,
            activation_challenge=challenge,
            activation_expires_at=expires,
            token_ciphertext=token_ciphertext,
        )
        saved = await self.repo.upsert(record)
        return saved, token, code, challenge

    async def prepare_ota(
        self,
        device_id: str,
        client_id: str,
        *,
        serial_number: str | None = None,
        locale: str = "my-MM",
        user_agent: str | None = None,
    ) -> OtaAuthResult:
        device_id = normalize_mac(device_id)
        existing = await self.repo.get(device_id, client_id)
        if existing is None:
            if not self.settings.allow_auto_provision:
                raise AuthError("device is not provisioned", 403)
            _, token, code, challenge = await self.begin_activation(
                device_id,
                client_id,
                serial_number=serial_number,
                locale=locale,
                user_agent=user_agent,
            )
            return OtaAuthResult(token, self._activation_payload(code, challenge))
        if existing.status == "disabled":
            raise AuthError("device is disabled", 403)
        if existing.status == "pending":
            _, token, code, challenge = await self.begin_activation(
                device_id,
                client_id,
                serial_number=serial_number or existing.serial_number,
                locale=locale,
                user_agent=user_agent,
            )
            return OtaAuthResult(token, self._activation_payload(code, challenge))
        await self.repo.touch(device_id, client_id, user_agent=user_agent)
        if (
            serial_number
            and existing.serial_number
            and serial_number != existing.serial_number
        ):
            raise AuthError("serial mismatch", 403)
        token = unwrap_token(existing.token_ciphertext, self.settings.auth_pepper)
        if token:
            return OtaAuthResult(token, None)
        log.info("auth.token_wrap_migrate", device_id=device_id, client_id=client_id)
        _, token = await self.provision(
            device_id,
            client_id,
            serial_number=serial_number or existing.serial_number,
            locale=locale,
            rotate=True,
            status="active",
        )
        return OtaAuthResult(token, None)

    async def poll_activation(
        self,
        device_id: str,
        client_id: str,
        *,
        challenge: str | None = None,
    ) -> str:
        device_id = normalize_mac(device_id)
        existing = await self.repo.get(device_id, client_id)
        if existing is None:
            return "unknown"
        if existing.status == "disabled":
            raise AuthError("device is disabled", 403)
        if existing.is_active:
            return "active"
        if (
            challenge
            and existing.activation_challenge
            and challenge != existing.activation_challenge
        ):
            raise AuthError("challenge mismatch", 400)
        return "pending"

    async def complete_activation(self, code: str) -> DeviceRecord | None:
        normalized = self.normalize_code(code)
        if len(normalized) != 6:
            return None
        existing = await self.repo.get_by_activation_code(normalized)
        if existing is None or existing.status != "pending":
            return None
        now = datetime.now(timezone.utc)
        expires = self._aware(existing.activation_expires_at)
        if expires is None or expires <= now:
            return None
        existing.status = "active"
        existing.activation_code = None
        existing.activation_challenge = None
        existing.activation_expires_at = None
        existing.last_seen_at = now
        return await self.repo.upsert(existing)

    async def issue_or_get_token(
        self,
        device_id: str,
        client_id: str,
        *,
        serial_number: str | None = None,
        locale: str = "my-MM",
        user_agent: str | None = None,
    ) -> str:
        result = await self.prepare_ota(
            device_id,
            client_id,
            serial_number=serial_number,
            locale=locale,
            user_agent=user_agent,
        )
        return result.token

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
