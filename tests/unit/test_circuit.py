from __future__ import annotations

from app.ai.tool_router import CircuitBreaker


def test_circuit_opens_after_failures() -> None:
    c = CircuitBreaker(threshold=3, reset_s=30)
    assert c.allow()
    c.fail()
    c.fail()
    assert c.allow()
    c.fail()
    assert not c.allow()
    c.ok()
    assert c.allow()
