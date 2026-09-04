"""Deterministic, bounded candidate generation behind the mate verifier.

This module constructs and orders candidate positions. It deliberately does
not decide chess correctness: only an accepted result returned by
``MateVerifier.verify()`` can become a :class:`GeneratedPuzzle`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import chess

from . import GENERATOR_VERSION
from .difficulty import DifficultyAssessment, assess_difficulty
from .density import DensityPreference, validate_density_preference
from .position import PositionError, normalized_fen, parse_board, puzzle_hash
from .renderer import (
    ArtifactMetadata,
    RenderRequest,
    write_png_artifact,
    write_svg_artifact,
)
from .verifier import MateVerifier, VerificationResult


SideToMove = Literal["white", "black"]
Difficulty = Literal["easy", "medium", "hard"]
DualPolicy = Literal["forbid", "warning", "allow"]

_VALID_SIDES = frozenset({"white", "black"})
_VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_VALID_DUAL_POLICIES = frozenset({"forbid", "warning", "allow"})
_MAX_SEED = 2**63 - 1
_MAX_CANDIDATE_ATTEMPTS = 10_000
_MAX_BATCH_SIZE = 100


class GenerationRequestError(ValueError):
    """A generation request violates the bounded public contract."""


@dataclass(frozen=True)
class GenerationRequest:
    """Complete reproducible configuration for one candidate stream.

    ``dual_policy="warning"`` is the default because Phase 1 explicitly
    classifies and accepts exact problems with post-key duals under that
    policy. Root-key uniqueness remains required by default.
    """

    mate_moves: int
    side_to_move: SideToMove
    difficulty: Difficulty
    seed: int
    max_candidate_attempts: int
    require_unique_key: bool = True
    dual_policy: DualPolicy = "warning"
    verifier_max_nodes: int = 500_000
    verifier_max_seconds: float = 10.0
    density: DensityPreference = "auto"

    def __post_init__(self) -> None:
        if isinstance(self.mate_moves, bool) or self.mate_moves not in (1, 2, 3):
            raise GenerationRequestError("mate_moves must be 1, 2, or 3")
        if (
            not isinstance(self.side_to_move, str)
            or self.side_to_move not in _VALID_SIDES
        ):
            raise GenerationRequestError("side_to_move must be white or black")
        if (
            not isinstance(self.difficulty, str)
            or self.difficulty not in _VALID_DIFFICULTIES
        ):
            raise GenerationRequestError("difficulty must be easy, medium, or hard")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or not 0 <= self.seed <= _MAX_SEED
        ):
            raise GenerationRequestError(
                f"seed must be an integer between 0 and {_MAX_SEED}"
            )
        if (
            isinstance(self.max_candidate_attempts, bool)
            or not isinstance(self.max_candidate_attempts, int)
            or not 1 <= self.max_candidate_attempts <= _MAX_CANDIDATE_ATTEMPTS
        ):
            raise GenerationRequestError(
                "max_candidate_attempts must be an integer between 1 and 10,000"
            )
        if not isinstance(self.require_unique_key, bool):
            raise GenerationRequestError("require_unique_key must be a boolean")
        if (
            not isinstance(self.dual_policy, str)
            or self.dual_policy not in _VALID_DUAL_POLICIES
        ):
            raise GenerationRequestError("dual_policy must be forbid, warning, or allow")
        if (
            isinstance(self.verifier_max_nodes, bool)
            or not isinstance(self.verifier_max_nodes, int)
            or not 1 <= self.verifier_max_nodes <= 5_000_000
        ):
            raise GenerationRequestError(
                "verifier_max_nodes must be an integer between 1 and 5,000,000"
            )
        if (
            isinstance(self.verifier_max_seconds, bool)
            or not isinstance(self.verifier_max_seconds, (int, float))
            or not 0.01 <= self.verifier_max_seconds <= 60
        ):
            raise GenerationRequestError(
                "verifier_max_seconds must be between 0.01 and 60"
            )
        try:
            validate_density_preference(self.density)
        except ValueError as exc:
            raise GenerationRequestError(str(exc)) from exc


@dataclass(frozen=True)
class Candidate:
    ordinal: int
    strategy: str
    fen: str


@dataclass(frozen=True)
class CandidateRejection:
    ordinal: int
    strategy: str
    stage: Literal["structural_screen", "duplicate", "verifier", "difficulty"]
    reason: str


@dataclass(frozen=True)
class GenerationAccounting:
    candidates_attempted: int
    structurally_rejected: int
    duplicate_rejected: int
    verifier_rejected: int
    difficulty_rejected: int
    accepted: int
    stockfish_rejected: int = 0
    symmetry_duplicate_rejected: int = 0
    verifier_submitted: int = 0
    stockfish_nodes: int = 0
    stockfish_analysis_ms: int = 0
    verifier_analysis_ms: int = 0
    quality_rejected: int = 0
    similarity_rejected: int = 0


@dataclass(frozen=True)
class GeneratedPuzzle:
    request: GenerationRequest
    candidate: Candidate
    attempt_number: int
    normalized_fen: str
    puzzle_identity: str
    verification: VerificationResult
    difficulty: DifficultyAssessment
    provenance: str = "deterministic_composition_template"
    provenance_verified: bool = False
    generator_version: str = GENERATOR_VERSION
    source_family: str = "builtin"
    motif: str = "mixed_other"
    quality_score: int = 0
    diversity_fingerprint: str = ""

    def to_render_request(self, puzzle_id: str | None = None) -> RenderRequest:
        """Create the existing Phase 2 request with the real verification."""

        public_id = puzzle_id or f"phase3-{self.puzzle_identity[:16]}"
        return RenderRequest(
            verification=self.verification,
            puzzle_id=public_id,
            difficulty=self.difficulty.label,
        )


@dataclass(frozen=True)
class GenerationResult:
    success: bool
    puzzle: GeneratedPuzzle | None
    accounting: GenerationAccounting
    failure_reason: str | None
    rejections: tuple[CandidateRejection, ...]
    generator_version: str = GENERATOR_VERSION


@dataclass(frozen=True)
class BatchGenerationResult:
    success: bool
    requested_count: int
    puzzles: tuple[GeneratedPuzzle, ...]
    accounting: GenerationAccounting
    failure_reason: str | None
    rejections: tuple[CandidateRejection, ...]
    generator_version: str = GENERATOR_VERSION


@dataclass(frozen=True)
class RenderedPuzzleArtifacts:
    svg: ArtifactMetadata
    png: ArtifactMetadata


# Each source is an arbitrary composition template. Structural validity and
# mate truth are intentionally not assumed here; every emitted variant still
# crosses the real Phase 1 verifier boundary.
_BASE_TEMPLATES: dict[int, tuple[tuple[str, str], ...]] = {
    1: (("queen-net-m1", "3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1"),),
    2: (
        ("queen-net-m2-a", "2k5/8/8/K7/8/8/8/3Q4 w - - 0 1"),
        ("queen-net-m2-b", "2k5/8/8/1K6/8/8/8/3Q4 w - - 0 1"),
    ),
    3: (("queen-net-m3", "8/8/8/7K/8/8/1Q6/4k3 w - - 0 1"),),
}


def _identity(file_index: int, rank_index: int) -> tuple[int, int]:
    return file_index, rank_index


def _rotate_90(file_index: int, rank_index: int) -> tuple[int, int]:
    return 7 - rank_index, file_index


def _rotate_180(file_index: int, rank_index: int) -> tuple[int, int]:
    return 7 - file_index, 7 - rank_index


def _rotate_270(file_index: int, rank_index: int) -> tuple[int, int]:
    return rank_index, 7 - file_index


def _reflect_horizontal(file_index: int, rank_index: int) -> tuple[int, int]:
    return 7 - file_index, rank_index


def _reflect_vertical(file_index: int, rank_index: int) -> tuple[int, int]:
    return file_index, 7 - rank_index


def _reflect_diagonal(file_index: int, rank_index: int) -> tuple[int, int]:
    return rank_index, file_index


def _reflect_anti_diagonal(file_index: int, rank_index: int) -> tuple[int, int]:
    return 7 - rank_index, 7 - file_index


_TRANSFORMS = (
    ("identity", _identity),
    ("rotate90", _rotate_90),
    ("rotate180", _rotate_180),
    ("rotate270", _rotate_270),
    ("reflect-horizontal", _reflect_horizontal),
    ("reflect-vertical", _reflect_vertical),
    ("reflect-diagonal", _reflect_diagonal),
    ("reflect-anti-diagonal", _reflect_anti_diagonal),
)


def _transform_board(
    board: chess.Board,
    transform: Callable[[int, int], tuple[int, int]],
) -> chess.Board:
    transformed = chess.Board(None)
    transformed.turn = board.turn
    for square, piece in board.piece_map().items():
        file_index, rank_index = transform(
            chess.square_file(square), chess.square_rank(square)
        )
        transformed.set_piece_at(chess.square(file_index, rank_index), piece)
    return transformed


def _candidate_sort_key(request: GenerationRequest, descriptor: str) -> tuple[str, str]:
    material = "|".join(
        (
            GENERATOR_VERSION,
            f"seed={request.seed}",
            f"mate={request.mate_moves}",
            f"side={request.side_to_move}",
            f"difficulty={request.difficulty}",
            descriptor,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest(), descriptor


def candidate_sequence(request: GenerationRequest) -> tuple[Candidate, ...]:
    """Return the complete stable candidate stream before the attempt bound."""

    variants: list[tuple[str, str]] = []
    for template_name, fen in _BASE_TEMPLATES[request.mate_moves]:
        base = chess.Board(fen)
        for transform_name, transform in _TRANSFORMS:
            board = _transform_board(base, transform)
            color_variant = "white"
            if request.side_to_move == "black":
                board = board.mirror()
                color_variant = "black-mirror"
            descriptor = f"{template_name}/{transform_name}/{color_variant}"
            variants.append((descriptor, board.fen(en_passant="legal")))

    variants.sort(key=lambda item: _candidate_sort_key(request, item[0]))
    return tuple(
        Candidate(ordinal=index, strategy=strategy, fen=fen)
        for index, (strategy, fen) in enumerate(variants, start=1)
    )


def _screen_candidate(
    candidate: Candidate, request: GenerationRequest
) -> tuple[chess.Board | None, str | None]:
    try:
        board = parse_board(candidate.fen)
    except PositionError as exc:
        return None, str(exc)
    expected_turn = chess.WHITE if request.side_to_move == "white" else chess.BLACK
    if board.turn != expected_turn:
        return None, "candidate side to move does not match request"
    return board, None


def _accounting(
    attempted: int,
    structural: int,
    duplicates: int,
    verifier_rejected: int,
    difficulty_rejected: int,
    accepted: int,
) -> GenerationAccounting:
    return GenerationAccounting(
        candidates_attempted=attempted,
        structurally_rejected=structural,
        duplicate_rejected=duplicates,
        verifier_rejected=verifier_rejected,
        difficulty_rejected=difficulty_rejected,
        accepted=accepted,
        verifier_submitted=verifier_rejected + difficulty_rejected + accepted,
    )


def generate_batch(request: GenerationRequest, count: int) -> BatchGenerationResult:
    """Generate up to ``count`` unique puzzles within the request budget."""

    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= _MAX_BATCH_SIZE
    ):
        raise GenerationRequestError("count must be an integer between 1 and 100")

    attempted = structural = duplicates = verifier_rejected = difficulty_rejected = 0
    puzzles: list[GeneratedPuzzle] = []
    rejections: list[CandidateRejection] = []
    seen_identities: set[str] = set()
    stream = candidate_sequence(request)

    for candidate in stream[: request.max_candidate_attempts]:
        attempted += 1
        board, screen_reason = _screen_candidate(candidate, request)
        if board is None:
            structural += 1
            rejections.append(
                CandidateRejection(
                    candidate.ordinal,
                    candidate.strategy,
                    "structural_screen",
                    screen_reason or "structural screen rejected candidate",
                )
            )
            continue

        identity = puzzle_hash(board, request.mate_moves)
        if identity in seen_identities:
            duplicates += 1
            rejections.append(
                CandidateRejection(
                    candidate.ordinal,
                    candidate.strategy,
                    "duplicate",
                    f"duplicate puzzle identity: {identity}",
                )
            )
            continue
        seen_identities.add(identity)

        verification = MateVerifier(
            max_nodes=request.verifier_max_nodes,
            max_seconds=float(request.verifier_max_seconds),
        ).verify(
            candidate.fen,
            request.mate_moves,
            require_unique_key=request.require_unique_key,
            dual_policy=request.dual_policy,
        )
        if verification.accepted is not True:
            verifier_rejected += 1
            rejections.append(
                CandidateRejection(
                    candidate.ordinal,
                    candidate.strategy,
                    "verifier",
                    verification.reason,
                )
            )
            continue

        difficulty = assess_difficulty(verification)
        if difficulty.label != request.difficulty:
            difficulty_rejected += 1
            rejections.append(
                CandidateRejection(
                    candidate.ordinal,
                    candidate.strategy,
                    "difficulty",
                    f"measured_{difficulty.label}_requested_{request.difficulty}",
                )
            )
            continue

        if (
            verification.normalized_fen is None
            or verification.puzzle_hash is None
            or verification.certificate_sha256 is None
        ):
            raise RuntimeError("accepted verifier result is missing required metadata")
        if verification.puzzle_hash != identity:
            raise RuntimeError("verifier puzzle identity disagrees with canonical candidate")
        if verification.normalized_fen != normalized_fen(board):
            raise RuntimeError("verifier normalized FEN disagrees with canonical candidate")

        puzzles.append(
            GeneratedPuzzle(
                request=request,
                candidate=candidate,
                attempt_number=attempted,
                normalized_fen=verification.normalized_fen,
                puzzle_identity=verification.puzzle_hash,
                verification=verification,
                difficulty=difficulty,
            )
        )
        if len(puzzles) == count:
            return BatchGenerationResult(
                success=True,
                requested_count=count,
                puzzles=tuple(puzzles),
                accounting=_accounting(
                    attempted,
                    structural,
                    duplicates,
                    verifier_rejected,
                    difficulty_rejected,
                    len(puzzles),
                ),
                failure_reason=None,
                rejections=tuple(rejections),
            )

    reason = (
        "candidate_attempt_budget_exhausted"
        if attempted == request.max_candidate_attempts
        else "candidate_stream_exhausted"
    )
    return BatchGenerationResult(
        success=False,
        requested_count=count,
        puzzles=tuple(puzzles),
        accounting=_accounting(
            attempted,
            structural,
            duplicates,
            verifier_rejected,
            difficulty_rejected,
            len(puzzles),
        ),
        failure_reason=reason,
        rejections=tuple(rejections),
    )


def generate_puzzle(request: GenerationRequest) -> GenerationResult:
    """Generate one verified puzzle or return a deterministic bounded failure."""

    batch = generate_batch(request, 1)
    return GenerationResult(
        success=batch.success,
        puzzle=batch.puzzles[0] if batch.puzzles else None,
        accounting=batch.accounting,
        failure_reason=batch.failure_reason,
        rejections=batch.rejections,
    )


def render_generated_puzzle(
    puzzle: GeneratedPuzzle,
    artifact_root: str | Path,
    artifact_stem: str,
    *,
    puzzle_id: str | None = None,
) -> RenderedPuzzleArtifacts:
    """Render public unsolved artifacts only through the Phase 2 boundary."""

    if not isinstance(puzzle, GeneratedPuzzle):
        raise GenerationRequestError("puzzle must be a GeneratedPuzzle")
    request = puzzle.to_render_request(puzzle_id)
    return RenderedPuzzleArtifacts(
        svg=write_svg_artifact(request, artifact_root, f"{artifact_stem}.svg"),
        png=write_png_artifact(request, artifact_root, f"{artifact_stem}.png"),
    )
