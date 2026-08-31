from __future__ import annotations

from typing import Protocol

from app.companion.life import (
    CareState,
    OwnerMemory,
    apply_care,
    decay_care,
    empty_care,
    empty_memory,
    patch_memory,
    utcnow,
)
from app.db.models import normalize_mac


class CompanionStore(Protocol):
    async def get_memory(self, device_id: str, client_id: str) -> OwnerMemory: ...

    async def set_memory(self, memory: OwnerMemory) -> OwnerMemory: ...

    async def get_care(self, device_id: str, client_id: str) -> CareState: ...

    async def apply_care(self, device_id: str, client_id: str, kind: str) -> CareState: ...

    async def decay_all(self) -> list[CareState]: ...

    async def list_achievements(self, device_id: str, client_id: str) -> list[str]: ...

    async def unlock_achievement(
        self, device_id: str, client_id: str, code: str
    ) -> bool: ...


def _key(device_id: str, client_id: str) -> tuple[str, str]:
    return (normalize_mac(device_id), client_id.strip())


class InMemoryCompanionStore:
    def __init__(self) -> None:
        self._memory: dict[tuple[str, str], OwnerMemory] = {}
        self._care: dict[tuple[str, str], CareState] = {}
        self._achievements: dict[tuple[str, str], list[str]] = {}

    async def get_memory(self, device_id: str, client_id: str) -> OwnerMemory:
        key = _key(device_id, client_id)
        row = self._memory.get(key)
        if row is None:
            return empty_memory(*key)
        return OwnerMemory(
            device_id=row.device_id,
            client_id=row.client_id,
            owner_name=row.owner_name,
            nickname=row.nickname,
            likes=row.likes,
            locale=row.locale,
            updated_at=row.updated_at,
        )

    async def set_memory(self, memory: OwnerMemory) -> OwnerMemory:
        key = _key(memory.device_id, memory.client_id)
        current = await self.get_memory(*key)
        patched = patch_memory(
            current,
            {
                "owner_name": memory.owner_name,
                "nickname": memory.nickname,
                "likes": memory.likes,
            },
        )
        patched.device_id, patched.client_id = key
        patched.updated_at = utcnow()
        self._memory[key] = patched
        return patched

    async def get_care(self, device_id: str, client_id: str) -> CareState:
        key = _key(device_id, client_id)
        row = self._care.get(key)
        if row is None:
            state = empty_care(*key)
            self._care[key] = state
            return state
        return row

    async def apply_care(self, device_id: str, client_id: str, kind: str) -> CareState:
        state = await self.get_care(device_id, client_id)
        apply_care(state, kind)
        return state

    async def decay_all(self) -> list[CareState]:
        changed: list[CareState] = []
        now = utcnow()
        for state in self._care.values():
            before = (state.happiness, state.energy, state.bond)
            decay_care(state, now=now)
            if (state.happiness, state.energy, state.bond) != before:
                changed.append(state)
        return changed

    async def list_achievements(self, device_id: str, client_id: str) -> list[str]:
        return list(self._achievements.get(_key(device_id, client_id), ()))

    async def unlock_achievement(self, device_id: str, client_id: str, code: str) -> bool:
        key = _key(device_id, client_id)
        have = self._achievements.setdefault(key, [])
        if code in have:
            return False
        have.append(code)
        return True
