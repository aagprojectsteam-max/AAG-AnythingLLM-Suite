from dataclasses import replace

import pytest

from aag_chess import SCORER_VERSION
from aag_chess.difficulty import DifficultyError, assess_difficulty
from aag_chess.verifier import MateVerifier


POSITIONS = (
    ("3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1", 1, "forbid", "easy"),
    ("2k5/8/8/K7/8/8/8/3Q4 w - - 0 1", 2, "forbid", "medium"),
    ("8/8/8/7K/8/8/1Q6/4k3 w - - 0 1", 3, "warning", "hard"),
)


@pytest.mark.parametrize(("fen", "mate", "policy", "label"), POSITIONS)
def test_measured_difficulty_classes(fen, mate, policy, label):
    verification = MateVerifier().verify(fen, mate, dual_policy=policy)
    assessment = assess_difficulty(verification)
    assert assessment.label == label
    assert 0 <= assessment.score <= 100
    assert assessment.mate_moves == mate
    assert assessment.root_legal_moves > 0
    assert assessment.scorer_version == SCORER_VERSION == "aag-difficulty-v1"
    assert assessment.public_dict() == {
        "label": label,
        "score": assessment.score,
        "scorer_version": SCORER_VERSION,
    }


def test_difficulty_is_deterministic_and_depth_dominant():
    assessments = []
    for fen, mate, policy, unused_label in POSITIONS:
        first = MateVerifier().verify(fen, mate, dual_policy=policy)
        second = MateVerifier().verify(fen, mate, dual_policy=policy)
        assessments.append(assess_difficulty(first))
        assert assess_difficulty(first) == assess_difficulty(second)
    assert [item.label for item in assessments] == ["easy", "medium", "hard"]
    assert [item.score for item in assessments] == sorted(
        item.score for item in assessments
    )


def test_difficulty_rejects_unaccepted_or_inconsistent_result():
    rejected = MateVerifier().verify("7k/8/8/8/8/8/8/K7 w - - 0 1", 1)
    with pytest.raises(DifficultyError, match="accepted"):
        assess_difficulty(rejected)
    accepted = MateVerifier().verify(POSITIONS[0][0], 1)
    with pytest.raises(DifficultyError, match="exact mate"):
        assess_difficulty(replace(accepted, exact_mate_plies=None))
