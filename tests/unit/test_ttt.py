from __future__ import annotations

import pytest

from app.companion.games.ttt import (
    TttMatch,
    minimax,
    pick_mickey_cell,
    winner_of,
)


def test_row_col_diag_and_draw() -> None:
    row = ["x", "x", "x", "o", "o", None, None, None, None]
    assert winner_of(row) == "player"
    col = ["o", "x", None, "o", "x", None, "o", None, "x"]
    assert winner_of(col) == "mickey"
    diag = ["x", "o", None, None, "x", "o", None, None, "x"]
    assert winner_of(diag) == "player"
    draw = ["x", "o", "x", "x", "o", "o", "o", "x", "x"]
    assert winner_of(draw) == "draw"
    open_board = ["x", None, None, None, "o", None, None, None, None]
    assert winner_of(open_board) is None


def test_play_rejects_illegal_occupied_and_wrong_turn() -> None:
    match = TttMatch()
    match.start()
    match.play(0)
    with pytest.raises(ValueError, match="occupied"):
        match.play(0)
    match.begin_mickey()
    with pytest.raises(ValueError, match="not_your_turn"):
        match.play(1)
    match = TttMatch()
    match.start()
    with pytest.raises(ValueError, match="bad_cell"):
        match.play(9)
    over = TttMatch()
    over.board = ["x", "x", "x", "o", "o", None, None, None, None]
    over.phase = "match_over"
    over.winner = "player"
    with pytest.raises(ValueError, match="match_over"):
        over.play(5)


def test_medium_blocks_open_two_in_a_row() -> None:
    board = ["x", None, "x", None, "o", None, None, None, None]
    _, cell = minimax(board, "o")
    assert cell == 1


def test_medium_blocks_fork_after_opposite_corners() -> None:
    board = ["x", None, None, None, "o", None, None, None, "x"]
    _, cell = minimax(board, "o")
    assert cell in {1, 3, 5, 7}


def test_easy_seeded_rng_can_diverge_from_minimax() -> None:
    board = ["x", None, "x", None, "o", None, None, None, None]
    match = TttMatch()
    match.start("easy")
    match._rng.seed(0)
    picks = {pick_mickey_cell(board, "easy", match._rng) for _ in range(40)}
    assert 1 in picks
    assert picks - {1}


def test_full_play_apply_pending_and_abort() -> None:
    match = TttMatch()
    start = match.start("medium")
    assert start["phase"] == "your_turn"
    assert start["board"] == [None] * 9
    assert start["turn"] == "player"
    first = match.play(0)
    assert first["board"][0] == "x"
    assert first["last_cell"] == 0
    thinking = match.begin_mickey()
    assert thinking["phase"] == "mickey_turn"
    assert thinking["turn"] == "mickey"
    assert match._pending is not None
    placed = match.apply_pending()
    assert placed["board"][placed["last_cell"]] == "o"
    assert placed["phase"] in {"your_turn", "match_over"}
    match.apply_pending()
    second = TttMatch()
    second.start()
    second.play(4)
    second.begin_mickey()
    aborted = second.abort_think()
    assert aborted["board"].count("o") == 1
    assert second._pending is None


def test_player_win_skips_mickey_turn() -> None:
    match = TttMatch()
    match.start()
    match.board = ["x", "x", None, "o", "o", None, None, None, None]
    state = match.play(2)
    assert state["winner"] == "player"
    assert state["phase"] == "match_over"
    assert state["turn"] is None
    with pytest.raises(ValueError, match="match_over"):
        match.begin_mickey()
