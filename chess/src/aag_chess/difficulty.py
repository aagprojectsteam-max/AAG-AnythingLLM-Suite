"""Deterministic, explainable V1 puzzle difficulty scoring.

The scorer consumes an already accepted verifier result.  It classifies
mechanical search characteristics; it does not judge beauty, originality, or
human solving experience, and it has no authority over chess correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import chess

from . import SCORER_VERSION
from .position import parse_board
from .verifier import VerificationResult


DifficultyLabel = Literal["easy", "medium", "hard"]


class DifficultyError(ValueError):
    """The verifier result cannot be scored under the V1 contract."""


@dataclass(frozen=True)
class DifficultyAssessment:
    label: DifficultyLabel
    score: int
    mate_moves: int
    root_legal_moves: int
    defender_nodes: int
    defender_replies: int
    maximum_defender_replies: int
    attacker_nodes: int
    dual_count: int
    checking_key: bool
    scorer_version: str = SCORER_VERSION

    def public_dict(self) -> dict[str, int | str]:
        """Return the deliberately small, solution-free public projection."""

        return {
            "label": self.label,
            "score": self.score,
            "scorer_version": self.scorer_version,
        }


@dataclass
class _ProofCounts:
    defender_nodes: int = 0
    defender_replies: int = 0
    maximum_defender_replies: int = 0
    attacker_nodes: int = 0


def _count_proof(node: dict[str, Any], counts: _ProofCounts) -> None:
    if node.get("terminal") == "checkmate":
        return
    role = node.get("role")
    moves = node.get("moves")
    if role not in {"attacker", "defender"} or not isinstance(moves, list):
        raise DifficultyError("verification proof has an invalid node")
    if role == "defender":
        counts.defender_nodes += 1
        counts.defender_replies += len(moves)
        counts.maximum_defender_replies = max(
            counts.maximum_defender_replies, len(moves)
        )
    else:
        counts.attacker_nodes += 1
    for branch in moves:
        if not isinstance(branch, dict) or not isinstance(branch.get("child"), dict):
            raise DifficultyError("verification proof has an invalid branch")
        _count_proof(branch["child"], counts)


def _label(score: int) -> DifficultyLabel:
    if score <= 34:
        return "easy"
    if score <= 69:
        return "medium"
    return "hard"


def assess_difficulty(verification: VerificationResult) -> DifficultyAssessment:
    """Score one accepted exact-mate proof using stable mechanical features.

    Mate depth is the dominant feature. Root choice count, defensive proof
    branching, post-key dual count, and whether the key checks make bounded
    adjustments. Scores are clamped to 0..100. A checking key receives a
    small reduction because it narrows the solver's candidate set.
    """

    if not isinstance(verification, VerificationResult):
        raise DifficultyError("verification must be a VerificationResult")
    if verification.accepted is not True or verification.proof is None:
        raise DifficultyError("difficulty requires an accepted proof")
    mate_moves = verification.requested_mate_moves
    if mate_moves not in (1, 2, 3):
        raise DifficultyError("difficulty supports mate in 1, 2, or 3")
    if verification.exact_mate_plies != 2 * mate_moves - 1:
        raise DifficultyError("verification does not prove the requested exact mate")
    if verification.normalized_fen is None or len(verification.key_moves) != 1:
        raise DifficultyError("difficulty requires a canonical position and unique key")

    board = parse_board(verification.normalized_fen)
    root_legal_moves = board.legal_moves.count()
    key = chess.Move.from_uci(verification.key_moves[0])
    if key not in board.legal_moves:
        raise DifficultyError("verification key is not legal in the root position")
    board.push(key)
    checking_key = board.is_check()

    counts = _ProofCounts()
    _count_proof(verification.proof, counts)
    raw_score = (
        10
        + 30 * (mate_moves - 1)
        + min(root_legal_moves, 30) // 3
        + 2 * min(counts.defender_replies, 12)
        + min(counts.defender_nodes, 8)
        + min(len(verification.duals), 3)
        - (5 if checking_key else 0)
    )
    score = max(0, min(100, raw_score))
    return DifficultyAssessment(
        label=_label(score),
        score=score,
        mate_moves=mate_moves,
        root_legal_moves=root_legal_moves,
        defender_nodes=counts.defender_nodes,
        defender_replies=counts.defender_replies,
        maximum_defender_replies=counts.maximum_defender_replies,
        attacker_nodes=counts.attacker_nodes,
        dual_count=len(verification.duals),
        checking_key=checking_key,
    )
