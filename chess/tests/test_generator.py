import io
import json
import xml.etree.ElementTree as ET

import chess
import pytest
from PIL import Image

import aag_chess.generator as generator_module
from aag_chess import GENERATOR_VERSION
from aag_chess.generator import (
    Candidate,
    GenerationRequest,
    GenerationRequestError,
    candidate_sequence,
    generate_batch,
    generate_puzzle,
    render_generated_puzzle,
)
from aag_chess.position import normalized_fen, parse_board, puzzle_hash
from aag_chess.renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    RenderRequest,
    render_png,
    render_svg,
)


def request(**changes) -> GenerationRequest:
    values = {
        "mate_moves": 1,
        "side_to_move": "white",
        "difficulty": "easy",
        "seed": 1729,
        "max_candidate_attempts": 8,
    }
    values.update(changes)
    return GenerationRequest(**values)


@pytest.fixture(scope="module")
def generated_matrix():
    results = {}
    for mate_moves in (1, 2, 3):
        for side_offset, side in enumerate(("white", "black")):
            results[mate_moves, side] = generate_puzzle(
                request(
                    mate_moves=mate_moves,
                    side_to_move=side,
                    difficulty=("easy", "medium", "hard")[mate_moves - 1],
                    seed=4000 + mate_moves + 100 * side_offset,
                )
            )
    assert all(result.success for result in results.values())
    return results


@pytest.fixture(scope="module")
def generated_by_depth(generated_matrix):
    return {
        mate_moves: generated_matrix[mate_moves, "white"]
        for mate_moves in (1, 2, 3)
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"mate_moves": 0}, "mate_moves"),
        ({"mate_moves": 4}, "mate_moves"),
        ({"mate_moves": True}, "mate_moves"),
        ({"side_to_move": "w"}, "side_to_move"),
        ({"side_to_move": "WHITE"}, "side_to_move"),
        ({"difficulty": "expert"}, "difficulty"),
        ({"difficulty": "Easy"}, "difficulty"),
        ({"seed": -1}, "seed"),
        ({"seed": 2**63}, "seed"),
        ({"seed": True}, "seed"),
        ({"max_candidate_attempts": 0}, "max_candidate_attempts"),
        ({"max_candidate_attempts": 10_001}, "max_candidate_attempts"),
        ({"max_candidate_attempts": True}, "max_candidate_attempts"),
        ({"require_unique_key": 1}, "require_unique_key"),
        ({"dual_policy": "ignore"}, "dual_policy"),
        ({"verifier_max_nodes": 0}, "verifier_max_nodes"),
        ({"verifier_max_seconds": 0}, "verifier_max_seconds"),
        ({"density": "crowded"}, "density"),
    ],
)
def test_request_validation(changes, message):
    with pytest.raises(GenerationRequestError, match=message):
        request(**changes)


@pytest.mark.parametrize("count", [0, 101, True, 1.5])
def test_batch_count_validation(count):
    with pytest.raises(GenerationRequestError, match="count"):
        generate_batch(request(), count)  # type: ignore[arg-type]


def test_candidate_sequence_is_deterministic_and_versioned():
    generation_request = request(mate_moves=2, seed=834729, difficulty="medium")
    first = candidate_sequence(generation_request)
    second = candidate_sequence(generation_request)
    assert first == second
    assert len(first) == 16
    assert [candidate.ordinal for candidate in first] == list(range(1, 17))
    assert len({candidate.fen for candidate in first}) == 16
    assert GENERATOR_VERSION == "aag-deterministic-generator-v1.2"


def test_seed_and_difficulty_can_change_candidate_order():
    seed_one = candidate_sequence(request(seed=1))
    seed_two = candidate_sequence(request(seed=2))
    hard = candidate_sequence(request(seed=1, difficulty="hard"))
    assert [candidate.strategy for candidate in seed_one] != [
        candidate.strategy for candidate in seed_two
    ]
    assert [candidate.strategy for candidate in seed_one] != [
        candidate.strategy for candidate in hard
    ]


def test_identical_seed_produces_identical_success():
    generation_request = request(mate_moves=2, seed=9981, difficulty="medium")
    first = generate_puzzle(generation_request)
    second = generate_puzzle(generation_request)
    assert first.success and second.success
    assert first.puzzle is not None and second.puzzle is not None
    assert first.puzzle.normalized_fen == second.puzzle.normalized_fen
    assert first.puzzle.puzzle_identity == second.puzzle.puzzle_identity
    assert (
        first.puzzle.verification.certificate_sha256
        == second.puzzle.verification.certificate_sha256
    )
    assert first.accounting == second.accounting


def test_different_seeds_can_produce_different_results():
    first = generate_puzzle(request(seed=1))
    second = generate_puzzle(request(seed=3))
    assert first.success and second.success
    assert first.puzzle is not None and second.puzzle is not None
    assert first.puzzle.puzzle_identity != second.puzzle.puzzle_identity


def test_explicit_bound_returns_structured_failure_without_expansion():
    result = generate_puzzle(
        request(
            mate_moves=2,
            seed=7,
            max_candidate_attempts=1,
            verifier_max_nodes=1,
            verifier_max_seconds=1,
        )
    )
    assert not result.success
    assert result.puzzle is None
    assert result.failure_reason == "candidate_attempt_budget_exhausted"
    assert result.accounting.candidates_attempted == 1
    assert result.accounting.verifier_rejected == 1
    assert result.accounting.accepted == 0
    assert result.rejections[0].reason == "verification node budget exceeded"


def test_finite_stream_exhaustion_is_reported_without_repetition():
    result = generate_batch(request(max_candidate_attempts=20), 9)
    assert not result.success
    assert result.failure_reason == "candidate_stream_exhausted"
    assert result.accounting.candidates_attempted == 8
    assert result.accounting.accepted == 8
    assert len(result.puzzles) == 8


def test_structural_rejection_is_accounted_before_real_acceptance(monkeypatch):
    generation_request = request(max_candidate_attempts=2)
    accepted_candidate = candidate_sequence(generation_request)[0]
    controlled = (
        Candidate(
            1,
            "invalid-adjacent-kings",
            "8/8/8/8/8/8/4k3/4K3 w - - 0 1",
        ),
        Candidate(2, accepted_candidate.strategy, accepted_candidate.fen),
    )
    monkeypatch.setattr(generator_module, "candidate_sequence", lambda unused: controlled)
    result = generate_puzzle(generation_request)
    assert result.success
    assert result.accounting.candidates_attempted == 2
    assert result.accounting.structurally_rejected == 1
    assert result.accounting.accepted == 1
    assert result.rejections[0].stage == "structural_screen"


def test_forbid_policy_rejects_classified_mate_three_duals():
    result = generate_puzzle(
        request(
            mate_moves=3,
            difficulty="hard",
            seed=30301,
            max_candidate_attempts=1,
            dual_policy="forbid",
        )
    )
    assert not result.success
    assert result.accounting.verifier_rejected == 1
    assert result.rejections[0].reason == "second_move_duals_forbidden"


@pytest.mark.parametrize("mate_moves", [1, 2, 3])
def test_generated_depth_is_exact_verified_canonical_and_unique(
    generated_by_depth, mate_moves
):
    result = generated_by_depth[mate_moves]
    assert result.success
    assert result.puzzle is not None
    puzzle = result.puzzle
    verification = puzzle.verification
    assert verification.accepted is True
    assert verification.requested_mate_moves == mate_moves
    assert verification.exact_mate_plies == 2 * mate_moves - 1
    assert verification.unique_key is True
    assert len(verification.key_moves) == 1
    assert verification.normalized_fen == puzzle.normalized_fen
    assert normalized_fen(parse_board(puzzle.normalized_fen)) == puzzle.normalized_fen
    assert puzzle.puzzle_identity == verification.puzzle_hash
    assert len(verification.certificate_sha256 or "") == 64


@pytest.mark.parametrize("mate_moves", [1, 2, 3])
@pytest.mark.parametrize(
    ("side", "expected_turn"), [("white", chess.WHITE), ("black", chess.BLACK)]
)
def test_both_sides_to_move_are_supported(
    generated_matrix, mate_moves, side, expected_turn
):
    result = generated_matrix[mate_moves, side]
    assert result.success and result.puzzle is not None
    assert parse_board(result.puzzle.normalized_fen).turn == expected_turn


def test_identity_is_existing_stable_canonical_problem_hash(generated_by_depth):
    puzzle = generated_by_depth[2].puzzle
    assert puzzle is not None
    board = parse_board(f"{puzzle.normalized_fen} 19 42")
    assert puzzle.puzzle_identity == puzzle_hash(board, 2)
    assert puzzle.provenance == "deterministic_composition_template"
    assert puzzle.provenance_verified is False


def test_batch_rejects_duplicate_identities_and_preserves_order(monkeypatch):
    generation_request = request(seed=55, max_candidate_attempts=3)
    originals = candidate_sequence(generation_request)
    controlled = (
        Candidate(1, originals[0].strategy, originals[0].fen),
        Candidate(2, "intentional-duplicate", originals[0].fen),
        Candidate(3, originals[1].strategy, originals[1].fen),
    )
    monkeypatch.setattr(generator_module, "candidate_sequence", lambda unused: controlled)
    result = generate_batch(generation_request, 2)
    assert result.success
    assert result.accounting.candidates_attempted == 3
    assert result.accounting.duplicate_rejected == 1
    assert result.accounting.accepted == 2
    assert len({puzzle.puzzle_identity for puzzle in result.puzzles}) == 2
    assert [puzzle.candidate.ordinal for puzzle in result.puzzles] == [1, 3]
    assert result.rejections[0].stage == "duplicate"


def test_batch_order_is_deterministic_and_unique():
    generation_request = request(seed=20260830, max_candidate_attempts=8)
    first = generate_batch(generation_request, 3)
    second = generate_batch(generation_request, 3)
    assert first.success and second.success
    first_ids = [puzzle.puzzle_identity for puzzle in first.puzzles]
    second_ids = [puzzle.puzzle_identity for puzzle in second.puzzles]
    assert first_ids == second_ids
    assert len(set(first_ids)) == 3
    assert first.accounting == second.accounting


def test_generated_puzzle_passes_directly_to_existing_render_request(generated_by_depth):
    puzzle = generated_by_depth[1].puzzle
    assert puzzle is not None
    render_request = puzzle.to_render_request("phase3-direct")
    assert isinstance(render_request, RenderRequest)
    assert render_request.verification is puzzle.verification
    assert render_request.difficulty == puzzle.request.difficulty
    assert ET.fromstring(render_svg(render_request)).tag.endswith("svg")
    image = Image.open(io.BytesIO(render_png(render_request)))
    image.load()
    assert image.size == (CARD_WIDTH, CARD_HEIGHT)


def test_generated_svg_png_are_valid_and_do_not_leak_solution(
    tmp_path, generated_by_depth
):
    puzzle = generated_by_depth[3].puzzle
    assert puzzle is not None
    artifacts = render_generated_puzzle(
        puzzle,
        tmp_path,
        "generated-mate-3",
        puzzle_id="phase3-unsolved",
    )
    svg = artifacts.svg.path.read_bytes()
    png = artifacts.png.path.read_bytes()
    root = ET.fromstring(svg)
    image = Image.open(io.BytesIO(png))
    image.load()
    assert root.tag.endswith("svg")
    assert image.format == "PNG"
    assert image.size == (CARD_WIDTH, CARD_HEIGHT)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert artifacts.svg.sha256 and artifacts.png.sha256

    png_metadata = json.dumps(image.info, ensure_ascii=False)
    secrets = (
        *puzzle.verification.key_moves,
        puzzle.verification.certificate_sha256,
        json.dumps(puzzle.verification.proof, sort_keys=True),
    )
    for secret in secrets:
        assert secret is not None
        assert secret.encode("utf-8") not in svg
        assert secret not in png_metadata
    assert b"data-key" not in svg
    assert b"data-proof" not in svg
    assert b"data-certificate" not in svg
