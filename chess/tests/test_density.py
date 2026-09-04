from collections import Counter

import pytest

from aag_chess import DENSITY_VERSION
from aag_chess.density import (
    PROFILE_RANGES,
    classify_piece_count,
    density_plan,
    select_auto_density,
    validate_density_preference,
)


@pytest.mark.parametrize(
    ("piece_count", "profile"),
    [(3, "sparse"), (9, "sparse"), (10, "normal"), (16, "normal"), (17, "rich"), (26, "rich")],
)
def test_density_piece_count_boundaries(piece_count, profile):
    assessment = classify_piece_count(piece_count)
    assert assessment.profile == profile
    assert assessment.piece_count == piece_count
    assert assessment.classifier_version == DENSITY_VERSION


@pytest.mark.parametrize("piece_count", [2, 27, True, 4.5])
def test_unsupported_piece_counts_fail_closed(piece_count):
    with pytest.raises(ValueError):
        classify_piece_count(piece_count)


def test_documented_ranges_are_contiguous_and_density_is_validated():
    assert PROFILE_RANGES == {
        "sparse": (3, 9),
        "normal": (10, 16),
        "rich": (17, 26),
    }
    assert validate_density_preference("auto") == "auto"
    assert validate_density_preference("rich") == "rich"
    with pytest.raises(ValueError):
        validate_density_preference("hard")


def test_auto_selection_is_deterministic_and_varies_across_seeds():
    first = [select_auto_density(seed, context="test") for seed in range(100)]
    second = [select_auto_density(seed, context="test") for seed in range(100)]
    assert first == second
    assert set(first) == {"sparse", "normal", "rich"}


def test_auto_distribution_favors_normal_and_rich():
    counts = Counter(select_auto_density(seed, context="distribution") for seed in range(1_000))
    assert counts["sparse"] < counts["normal"]
    assert counts["sparse"] < counts["rich"]
    assert counts["normal"] + counts["rich"] >= 800


def test_batch_plan_balances_profiles_without_sparse_streaks():
    first = density_plan(12345, 10, "auto", context="batch")
    second = density_plan(12345, 10, "auto", context="batch")
    assert first == second
    counts = Counter(first)
    assert counts["normal"] + counts["rich"] >= 8
    assert counts["normal"] and counts["rich"] and counts["sparse"]
    assert all(first[index : index + 3] != ("sparse",) * 3 for index in range(8))
    assert density_plan(7, 4, "rich", context="batch") == ("rich",) * 4
