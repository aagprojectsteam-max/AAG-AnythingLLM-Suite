#!/usr/bin/env python3
"""Frozen Phase 7R3 prompt and measurable composition contract."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path("/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System")
QUALITY_PATH = ROOT / "image-agent/phase-7r3/acceptance/CONTRACT-B-FREEZE.json"
MATRIX_PATH = ROOT / "image-agent/phase-7r3/acceptance/CONFIRMATION-MATRIX.json"


@lru_cache(maxsize=1)
def quality_contract() -> dict:
    return json.loads(QUALITY_PATH.read_text())


@lru_cache(maxsize=1)
def matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text())


def classify_prompt(prompt: str) -> tuple[str, str]:
    """Return exact frozen (family, identity), rejecting non-contract prompts."""
    matches = []
    for family, prompts in matrix()["prompt_contracts"].items():
        for identity, expected in prompts.items():
            if prompt == expected:
                matches.append((family, identity))
    if len(matches) != 1:
        raise ValueError("prompt is not an exact frozen Phase 7R3 composition contract")
    return matches[0]


def geometry_metrics(bbox: list[float], dimensions: tuple[int, int]) -> dict:
    width, height = dimensions
    x1, y1, x2, y2 = [float(value) for value in bbox]
    face_width, face_height = x2 - x1, y2 - y1
    center_x, center_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    return {
        "face_occupancy_percent": 100.0 * face_width * face_height / (width * height),
        "face_width_percent": 100.0 * face_width / width,
        "face_height_percent": 100.0 * face_height / height,
        "face_bbox_aspect_ratio": face_width / max(face_height, 1e-12),
        "face_center_offset_x_percent": 100.0 * (center_x - width / 2.0) / width,
        "face_center_offset_y_percent": 100.0 * (center_y - height / 2.0) / height,
        "left_clearance_percent": 100.0 * x1 / width,
        "right_clearance_percent": 100.0 * (width - x2) / width,
        "top_clearance_percent": 100.0 * y1 / height,
        "bottom_clearance_percent": 100.0 * (height - y2) / height,
    }


def composition_failures(family: str, metrics: dict) -> list[str]:
    limits = quality_contract()["prompt_families"][family]
    failures = []
    occupancy = limits["face_occupancy_percent"]
    if not occupancy["minimum"] <= metrics["face_occupancy_percent"] <= occupancy["maximum"]:
        failures.append("FACE_OCCUPANCY_OUT_OF_RANGE")
    rules = {
        "face_width_percent_maximum": ("face_width_percent", lambda value, limit: value <= limit),
        "left_clearance_percent_minimum": ("left_clearance_percent", lambda value, limit: value >= limit),
        "right_clearance_percent_minimum": ("right_clearance_percent", lambda value, limit: value >= limit),
        "top_clearance_percent_minimum": ("top_clearance_percent", lambda value, limit: value >= limit),
        "bottom_clearance_percent_minimum": ("bottom_clearance_percent", lambda value, limit: value >= limit),
        "absolute_face_center_offset_x_percent_maximum": (
            "face_center_offset_x_percent", lambda value, limit: abs(value) <= limit
        ),
        "absolute_face_center_offset_y_percent_maximum": (
            "face_center_offset_y_percent", lambda value, limit: abs(value) <= limit
        ),
    }
    for rule, (metric, check) in rules.items():
        if rule in limits and not check(metrics[metric], limits[rule]):
            failures.append(rule.upper())
    return failures


def historical_family(family: str) -> str:
    if family == "baseline-close":
        return "structured-close-a"
    if family == "composition":
        return "composition-control"
    raise ValueError(f"unsupported historical family: {family}")

