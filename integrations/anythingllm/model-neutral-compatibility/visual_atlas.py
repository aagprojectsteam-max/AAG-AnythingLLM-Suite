#!/usr/bin/env python3
"""Canonical Visual Atlas catalog and deterministic selective retrieval.

The manifest remains the authority for entries and assets. This module only
adds a small, reviewed alias layer and never performs inference or generation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ATLAS_MODULE = "visual-atlas"
PLAN_SCHEMA = "aag.selective-knowledge.plan.v1"
CATALOG_SCHEMA = "aag.visual-style-atlas.catalog.v1"
SUPPORTED_MODES = {"auto", "manual_taxonomy", "manual_browse"}
SAFE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AtlasError(ValueError):
    pass


def _project_root() -> Path:
    override = os.environ.get("AAG_IMAGE_PROJECT_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[3]


def _atlas_root() -> Path:
    override = os.environ.get("AAG_VISUAL_ATLAS_ROOT")
    if override:
        return Path(override).resolve()
    return _project_root() / "visual-atlas"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AtlasError(f"Visual Atlas data is unavailable or invalid: {path.name}") from error
    if not isinstance(value, dict):
        raise AtlasError(f"Visual Atlas data is not an object: {path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("’", "'").replace("־", "-")
    text = re.sub(r"[^\w\u0590-\u05ff]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


@dataclass(frozen=True)
class AtlasPaths:
    taxonomy: Path
    manifest: Path
    aliases: Path


class VisualAtlas:
    """Validated, immutable view over the completed Atlas."""

    def __init__(self, paths: AtlasPaths | None = None, *, verify_assets: bool = False):
        atlas_root = _atlas_root()
        self.paths = paths or AtlasPaths(
            taxonomy=Path(__file__).resolve().parent / "composer" / "visual-taxonomy.json",
            manifest=atlas_root / "manifest" / "atlas-manifest.json",
            aliases=atlas_root / "manifest" / "retrieval-aliases.json",
        )
        self.taxonomy = _json(self.paths.taxonomy)
        self.manifest = _json(self.paths.manifest)
        self.alias_config = _json(self.paths.aliases)
        self.atlas_root = self.paths.manifest.parent.parent.resolve()
        product = _json(self.atlas_root / "manifest" / "product-assets.json")
        self.product_assets = {item.get("key"): item for item in product.get("entries", [])}
        self._validate(verify_assets=verify_assets)

    def _validate(self, *, verify_assets: bool) -> None:
        taxonomy_sha = _sha256(self.paths.taxonomy)
        if self.manifest.get("taxonomy_sha256") != taxonomy_sha:
            raise AtlasError("Visual Atlas taxonomy hash does not match the canonical manifest.")
        if self.alias_config.get("atlas_version") != self.manifest.get("atlas_version"):
            raise AtlasError("Visual Atlas alias metadata targets a different Atlas version.")

        pairs: list[tuple[str, str]] = []
        self.family_labels: dict[str, str] = {}
        self.subfamily_labels: dict[tuple[str, str], str] = {}
        for family in self.taxonomy.get("families", []):
            family_id = family.get("id")
            if not isinstance(family_id, str) or not SAFE_ID_RE.fullmatch(family_id):
                raise AtlasError("Visual Atlas taxonomy contains an invalid family ID.")
            self.family_labels[family_id] = str(family.get("label") or family_id)
            for subfamily in family.get("subfamilies", []):
                subfamily_id = subfamily.get("id")
                if not isinstance(subfamily_id, str) or not SAFE_ID_RE.fullmatch(subfamily_id):
                    raise AtlasError("Visual Atlas taxonomy contains an invalid subfamily ID.")
                pair = (family_id, subfamily_id)
                if pair in self.subfamily_labels:
                    raise AtlasError("Visual Atlas taxonomy contains a duplicate style.")
                pairs.append(pair)
                self.subfamily_labels[pair] = str(subfamily.get("label") or subfamily_id)

        entries = self.manifest.get("entries")
        if not isinstance(entries, list) or len(entries) != 493 or len(pairs) != 493:
            raise AtlasError("Visual Atlas must contain exactly 493 canonical entries.")
        self.entries: dict[tuple[str, str], dict[str, Any]] = {}
        self.thumbnail_sha256: dict[tuple[str, str], str] = {}
        for entry in entries:
            pair = (entry.get("family_id"), entry.get("subfamily_id"))
            if pair not in self.subfamily_labels or pair in self.entries:
                raise AtlasError("Visual Atlas manifest does not map one-to-one to the taxonomy.")
            if entry.get("status") != "COMPLETED":
                raise AtlasError("Visual Atlas contains an entry that is not completed.")
            output = self._asset_path(entry.get("output_path"), ".png")
            thumbnail = self._asset_path(entry.get("thumbnail_path"), ".webp")
            output_present = output.is_file()
            thumbnail_present = thumbnail.is_file()
            if output_present != thumbnail_present:
                raise AtlasError("Visual Atlas contains a partial preview/thumbnail pair.")
            if verify_assets and not output_present:
                raise AtlasError("Visual Atlas pixel verification was requested but assets are missing.")
            if output_present and (output.stat().st_size < 128 or thumbnail.stat().st_size < 128):
                raise AtlasError("Visual Atlas contains an empty preview or thumbnail.")
            if output_present and thumbnail.read_bytes()[:4] != b"RIFF":
                raise AtlasError("Visual Atlas contains an invalid thumbnail header.")
            if verify_assets and _sha256(output) != entry.get("sha256"):
                raise AtlasError("Visual Atlas preview hash validation failed.")
            self.entries[pair] = {**entry, "assets_available": output_present}
            # Thumbnail derivatives have their own immutable browser cache
            # identity. Tying their URL to the reference PNG hash leaves a
            # repaired derivative trapped behind its previous cached bytes.
            product_entry = self.product_assets.get(f"{pair[0]}/{pair[1]}", {})
            recorded_thumbnail = product_entry.get("thumbnail", {}).get("sha256")
            if not thumbnail_present and not isinstance(recorded_thumbnail, str):
                raise AtlasError("Visual Atlas metadata lacks the thumbnail integrity record.")
            self.thumbnail_sha256[pair] = _sha256(thumbnail) if thumbnail_present else recorded_thumbnail
        if set(pairs) != set(self.entries):
            raise AtlasError("Visual Atlas manifest and taxonomy style sets differ.")

        raw_aliases = self.alias_config.get("entries", {})
        if not isinstance(raw_aliases, dict):
            raise AtlasError("Visual Atlas aliases are invalid.")
        self.aliases: dict[tuple[str, str], list[str]] = {}
        for key, values in raw_aliases.items():
            bits = str(key).split("/", 1)
            pair = tuple(bits) if len(bits) == 2 else ("", "")
            if pair not in self.entries or not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise AtlasError(f"Visual Atlas alias target is invalid: {key}")
            normalized = sorted({normalize_text(value) for value in values if normalize_text(value)}, key=lambda item: (-len(item.split()), item))
            self.aliases[pair] = normalized
        self.style_cues = tuple(normalize_text(value) for value in self.alias_config.get("style_cues", []) if normalize_text(value))
        self.top_k = min(2, max(1, int(self.alias_config.get("top_k", 2))))
        self.minimum_confidence = float(self.alias_config.get("minimum_confidence", 0.72))
        self.taxonomy_sha256 = taxonomy_sha
        self.manifest_sha256 = _sha256(self.paths.manifest)

    def _asset_path(self, relative: Any, suffix: str) -> Path:
        if not isinstance(relative, str) or not relative.endswith(suffix) or relative.startswith(("/", "~")):
            raise AtlasError("Visual Atlas contains an unsafe asset path.")
        resolved = (self.atlas_root / relative).resolve()
        if self.atlas_root not in resolved.parents:
            raise AtlasError("Visual Atlas asset path escapes its root.")
        return resolved

    def entry(self, family_id: str, subfamily_id: str) -> dict[str, Any]:
        try:
            return self.entries[(family_id, subfamily_id)]
        except KeyError as error:
            raise AtlasError("Unknown Visual Atlas family/subfamily selection.") from error

    def asset(self, family_id: str, subfamily_id: str, kind: str) -> tuple[Path, str]:
        entry = self.entry(family_id, subfamily_id)
        if not entry.get("assets_available"):
            raise AtlasError("Visual Atlas pixels are not installed; metadata-only mode is active.")
        if kind == "thumbnail":
            return self._asset_path(entry["thumbnail_path"], ".webp"), "image/webp"
        if kind == "preview":
            return self._asset_path(entry["output_path"], ".png"), "image/png"
        raise AtlasError("Unknown Visual Atlas asset kind.")

    def _public_entry(self, pair: tuple[str, str]) -> dict[str, Any]:
        entry = self.entries[pair]
        family_id, subfamily_id = pair
        return {
            # Retain the canonical taxonomy shape consumed by every existing
            # Composer selector while adding manifest-backed Atlas metadata.
            "id": subfamily_id,
            "label": self.subfamily_labels[pair],
            "index": entry["index"],
            "family_id": family_id,
            "family_label": self.family_labels[family_id],
            "subfamily_id": subfamily_id,
            "subfamily_label": self.subfamily_labels[pair],
            "description": entry["style_descriptor"],
            "aliases": self.aliases.get(pair, []),
            "atlas": {
                "available": bool(entry.get("assets_available")),
                "sha256": entry["sha256"],
                "thumbnail_sha256": self.thumbnail_sha256[pair],
                "width": entry["width"],
                "height": entry["height"],
            },
        }

    def catalog(self) -> dict[str, Any]:
        families = []
        for family in self.taxonomy["families"]:
            family_id = family["id"]
            families.append({
                "id": family_id,
                "label": self.family_labels[family_id],
                "classification": family.get("classification"),
                "subfamily_classification": family.get("subfamily_classification"),
                "subfamilies": [self._public_entry((family_id, subfamily["id"])) for subfamily in family["subfamilies"]],
            })
        return {
            "schema": CATALOG_SCHEMA,
            "atlas_version": self.manifest["atlas_version"],
            "taxonomy_sha256": self.taxonomy_sha256,
            "manifest_sha256": self.manifest_sha256,
            "total_entries": len(self.entries),
            "families": families,
        }

    def _selection(self, pair: tuple[str, str], *, confidence: float, matched: list[str]) -> dict[str, Any]:
        entry = self.entries[pair]
        return {
            "family_id": pair[0],
            "family_label": self.family_labels[pair[0]],
            "subfamily_id": pair[1],
            "subfamily_label": self.subfamily_labels[pair],
            "atlas_index": entry["index"],
            "style_descriptor": entry["style_descriptor"],
            "confidence": round(confidence, 3),
            "matched": matched[:4],
        }

    def _plan(self, *, used: bool, mode: str, reason: str, selections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        selected = selections or []
        return {
            "schema": PLAN_SCHEMA,
            "module": ATLAS_MODULE,
            "used": used,
            "mode": mode,
            "reason": reason,
            "confidence": max((item["confidence"] for item in selected), default=0.0),
            "selections": selected[: self.top_k],
            "visual_reference_used": False,
            "context_chars": 0,
            "estimated_context_tokens": 0,
            "taxonomy_sha256": self.taxonomy_sha256,
            "manifest_sha256": self.manifest_sha256,
        }

    def select(
        self,
        text: Any,
        *,
        mode: str = "auto",
        family_id: str | None = None,
        subfamily_id: str | None = None,
        operation: str = "generate",
        preservation: str = "none",
    ) -> dict[str, Any]:
        if mode not in SUPPORTED_MODES:
            raise AtlasError("Unknown Visual Atlas selection mode.")
        if operation == "upscale":
            return self._plan(used=False, mode=mode, reason="operation_excluded")
        if preservation == "identity":
            return self._plan(used=False, mode=mode, reason="identity_reference_protected")

        if mode != "auto" or (family_id and subfamily_id):
            if not family_id or not subfamily_id:
                raise AtlasError("Manual Visual Atlas selection requires a family and subfamily.")
            pair = (family_id, subfamily_id)
            self.entry(*pair)
            return self._plan(
                used=True,
                mode=mode if mode != "auto" else "manual_taxonomy",
                reason="explicit_user_selection",
                selections=[self._selection(pair, confidence=1.0, matched=["manual"])],
            )

        normalized = normalize_text(text)
        if not normalized:
            return self._plan(used=False, mode="auto", reason="empty_request")
        has_style_cue = any(_contains(normalized, cue) for cue in self.style_cues)
        if not has_style_cue:
            return self._plan(used=False, mode="auto", reason="no_style_intent")

        scores: dict[tuple[str, str], tuple[float, list[str]]] = {}
        for pair, aliases in self.aliases.items():
            matched = [alias for alias in aliases if _contains(normalized, alias)]
            if matched:
                longest = max(len(alias.split()) for alias in matched)
                scores[pair] = (min(0.99, 0.88 + 0.02 * longest), matched)

        # Canonical labels are useful fallback aliases, but ties remain unresolved.
        for pair in self.entries:
            family_phrase = normalize_text(self.family_labels[pair[0]])
            subfamily_phrase = normalize_text(self.subfamily_labels[pair])
            if _contains(normalized, subfamily_phrase) and pair not in scores:
                words = len(subfamily_phrase.split())
                confidence = 0.75 if words >= 2 or _contains(normalized, family_phrase) else 0.72
                scores[pair] = (confidence, [subfamily_phrase])

        ranked = sorted(scores.items(), key=lambda item: (-item[1][0], self.entries[item[0]]["index"]))
        if not ranked or ranked[0][1][0] < self.minimum_confidence:
            return self._plan(used=False, mode="auto", reason="no_reliable_match")
        if len(ranked) > 1 and ranked[0][1][0] == ranked[1][1][0] and ranked[0][1][0] < 0.88:
            return self._plan(used=False, mode="auto", reason="ambiguous_match")

        top_score = ranked[0][1][0]
        chosen = []
        for pair, (score, matched) in ranked:
            if len(chosen) >= self.top_k or score < max(self.minimum_confidence, top_score - 0.08):
                break
            chosen.append(self._selection(pair, confidence=score, matched=matched))
        return self._plan(used=True, mode="auto", reason="deterministic_alias_match", selections=chosen)


_INSTANCE: VisualAtlas | None = None


def get_visual_atlas() -> VisualAtlas:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = VisualAtlas()
    return _INSTANCE
