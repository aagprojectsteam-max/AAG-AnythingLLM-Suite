"""Deterministic board-density profiles for candidate construction.

Density is presentation-safe metadata and a generation preference.  It never
participates in mate truth, difficulty scoring, or verifier acceptance.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Literal

from . import DENSITY_VERSION


DensityProfile = Literal["sparse", "normal", "rich"]
DensityPreference = Literal["auto", "sparse", "normal", "rich"]

PROFILE_RANGES: dict[DensityProfile, tuple[int, int]] = {
    # The legacy builtin KQK fallback has three-piece positions. Stockfish
    # sparse construction deliberately emits 5-9 pieces.
    "sparse": (3, 9),
    "normal": (10, 16),
    "rich": (17, 26),
}
PROFILE_WEIGHTS: dict[DensityProfile, int] = {
    "sparse": 15,
    "normal": 50,
    "rich": 35,
}
_PROFILES: tuple[DensityProfile, ...] = ("sparse", "normal", "rich")


@dataclass(frozen=True)
class DensityAssessment:
    profile: DensityProfile
    piece_count: int
    classifier_version: str = DENSITY_VERSION

    def public_dict(self) -> dict[str, str | int]:
        return {
            "profile": self.profile,
            "piece_count": self.piece_count,
            "classifier_version": self.classifier_version,
        }


def classify_piece_count(piece_count: int) -> DensityAssessment:
    if isinstance(piece_count, bool) or not isinstance(piece_count, int):
        raise ValueError("piece_count must be an integer")
    for profile in _PROFILES:
        minimum, maximum = PROFILE_RANGES[profile]
        if minimum <= piece_count <= maximum:
            return DensityAssessment(profile, piece_count)
    raise ValueError("supported puzzle density requires between 3 and 26 pieces")


def validate_density_preference(value: object) -> DensityPreference:
    if value not in {"auto", *_PROFILES}:
        raise ValueError("density must be auto, sparse, normal, or rich")
    return value  # type: ignore[return-value]


def _seed_material(seed: int, context: str) -> int:
    payload = f"{DENSITY_VERSION}|seed={seed}|{context}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def select_auto_density(seed: int, *, context: str = "single") -> DensityProfile:
    """Select 15/50/35 sparse/normal/rich using only stable request data."""

    roll = _seed_material(seed, context) % 100
    if roll < PROFILE_WEIGHTS["sparse"]:
        return "sparse"
    if roll < PROFILE_WEIGHTS["sparse"] + PROFILE_WEIGHTS["normal"]:
        return "normal"
    return "rich"


def density_plan(
    seed: int,
    count: int,
    preference: DensityPreference,
    *,
    context: str,
) -> tuple[DensityProfile, ...]:
    """Return a deterministic accepted-puzzle profile plan for one batch.

    Automatic batches of two or more deliberately favor normal/rich.  Larger
    batches use largest-remainder apportionment of the documented weights and
    a seeded stable shuffle.  Sparse runs can therefore never exceed two.
    """

    validate_density_preference(preference)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    if preference != "auto":
        return tuple(preference for _ in range(count))
    if count == 1:
        return (select_auto_density(seed, context=context),)
    if count == 2:
        plan: list[DensityProfile] = ["normal", "rich"]
    elif count == 3:
        plan = ["normal", "normal", "rich"]
    else:
        quotas = {
            profile: count * PROFILE_WEIGHTS[profile] / 100 for profile in _PROFILES
        }
        allocations = {profile: int(quotas[profile]) for profile in _PROFILES}
        remaining = count - sum(allocations.values())
        tie_seed = _seed_material(seed, f"{context}|quota")
        tie_order = sorted(
            _PROFILES,
            key=lambda profile: (
                -(quotas[profile] - allocations[profile]),
                hashlib.sha256(f"{tie_seed}|{profile}".encode()).hexdigest(),
            ),
        )
        for profile in tie_order[:remaining]:
            allocations[profile] += 1
        plan = [
            profile
            for profile in _PROFILES
            for _ in range(allocations[profile])
        ]
    random.Random(_seed_material(seed, f"{context}|shuffle")).shuffle(plan)
    # A bounded stable repair also handles a sparse run at the end of a shuffle.
    def sparse_triples() -> int:
        return sum(
            plan[index : index + 3] == ["sparse", "sparse", "sparse"]
            for index in range(max(0, len(plan) - 2))
        )

    while (before := sparse_triples()) > 0:
        target = next(
            index + 2
            for index in range(len(plan) - 2)
            if plan[index : index + 3] == ["sparse", "sparse", "sparse"]
        )
        repaired = False
        for candidate, profile in enumerate(plan):
            if profile == "sparse" or candidate in range(target - 2, target + 1):
                continue
            plan[target], plan[candidate] = plan[candidate], plan[target]
            if sparse_triples() < before:
                repaired = True
                break
            plan[target], plan[candidate] = plan[candidate], plan[target]
        if not repaired:  # unreachable under the documented <=15% sparse quota
            raise RuntimeError("could not construct a sparse-streak-safe density plan")
    return tuple(plan)
