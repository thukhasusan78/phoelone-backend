from __future__ import annotations

import pytest

from app.protocol.state import InvalidTransition, SessionState, StateMachine


def test_happy_path_transitions() -> None:
    sm = StateMachine()
    assert sm.state == SessionState.CONNECTING
    sm.transition(SessionState.READY)
    sm.transition(SessionState.LISTENING)
    sm.transition(SessionState.THINKING)
    sm.transition(SessionState.SPEAKING)
    sm.transition(SessionState.READY)


def test_generation_bump() -> None:
    sm = StateMachine()
    first = sm.generation
    second = sm.bump_generation()
    assert second == first + 1


def test_closed_blocks() -> None:
    sm = StateMachine()
    sm.close()
    with pytest.raises(InvalidTransition):
        sm.transition(SessionState.READY)
