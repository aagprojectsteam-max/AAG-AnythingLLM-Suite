import chess
import pytest

from aag_chess.position import PositionError, inspect_position, normalized_fen, parse_board
from aag_chess.verifier import MateVerifier, principal_variation


MATE_1 = "3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1"
MATE_2 = "2k5/8/8/K7/8/8/8/3Q4 w - - 0 1"
MATE_3_WITH_DUALS = "8/8/8/7K/8/8/1Q6/4k3 w - - 0 1"
MULTIPLE_KEY_MATE_1 = "7k/8/6QK/8/8/8/8/8 w - - 0 1"
NO_MATE = "7k/8/8/8/8/8/8/K7 w - - 0 1"
STALEMATE = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
CHECKMATE = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"


def verifier() -> MateVerifier:
    return MateVerifier(max_nodes=500_000, max_seconds=10)


@pytest.mark.parametrize(
    ("fen", "mate_moves", "policy", "key", "plies"),
    [
        (MATE_1, 1, "forbid", "g6g8", 1),
        (MATE_2, 2, "forbid", "a5b6", 3),
        (MATE_3_WITH_DUALS, 3, "warning", "h5g4", 5),
    ],
)
def test_positive_exact_mate_fixtures(fen, mate_moves, policy, key, plies):
    result = verifier().verify(fen, mate_moves, dual_policy=policy)
    assert result.accepted
    assert result.exact_mate_plies == plies
    assert result.unique_key
    assert result.key_moves == (key,)
    assert result.proof is not None
    assert len(result.certificate_sha256) == 64


def test_mate_three_duals_are_classified_and_forbidden_by_default():
    warning = verifier().verify(MATE_3_WITH_DUALS, 3, dual_policy="warning")
    forbidden = verifier().verify(MATE_3_WITH_DUALS, 3)
    assert warning.accepted
    assert warning.reason == "accepted_with_dual_warning"
    assert warning.duals
    assert not forbidden.accepted
    assert forbidden.reason == "second_move_duals_forbidden"


def test_no_mate_and_stalemate_are_not_accepted():
    assert verifier().verify(NO_MATE, 1).reason == "no_forced_mate_within_bound"
    stale = parse_board(STALEMATE)
    assert stale.is_stalemate() and not stale.is_checkmate()
    assert verifier().verify(STALEMATE, 1).reason == "no_forced_mate_within_bound"


def test_checkmate_is_distinct_from_stalemate():
    mate = parse_board(CHECKMATE)
    assert mate.is_checkmate() and not mate.is_stalemate()


def test_exact_depth_rejects_a_shorter_mate():
    result = verifier().verify(MATE_1, 2, dual_policy="warning")
    assert not result.accepted
    assert result.exact_mate_plies == 1
    assert result.reason == "mate_distance_not_exact"


def test_multiple_key_rejected_and_unique_key_accepted():
    multiple = verifier().verify(MULTIPLE_KEY_MATE_1, 1)
    unique = verifier().verify(MATE_1, 1)
    assert not multiple.accepted
    assert multiple.reason == "key_not_unique"
    assert len(multiple.key_moves) == 3
    assert unique.accepted and unique.unique_key


def test_proof_certificate_is_stable():
    first = verifier().verify(MATE_2, 2)
    second = verifier().verify(MATE_2, 2)
    assert first.certificate_sha256 == second.certificate_sha256
    assert first.proof == second.proof
    assert principal_variation(first.proof) == ["a5b6", "c8b8", "d1d8"]


def test_structurally_invalid_position_is_rejected():
    adjacent_kings = "8/8/8/8/8/8/4k3/4K3 w - - 0 1"
    result = verifier().verify(adjacent_kings, 1)
    assert not result.accepted
    assert not result.structurally_valid
    with pytest.raises(PositionError):
        parse_board(adjacent_kings)


def test_normalization_omits_only_counters_and_hash_is_stable():
    board_a = parse_board(MATE_2)
    board_b = parse_board(MATE_2.replace("0 1", "12 37"))
    assert normalized_fen(board_a) == normalized_fen(board_b)
    assert inspect_position(MATE_2, 2).puzzle_hash == inspect_position(
        MATE_2.replace("0 1", "12 37"), 2
    ).puzzle_hash


def test_illegal_en_passant_field_is_safely_normalized_away():
    board = chess.Board(
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    )
    assert board.is_valid()
    assert normalized_fen(board).endswith(" -")


def test_budget_failure_is_structured():
    result = MateVerifier(max_nodes=1, max_seconds=1).verify(MATE_2, 2)
    assert not result.accepted
    assert result.reason == "verification node budget exceeded"


@pytest.mark.parametrize("depth", [0, 4, -1])
def test_unsupported_depth(depth):
    with pytest.raises(ValueError):
        verifier().verify(MATE_1, depth)
