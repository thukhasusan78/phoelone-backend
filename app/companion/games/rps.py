from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

THROWS = ("rock", "paper", "scissors")
Winner = Literal["player", "mickey", "draw"]
Phase = Literal["awaiting_throw", "countdown", "reveal", "match_over"]

# Hint for the web chant. The real reveal clock is countdown TTS finishing.
COUNTDOWN_MS = 2400
# Extra window after the chant so a last-moment tap still counts.
THROW_GRACE_S = 0.7

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
    round_id: str = ""
    _pending_player: str | None = None
    _pending_mickey: str | None = None
    _rng: random.Random = field(default_factory=random.Random)
    _throw_event: asyncio.Event | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.best_of < 1 or self.best_of % 2 == 0:
            self.best_of = 3

    @property
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1

    @property
    def match_winner(self) -> Winner | None:
        if self.player_score >= self.wins_needed:
            return "player"
        if self.mickey_score >= self.wins_needed:
            return "mickey"
        return None

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
        self.round_id = ""
        self._pending_player = None
        self._pending_mickey = None
        self._throw_event = None
        return self.to_state()

    def begin_round(self, *, mickey: str | None = None) -> dict[str, Any]:
        if self.phase == "match_over":
            raise ValueError("match_over")
        if self.phase == "countdown":
            raise ValueError("countdown")
        choice = mickey if mickey in THROWS else self._rng.choice(THROWS)
        self.round_id = uuid.uuid4().hex[:8]
        self.last_player = None
        self.last_mickey = None
        self.last_winner = None
        self._pending_player = None
        self._pending_mickey = choice
        self._throw_event = asyncio.Event()
        self.phase = "countdown"
        return self.to_state()

    def commit(self, player: str) -> dict[str, Any]:
        if player not in THROWS:
            raise ValueError("invalid throw")
        if self.phase != "countdown":
            raise ValueError("not_counting")
        self._pending_player = player
        event = self._throw_event
        if event is not None and not event.is_set():
            event.set()
        return self.to_state()

    def abort_round(self) -> dict[str, Any]:
        if self.phase != "countdown":
            return self.to_state()
        self.phase = "awaiting_throw"
        self.last_player = None
        self.last_mickey = None
        self.last_winner = None
        self._pending_player = None
        self._pending_mickey = None
        event = self._throw_event
        if event is not None and not event.is_set():
            event.set()
        return self.to_state()

    async def wait_for_throw(self, timeout_s: float) -> bool:
        if self._pending_player in THROWS:
            return True
        event = self._throw_event
        if event is None:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout_s)
        except asyncio.TimeoutError:
            return self._pending_player in THROWS
        return self._pending_player in THROWS

    def reveal(self) -> dict[str, Any]:
        if self.phase != "countdown":
            return self.to_state()
        player = self._pending_player
        mickey = self._pending_mickey
        self._pending_player = None
        self._pending_mickey = None
        if player not in THROWS or mickey not in THROWS:
            self.phase = "awaiting_throw"
            self.last_player = None
            self.last_mickey = None
            self.last_winner = None
            state = self.to_state()
            state["timeout"] = True
            return state
        winner = decide_winner(player, mickey)
        self.last_player = player
        self.last_mickey = mickey
        self.last_winner = winner
        if winner == "player":
            self.player_score += 1
        elif winner == "mickey":
            self.mickey_score += 1
        if self.match_winner is not None:
            self.phase = "match_over"
        else:
            self.phase = "reveal"
        return self.to_state()

    def to_state(self) -> dict[str, Any]:
        phase: Phase = self.phase
        if phase == "countdown":
            return {
                "type": "game.state",
                "game": "rps",
                "match_id": self.match_id,
                "round_id": self.round_id,
                "you": None,
                "mickey": None,
                "winner": None,
                "score": {"you": self.player_score, "mickey": self.mickey_score},
                "best_of": self.best_of,
                "wins_needed": self.wins_needed,
                "phase": "countdown",
                "countdown_ms": COUNTDOWN_MS,
                "committed": self._pending_player in THROWS,
                "match_winner": None,
            }
        if phase == "reveal":
            # Web shows the last throws; the next chant is a new countdown frame.
            phase = "awaiting_throw"
        match_over = phase == "match_over" or self.match_winner is not None
        if match_over:
            phase = "match_over"
        return {
            "type": "game.state",
            "game": "rps",
            "match_id": self.match_id,
            "round_id": self.round_id or None,
            "you": self.last_player,
            "mickey": self.last_mickey,
            "winner": self.last_winner,
            "score": {"you": self.player_score, "mickey": self.mickey_score},
            "best_of": self.best_of,
            "wins_needed": self.wins_needed,
            "phase": phase,
            "match_winner": self.match_winner if phase == "match_over" else None,
        }
