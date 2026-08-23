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
