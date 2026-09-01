from __future__ import annotations

import asyncio

import pytest

from app.companion.errors import CompanionError
from app.companion.games.rps import COUNTDOWN_MS, RpsMatch, decide_winner
from app.companion.reactions import (
    DANCE_ACTIONS,
    dance_payload,
    rps_countdown_line,
    rps_plan,
    rps_recover_motion,
    rps_timeout_line,
    ttt_plan,
)


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
    winding = match.begin_round(mickey="scissors")
    assert winding["phase"] == "countdown"
    assert winding["winner"] is None
    assert winding["mickey"] is None
    assert winding["you"] is None
    match.commit("rock")
    first = match.reveal()
    assert first["winner"] == "player"
    assert first["score"]["you"] == 1
    assert first["phase"] == "awaiting_throw"
    match.begin_round(mickey="rock")
    match.commit("paper")
    second = match.reveal()
    assert second["phase"] == "match_over"
    assert second["match_winner"] == "player"
    assert second["score"]["you"] == 2
    with pytest.raises(ValueError):
        match.begin_round(mickey="rock")
    with pytest.raises(ValueError):
        match.commit("rock")


def test_draw_does_not_score() -> None:
    match = RpsMatch()
    match.begin_round(mickey="rock")
    match.commit("rock")
    state = match.reveal()
    assert state["winner"] == "draw"
    assert state["score"] == {"you": 0, "mickey": 0}
    assert state["phase"] == "awaiting_throw"


def test_countdown_hides_result_until_reveal() -> None:
    match = RpsMatch()
    match.start()
    mid = match.begin_round(mickey="paper")
    assert mid["phase"] == "countdown"
    assert mid["you"] is None
    assert mid["mickey"] is None
    assert mid["winner"] is None
    assert mid["committed"] is False
    assert mid["score"] == {"you": 0, "mickey": 0}
    assert mid["countdown_ms"] == COUNTDOWN_MS
    with pytest.raises(ValueError, match="countdown"):
        match.begin_round()
    locked = match.commit("rock")
    assert locked["phase"] == "countdown"
    assert locked["committed"] is True
    assert locked["you"] is None
    out = match.reveal()
    assert out["mickey"] == "paper"
    assert out["you"] == "rock"
    assert out["winner"] == "mickey"
    assert out["score"]["mickey"] == 1
    assert out["phase"] == "awaiting_throw"
    assert match.reveal()["winner"] == "mickey"


def test_timeout_does_not_score() -> None:
    match = RpsMatch()
    match.begin_round(mickey="rock")
    state = match.reveal()
    assert state["timeout"] is True
    assert state["winner"] is None
    assert state["you"] is None
    assert state["phase"] == "awaiting_throw"
    assert state["score"] == {"you": 0, "mickey": 0}


def test_abort_round_returns_to_awaiting() -> None:
    match = RpsMatch()
    match.begin_round(mickey="paper")
    match.commit("rock")
    out = match.abort_round()
    assert out["phase"] == "awaiting_throw"
    assert out["winner"] is None
    assert match.player_score == 0


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
    assert win.countdown_line == rps_countdown_line()
    lose = rps_plan("player")
    assert lose.end_emotion == "sad"
    assert lose.motion["action"] == "sit"
    draw = rps_plan("draw")
    assert draw.end_emotion == "confused"
    assert rps_recover_motion()["action"] == "home"


def test_rps_plan_match_over_appends_closer() -> None:
    line = rps_plan("mickey", match_over=True).line
    assert "နိုင်" in line


def test_rps_countdown_line() -> None:
    line = rps_countdown_line()
    assert "Rock" in line
    assert "Paper" in line
    assert "Scissors" in line
    assert rps_timeout_line()


def test_ttt_plan_motions_match_rps() -> None:
    win = ttt_plan("mickey")
    assert win.end_emotion == "happy"
    assert win.motion["action"] == "jump"
    lose = ttt_plan("player")
    assert lose.end_emotion == "sad"
    assert lose.motion["action"] == "sit"
    draw = ttt_plan("draw")
    assert draw.end_emotion == "confused"
    assert draw.motion["action"] == "home"


@pytest.mark.asyncio
async def test_wait_for_throw_unblocks_on_commit() -> None:
    match = RpsMatch()
    match.begin_round(mickey="rock")
    waiter = asyncio.create_task(match.wait_for_throw(1.0))
    await asyncio.sleep(0)
    match.commit("paper")
    assert await waiter is True
