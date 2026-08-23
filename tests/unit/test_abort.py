from __future__ import annotations

from app.protocol.state import StateMachine


def test_abort_increments_generation() -> None:
    from app.protocol.state import StateMachine

    sm = StateMachine()
    g1 = sm.bump_generation()
    g2 = sm.bump_generation()
    assert g2 > g1
    sm.close()
    assert sm.state.value == "closed"
