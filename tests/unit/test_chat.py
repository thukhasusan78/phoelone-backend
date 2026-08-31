from __future__ import annotations

import pytest

from app.companion.chat import CHAT_MAX_CHARS, normalize_chat_text
from app.companion.errors import CompanionError


def test_normalize_rejects_empty() -> None:
    with pytest.raises(CompanionError) as exc:
        normalize_chat_text("   ")
    assert exc.value.code == "invalid"


def test_normalize_strips_and_caps() -> None:
    assert normalize_chat_text("  မင်္ဂလာပါ  ") == "မင်္ဂလာပါ"
    long = "က" * (CHAT_MAX_CHARS + 40)
    assert normalize_chat_text(long) == "က" * CHAT_MAX_CHARS
