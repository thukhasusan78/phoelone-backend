from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

THROWS = ("rock", "paper", "scissors")
Winner = Literal["player", "mickey", "draw"]
Phase = Literal["awaiting_throw", "reveal", "match_over"]

_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def decide_winner(player: str, mickey: str) -> Winner:
    if player == mickey:
        return "draw"
    if _BEATS[player] == mickey:
        return "player"
    return "mickey"


@dataclass
class RpsMatch:
    best_of: int = 3
    match_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    player_score: int = 0
    mickey_score: int = 0
    phase: Phase = "awaiting_throw"
    last_player: str | None = None
    last_mickey: str | None = None
    last_winner: Winner | None = None
    _rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        if self.best_of < 1 or self.best_of % 2 == 0:
            self.best_of = 3

    @property
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1

    def start(self, best_of: int = 3) -> dict[str, Any]:
        if best_of < 1 or best_of % 2 == 0:
            best_of = 3
        self.best_of = best_of
        self.match_id = uuid.uuid4().hex[:12]
        self.player_score = 0
        self.mickey_score = 0
        self.phase = "awaiting_throw"
        self.last_player = None
        self.last_mickey = None
        self.last_winner = None
        return self.to_state()

    def move(self, player: str, *, mickey: str | None = None) -> dict[str, Any]:
        if player not in THROWS:
            raise ValueError("invalid throw")
        if self.phase == "match_over":
            raise ValueError("match_over")
        choice = mickey if mickey in THROWS else self._rng.choice(THROWS)
        winner = decide_winner(player, choice)
        if winner == "player":
            self.player_score += 1
        elif winner == "mickey":
            self.mickey_score += 1
        self.last_player = player
        self.last_mickey = choice
        self.last_winner = winner
        if self.player_score >= self.wins_needed or self.mickey_score >= self.wins_needed:
            self.phase = "match_over"
        else:
            self.phase = "reveal"
        return self.to_state()

    def to_state(self) -> dict[str, Any]:
        phase: Phase = self.phase
        if phase == "reveal":
            # Web can throw again immediately; UI uses last_* for the reveal.
            phase = "awaiting_throw"
            if self.player_score >= self.wins_needed or self.mickey_score >= self.wins_needed:
                phase = "match_over"
        return {
            "type": "game.state",
            "game": "rps",
            "match_id": self.match_id,
            "you": self.last_player,
            "mickey": self.last_mickey,
            "winner": self.last_winner,
            "score": {"you": self.player_score, "mickey": self.mickey_score},
            "best_of": self.best_of,
            "phase": phase,
        }
