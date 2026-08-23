from __future__ import annotations

from enum import Enum


class SessionState(str, Enum):
    CONNECTING = "connecting"
    READY = "ready"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


ALLOWED_FROM: dict[SessionState, set[SessionState]] = {
    SessionState.CONNECTING: {SessionState.READY, SessionState.CLOSED},
    SessionState.READY: {SessionState.LISTENING, SessionState.SPEAKING, SessionState.CLOSED},
    SessionState.LISTENING: {
        SessionState.THINKING,
        SessionState.READY,
        SessionState.SPEAKING,
        SessionState.CLOSED,
        SessionState.LISTENING,
    },
    SessionState.THINKING: {
        SessionState.SPEAKING,
        SessionState.READY,
        SessionState.LISTENING,
        SessionState.CLOSED,
    },
    SessionState.SPEAKING: {
        SessionState.READY,
        SessionState.LISTENING,
        SessionState.THINKING,
        SessionState.CLOSED,
        SessionState.SPEAKING,
    },
    SessionState.CLOSED: set(),
}


class InvalidTransition(Exception):
    pass


class StateMachine:
    def __init__(self) -> None:
        self.state = SessionState.CONNECTING
        self.generation = 0

    def can(self, target: SessionState) -> bool:
        return target in ALLOWED_FROM[self.state]

    def transition(self, target: SessionState) -> int:
        if self.state == SessionState.CLOSED:
            raise InvalidTransition(f"{self.state} -> {target}")
        if target != SessionState.CLOSED and not self.can(target):
            raise InvalidTransition(f"{self.state} -> {target}")
        self.state = target
        return self.generation

    def bump_generation(self) -> int:
        self.generation += 1
        return self.generation

    def close(self) -> None:
        self.state = SessionState.CLOSED
        self.generation += 1
