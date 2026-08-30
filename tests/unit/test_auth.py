from __future__ import annotations

import pytest

from app.auth.service import AuthError, AuthService
from app.config import Settings
from app.db.memory import InMemoryDeviceRepository
from app.db.models import hash_token, normalize_mac, tokens_match


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="memory://",
        allow_auto_provision=True,
        auth_pepper="test-pepper",
        gemini_api_keys="test-key",
        public_http_origin="http://testserver",
        public_ws_origin="ws://testserver",
    )


@pytest.fixture
def repo() -> InMemoryDeviceRepository:
    return InMemoryDeviceRepository()


@pytest.fixture
def auth(repo: InMemoryDeviceRepository, settings: Settings) -> AuthService:
    return AuthService(repo, settings)


async def test_normalize_mac() -> None:
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"


async def test_token_roundtrip(settings: Settings) -> None:
    token = "abc"
    digest = hash_token(token, settings.auth_pepper)
    assert tokens_match(token, digest, settings.auth_pepper)
    assert not tokens_match("nope", digest, settings.auth_pepper)


async def test_provision_and_authenticate(auth: AuthService, settings: Settings) -> None:
    record, token = await auth.provision("AA:BB:CC:DD:EE:FF", "client-1")
    assert record.device_id == "aa:bb:cc:dd:ee:ff"
    saved = await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {token}")
    assert saved.client_id == "client-1"


async def test_reject_bad_token(auth: AuthService) -> None:
    await auth.provision("aa:bb:cc:dd:ee:ff", "client-1")
    with pytest.raises(AuthError):
        await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", "Bearer wrong")


async def test_disable_device(auth: AuthService) -> None:
    _, token = await auth.provision("aa:bb:cc:dd:ee:ff", "client-1")
    await auth.disable("aa:bb:cc:dd:ee:ff", "client-1")
    with pytest.raises(AuthError):
        await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {token}")


async def test_auto_provision_off() -> None:
    settings = Settings(database_url="memory://", allow_auto_provision=False, auth_pepper="x")
    auth = AuthService(InMemoryDeviceRepository(), settings)
    with pytest.raises(AuthError):
        await auth.issue_or_get_token("aa:bb:cc:dd:ee:ff", "client-1")


async def test_activation_bind_then_ws(auth: AuthService) -> None:
    result = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-1")
    assert result.activation is not None
    code = result.activation["code"]
    with pytest.raises(AuthError):
        await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {result.token}")
    assert await auth.poll_activation("aa:bb:cc:dd:ee:ff", "client-1") == "pending"
    bound = await auth.complete_activation(code)
    assert bound is not None
    assert bound.is_active
    saved = await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {result.token}")
    assert saved.client_id == "client-1"
    assert await auth.poll_activation("aa:bb:cc:dd:ee:ff", "client-1") == "active"
    second = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-1")
    assert second.activation is None
    assert second.token == result.token
    saved_again = await auth.authenticate_ws(
        "aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {result.token}"
    )
    assert saved_again.client_id == "client-1"
    third = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-1")
    assert third.token == result.token


async def test_complete_activation_rejects_bad_code(auth: AuthService) -> None:
    await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-1")
    assert await auth.complete_activation("abcdef") is None
    assert await auth.complete_activation("123456") is None


async def test_pending_ota_reuses_token(auth: AuthService) -> None:
    first = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-pending")
    second = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-pending")
    assert first.activation is not None
    assert second.activation is not None
    assert first.activation["code"] == second.activation["code"]
    assert first.token == second.token


async def test_rotate_invalidates_previous_token(auth: AuthService) -> None:
    record, token = await auth.provision("aa:bb:cc:dd:ee:ff", "client-1")
    rotated, new_token = await auth.provision("aa:bb:cc:dd:ee:ff", "client-1", rotate=True)
    assert new_token != token
    assert rotated.token_version == record.token_version + 1
    with pytest.raises(AuthError):
        await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {token}")
    saved = await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {new_token}")
    assert saved.token_version == rotated.token_version


async def test_hash_only_row_migrates_on_ota(
    auth: AuthService, repo: InMemoryDeviceRepository
) -> None:
    _, token = await auth.provision("aa:bb:cc:dd:ee:ff", "client-1")
    stored = await repo.get("aa:bb:cc:dd:ee:ff", "client-1")
    assert stored is not None
    stored.token_ciphertext = None
    migrated = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-1")
    assert migrated.token != token
    with pytest.raises(AuthError):
        await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {token}")
    await auth.authenticate_ws("aa:bb:cc:dd:ee:ff", "client-1", f"Bearer {migrated.token}")
    again = await auth.prepare_ota("aa:bb:cc:dd:ee:ff", "client-1")
    assert again.token == migrated.token


async def test_wrap_token_roundtrip() -> None:
    from app.auth.token_wrap import unwrap_token, wrap_token

    token = "abc-token"
    blob = wrap_token(token, "test-pepper")
    assert unwrap_token(blob, "test-pepper") == token
    assert unwrap_token(blob, "wrong") is None
    assert unwrap_token(None, "test-pepper") is None


