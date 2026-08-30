from __future__ import annotations

import pytest

from app.companion.errors import CompanionError
from app.companion.games.rps import RpsMatch, decide_winner
from app.companion.reactions import DANCE_ACTIONS, dance_payload, rps_plan


def test_decide_winner() -> None:
    assert decide_winner("rock", "scissors") == "player"
    assert decide_winner("rock", "paper") == "mickey"
    assert decide_winner("rock", "rock") == "draw"
    assert decide_winner("scissors", "paper") == "player"


def test_best_of_three_match() -> None:
    match = RpsMatch()
    match._rng.seed(1)
    start = match.start(3)
    assert start["phase"] == "awaiting_throw"
    assert start["score"] == {"you": 0, "mickey": 0}
    first = match.move("rock", mickey="scissors")
    assert first["winner"] == "player"
    assert first["score"]["you"] == 1
    assert first["phase"] == "awaiting_throw"
    second = match.move("paper", mickey="rock")
    assert second["phase"] == "match_over"
    assert second["score"]["you"] == 2
    with pytest.raises(ValueError):
        match.move("rock", mickey="rock")


def test_draw_does_not_score() -> None:
    match = RpsMatch()
    state = match.move("rock", mickey="rock")
    assert state["winner"] == "draw"
    assert state["score"] == {"you": 0, "mickey": 0}
    assert state["phase"] == "awaiting_throw"


def test_dance_allowlist() -> None:
    payload = dance_payload("walk")
    assert payload["action"] == "walk"
    assert payload["steps"] == 2
    assert payload["speed"] == 2000
    assert "jump" in DANCE_ACTIONS
    with pytest.raises(CompanionError) as exc:
        dance_payload("hands_up")
    assert exc.value.code == "invalid"
    with pytest.raises(CompanionError):
        dance_payload("backflip")


def test_rps_plan_emotions() -> None:
    win = rps_plan("mickey")
    assert win.end_emotion == "happy"
    assert win.motion["action"] == "jump"
    lose = rps_plan("player")
    assert lose.end_emotion == "sad"
    draw = rps_plan("draw")
    assert draw.end_emotion == "confused"
