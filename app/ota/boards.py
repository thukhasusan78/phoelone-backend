"""OTA board identities advertised by firmware in POST body ``board.type``."""

from __future__ import annotations

# Current Mickey firmware. Legacy XiaoZhi / Phoe Lone identities stay accepted.
PRIMARY_BOARD_TYPE = "mickey"
KNOWN_BOARD_TYPES = frozenset({"mickey", "phoe-lone", "otto-robot"})


def normalize_board_type(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


def is_known_board_type(value: object) -> bool:
    board = normalize_board_type(value)
    return board in KNOWN_BOARD_TYPES
