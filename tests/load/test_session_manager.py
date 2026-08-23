from __future__ import annotations

import pytest

from app.config import Settings
from app.sessions.session import SessionManager


class _Dummy:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_session_manager_replaces_and_limits() -> None:
    settings = Settings(database_url="memory://", max_concurrent_sessions=1)
    manager = SessionManager(settings)
    first = _Dummy("aa:bb:cc:dd:ee:ff")
    await manager.attach(first)  # type: ignore[arg-type]
    assert manager.count() == 1
    second = _Dummy("aa:bb:cc:dd:ee:ff")
    await manager.attach(second)  # type: ignore[arg-type]
    assert first.closed is True
    third = _Dummy("11:22:33:44:55:66")
    with pytest.raises(RuntimeError):
        await manager.attach(third)  # type: ignore[arg-type]
    await manager.detach(second)  # type: ignore[arg-type]
    assert manager.count() == 0
