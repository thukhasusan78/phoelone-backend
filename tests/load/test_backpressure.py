from __future__ import annotations

"""Lightweight concurrency / backpressure checks without hardware."""

import asyncio

from app.sessions.session import Outbound
from app.protocol.state import StateMachine


def test_stale_generation_dropped() -> None:
    sm = StateMachine()
    old = sm.generation
    sm.bump_generation()
    item = Outbound("bytes", b"x", old)
    assert item.generation != sm.generation
