from __future__ import annotations

from app.companion.errors import CompanionError

CHAT_MAX_CHARS = 400
CHAT_HISTORY = 10


def normalize_chat_text(raw: object) -> str:
    text = " ".join(str(raw or "").split()).strip()
    if not text:
        raise CompanionError("invalid", "Type a message first.")
    if len(text) > CHAT_MAX_CHARS:
        return text[:CHAT_MAX_CHARS]
    return text
