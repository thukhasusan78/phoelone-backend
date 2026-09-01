from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Mark = Literal["x", "o"]
Winner = Literal["player", "mickey", "draw"]
Phase = Literal["your_turn", "mickey_turn", "match_over"]
Difficulty = Literal["easy", "medium"]

PLAYER: Mark = "x"
MICKEY: Mark = "o"
EASY_RANDOM = 0.4

_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)


def empty_board() -> list[Mark | None]:
    return [None] * 9


def empty_cells(board: list[Mark | None]) -> list[int]:
    return [i for i, mark in enumerate(board) if mark is None]


def winner_of(board: list[Mark | None]) -> Winner | None:
    for a, b, c in _LINES:
        mark = board[a]
        if mark and mark == board[b] == board[c]:
            return "player" if mark == PLAYER else "mickey"
    if all(board):
        return "draw"
    return None


def minimax(
    board: list[Mark | None], to_move: Mark
) -> tuple[int, int | None]:
    outcome = winner_of(board)
    if outcome == "mickey":
        return 1, None
    if outcome == "player":
        return -1, None
    if outcome == "draw":
        return 0, None
    cells = empty_cells(board)
    if to_move == MICKEY:
        best_score = -2
        best_cell = cells[0]
        for cell in cells:
            board[cell] = MICKEY
            score, _ = minimax(board, PLAYER)
            board[cell] = None
            if score > best_score:
                best_score = score
                best_cell = cell
            if best_score == 1:
                break
        return best_score, best_cell
    best_score = 2
    best_cell = cells[0]
    for cell in cells:
        board[cell] = PLAYER
        score, _ = minimax(board, MICKEY)
        board[cell] = None
        if score < best_score:
            best_score = score
            best_cell = cell
        if best_score == -1:
            break
    return best_score, best_cell


def pick_mickey_cell(
    board: list[Mark | None],
    difficulty: Difficulty,
    rng: random.Random,
) -> int:
    cells = empty_cells(board)
    if not cells:
        raise ValueError("no_moves")
    _, best = minimax(board, MICKEY)
    if best is None:
        return cells[0]
    if difficulty == "easy" and rng.random() < EASY_RANDOM:
        return rng.choice(cells)
    return best


@dataclass
class TttMatch:
    match_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    difficulty: Difficulty = "medium"
    board: list[Mark | None] = field(default_factory=empty_board)
    phase: Phase = "your_turn"
    winner: Winner | None = None
    last_cell: int | None = None
    _pending: int | None = field(default=None, repr=False, compare=False)
    _rng: random.Random = field(default_factory=random.Random)

    def start(self, difficulty: str = "medium") -> dict[str, Any]:
        level: Difficulty = "easy" if difficulty == "easy" else "medium"
        self.match_id = uuid.uuid4().hex[:12]
        self.difficulty = level
        self.board = empty_board()
        self.phase = "your_turn"
        self.winner = None
        self.last_cell = None
        self._pending = None
        return self.to_state()

    def play(self, cell: int) -> dict[str, Any]:
        if self.phase == "match_over":
            raise ValueError("match_over")
        if self.phase != "your_turn":
            raise ValueError("not_your_turn")
        if not isinstance(cell, int) or cell < 0 or cell > 8:
            raise ValueError("bad_cell")
        if self.board[cell] is not None:
            raise ValueError("occupied")
        self.board[cell] = PLAYER
        self.last_cell = cell
        self.winner = winner_of(self.board)
        if self.winner is not None:
            self.phase = "match_over"
        return self.to_state()

    def begin_mickey(self) -> dict[str, Any]:
        if self.phase == "match_over":
            raise ValueError("match_over")
        if self.phase != "your_turn":
            raise ValueError("not_your_turn")
        if self.winner is not None:
            raise ValueError("match_over")
        self._pending = pick_mickey_cell(self.board, self.difficulty, self._rng)
        self.phase = "mickey_turn"
        return self.to_state()

    def apply_pending(self) -> dict[str, Any]:
        cell = self._pending
        if cell is None:
            return self.to_state()
        if self.board[cell] is not None:
            self._pending = None
            if self.phase == "mickey_turn" and self.winner is None:
                self.phase = "your_turn"
            return self.to_state()
        self.board[cell] = MICKEY
        self.last_cell = cell
        self._pending = None
        self.winner = winner_of(self.board)
        if self.winner is not None:
            self.phase = "match_over"
        else:
            self.phase = "your_turn"
        return self.to_state()

    def abort_think(self) -> dict[str, Any]:
        return self.apply_pending()

    def to_state(self) -> dict[str, Any]:
        turn: str | None
        if self.phase == "your_turn":
            turn = "player"
        elif self.phase == "mickey_turn":
            turn = "mickey"
        else:
            turn = None
        return {
            "type": "game.state",
            "game": "ttt",
            "match_id": self.match_id,
            "board": list(self.board),
            "turn": turn,
            "phase": self.phase,
            "winner": self.winner,
            "last_cell": self.last_cell,
            "difficulty": self.difficulty,
        }
