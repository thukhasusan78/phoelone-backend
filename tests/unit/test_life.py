from __future__ import annotations

from datetime import timedelta

import pytest

from app.companion.life import (
    NAME_MAX,
    apply_care,
    cap_field,
    decay_care,
    empty_care,
    empty_memory,
    owner_prompt_prefix,
    patch_memory,
    utcnow,
)
from app.db.companion_store import InMemoryCompanionStore


def test_patch_memory_caps_and_prompt() -> None:
    mem = empty_memory("aa:bb:cc:dd:ee:ff", "cid")
    patch_memory(mem, {"owner_name": "Thukha", "likes": "x" * 400})
    assert mem.owner_name == "Thukha"
    assert len(mem.likes) == 160
    prefix = owner_prompt_prefix(mem)
    assert "Thukha" in prefix
    assert "OWNER MEMORY" in prefix
    assert owner_prompt_prefix(empty_memory("a", "b")) == ""
    assert len(cap_field("  hello  world  ", NAME_MAX)) <= NAME_MAX


def test_care_pet_raises_happiness_and_clamps() -> None:
    state = empty_care("aa:bb:cc:dd:ee:ff", "cid")
    before = state.happiness
    apply_care(state, "pet")
    assert state.happiness > before
    state.happiness = 99
    apply_care(state, "pet")
    assert state.happiness == 100


def test_care_decay_only_after_neglect() -> None:
    state = empty_care("aa:bb:cc:dd:ee:ff", "cid")
    apply_care(state, "pet")
    happy = state.happiness
    decay_care(state)
    assert state.happiness == happy
    state.last_touch_at = utcnow() - timedelta(hours=4)
    decay_care(state)
    assert state.happiness < happy


@pytest.mark.asyncio
async def test_in_memory_store_roundtrip() -> None:
    store = InMemoryCompanionStore()
    mem = empty_memory("AA-BB-CC-DD-EE-FF", "cid")
    patch_memory(mem, {"owner_name": "Ada", "nickname": "Mick"})
    saved = await store.set_memory(mem)
    loaded = await store.get_memory("aa:bb:cc:dd:ee:ff", "cid")
    assert loaded.owner_name == "Ada"
    assert loaded.nickname == "Mick"
    assert saved.device_id == "aa:bb:cc:dd:ee:ff"
    first = await store.apply_care("aa:bb:cc:dd:ee:ff", "cid", "pet")
    second = await store.get_care("aa:bb:cc:dd:ee:ff", "cid")
    assert second.happiness == first.happiness
    energy_before = second.energy
    slept = await store.apply_care("aa:bb:cc:dd:ee:ff", "cid", "sleep")
    assert slept.energy > energy_before
    voice = await store.apply_care("aa:bb:cc:dd:ee:ff", "cid", "voice")
    assert voice.streak_days >= 1
    assert await store.unlock_achievement("aa:bb:cc:dd:ee:ff", "cid", "first_pet") is True
    assert await store.unlock_achievement("aa:bb:cc:dd:ee:ff", "cid", "first_pet") is False
    assert "first_pet" in await store.list_achievements("aa:bb:cc:dd:ee:ff", "cid")


def test_streak_uses_myanmar_local_date() -> None:
    from datetime import datetime, timezone

    from app.companion.life import local_date_iso

    utc_morning = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
    assert local_date_iso(utc_morning) == "2026-08-31"
    utc_prev_calendar = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    assert local_date_iso(utc_prev_calendar) == "2026-08-31"
