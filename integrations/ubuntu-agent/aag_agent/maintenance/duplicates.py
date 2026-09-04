"""Bounded staged duplicate analysis with conservative reclaim estimates."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import ScanBudget
from .filesystem import FileRecord, ScanLimits, scan_tree
from .models import Envelope, StructuredError
from .policy import PolicyError, ProtectedResourcePolicy


def _stable_stat(path: Path, before: os.stat_result) -> os.stat_result:
    after = path.stat(follow_symlinks=False)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError("file_changed_during_hash")
    return after


def _quick_fingerprint(record: FileRecord, chunk_bytes: int) -> tuple[str, int]:
    path = Path(record.path)
    before = path.stat(follow_symlinks=False)
    if not os.path.isfile(path):
        raise RuntimeError("unsupported_file_type")
    digest = hashlib.sha256()
    digest.update(str(before.st_size).encode("ascii"))
    bytes_read = 0
    with path.open("rb", buffering=0) as stream:
        head = stream.read(chunk_bytes)
        bytes_read += len(head)
        digest.update(head)
        if before.st_size > chunk_bytes:
            stream.seek(max(0, before.st_size - chunk_bytes))
            tail = stream.read(chunk_bytes)
            bytes_read += len(tail)
            digest.update(tail)
    _stable_stat(path, before)
    return digest.hexdigest(), bytes_read


def _full_hash(record: FileRecord, remaining_budget: int, cancel_event: threading.Event | None) -> tuple[str, int]:
    path = Path(record.path)
    before = path.stat(follow_symlinks=False)
    if before.st_size > remaining_budget:
        raise RuntimeError("full_hash_budget_exhausted")
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb", buffering=0) as stream:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("cancelled")
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            bytes_read += len(block)
    _stable_stat(path, before)
    return digest.hexdigest(), bytes_read


def duplicate_analysis(
    path: str | Path,
    policy: ProtectedResourcePolicy,
    budget: ScanBudget,
    *,
    verify_full: bool = False,
    overrides: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    tool = "storage.duplicate_verify" if verify_full else "storage.duplicate_candidates"
    limits = ScanLimits.from_budget(budget, overrides)
    envelope = Envelope(tool, scope={"path": str(path), "limits": {
        "max_duration_seconds": limits.max_duration_seconds,
        "max_depth": limits.max_depth,
        "max_entries": limits.max_entries,
        "result_limit": limits.result_limit,
        "minimum_file_size": limits.minimum_file_size,
        "quick_fingerprint_bytes": budget.quick_fingerprint_bytes,
        "max_full_hash_bytes": budget.max_full_hash_bytes,
    }}, policy_fingerprint=policy.fingerprint)
    try:
        outcome = scan_tree(path, policy, limits, scan_mode="metadata", collect_candidates=True, cancel_event=cancel_event)
    except (PolicyError, ValueError, RuntimeError) as exc:
        envelope.error(StructuredError(str(exc), "Duplicate scan rejected or unavailable", path=str(path), operation="duplicate_scan", recoverable=False))
        return envelope.finish(failed=True)
    envelope.data["completeness"]["entries_examined"] = outcome.entries_examined
    for error in outcome.errors:
        envelope.error(error)
    for limit in outcome.limits_reached:
        envelope.limit(limit)

    by_inode: dict[tuple[int, int], list[FileRecord]] = defaultdict(list)
    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    same_name: dict[str, list[FileRecord]] = defaultdict(list)
    for record in outcome.candidates:
        by_inode[(record.device, record.inode)].append(record)
        by_size[record.logical_bytes].append(record)
        same_name[Path(record.path).name].append(record)

    groups: list[dict[str, Any]] = []
    hardlinked_paths: set[str] = set()
    for inode, records in sorted(by_inode.items()):
        if len(records) < 2:
            continue
        hardlinked_paths.update(record.path for record in records)
        groups.append({
            "classification": "same_inode_hardlink",
            "files": [record.to_dict() for record in sorted(records, key=lambda item: item.path)],
            "logical_bytes_each": records[0].logical_bytes,
            "estimated_reclaimable_bytes": 0,
            "estimate_dimension": "allocated",
            "confidence": "confirmed",
            "risk": "R4",
            "caveats": ["hardlinks_already_share_storage", "removing_one_link_does_not_reclaim_until_last_link"],
            "inode": {"device": inode[0], "inode": inode[1]},
        })

    quick_groups: list[tuple[str, list[FileRecord]]] = []
    quick_bytes_read = 0
    for size, records in sorted(by_size.items()):
        eligible = [record for record in records if record.path not in hardlinked_paths]
        if len(eligible) < 2:
            continue
        fingerprints: dict[str, list[FileRecord]] = defaultdict(list)
        protected: list[FileRecord] = []
        for record in eligible:
            decision = policy.classify(record.path)
            if not decision.content_hashing_allowed or "duplicate_quick" not in decision.allowed_scan_modes:
                protected.append(record)
                continue
            try:
                fingerprint, read_count = _quick_fingerprint(record, budget.quick_fingerprint_bytes)
                quick_bytes_read += read_count
                fingerprints[fingerprint].append(record)
            except (OSError, RuntimeError) as exc:
                envelope.error(StructuredError(str(exc), "Quick fingerprint failed", path=record.path, operation="quick_fingerprint"))
        if len(protected) >= 2:
            groups.append({
                "classification": "intentionally_redundant_or_protected",
                "files": [record.to_dict() for record in sorted(protected, key=lambda item: item.path)],
                "logical_bytes_each": size,
                "estimated_reclaimable_bytes": "unknown",
                "estimate_dimension": "allocated",
                "confidence": "unknown",
                "risk": "R4",
                "caveats": ["content_hashing_blocked_by_policy", "same_size_is_not_content_proof"],
            })
        for fingerprint, matched in sorted(fingerprints.items()):
            if len(matched) < 2:
                continue
            quick_groups.append((fingerprint, matched))

    full_bytes_read = 0
    for quick_fingerprint, records in quick_groups:
        if not verify_full:
            groups.append({
                "classification": "probable_content_match",
                "files": [record.to_dict() for record in sorted(records, key=lambda item: item.path)],
                "logical_bytes_each": records[0].logical_bytes,
                "quick_fingerprint": quick_fingerprint,
                "estimated_reclaimable_bytes": "unknown",
                "estimate_dimension": "allocated",
                "confidence": "medium",
                "risk": "R4",
                "caveats": ["full_sha256_not_performed", "no_cleanup_is_authorized"],
            })
            continue
        full_groups: dict[str, list[FileRecord]] = defaultdict(list)
        for record in records:
            decision = policy.classify(record.path)
            if not decision.content_hashing_allowed or "duplicate_full" not in decision.allowed_scan_modes:
                envelope.error(StructuredError("full_hash_blocked_by_policy", "Full hash is not allowed for this resource", path=record.path, operation="full_hash"))
                continue
            try:
                digest, read_count = _full_hash(record, budget.max_full_hash_bytes - full_bytes_read, cancel_event)
                full_bytes_read += read_count
                full_groups[digest].append(record)
            except (OSError, RuntimeError) as exc:
                envelope.error(StructuredError(str(exc), "Full hash failed", path=record.path, operation="full_hash"))
                if str(exc) in {"full_hash_budget_exhausted", "cancelled"}:
                    envelope.limit(str(exc))
                    break
        for digest, matched in sorted(full_groups.items()):
            if len(matched) < 2:
                continue
            allocated = sorted((record.allocated_bytes for record in matched), reverse=True)
            reclaim = sum(allocated[1:]) if not any(record.sparse for record in matched) else "unknown"
            groups.append({
                "classification": "confirmed_content_match",
                "files": [record.to_dict() for record in sorted(matched, key=lambda item: item.path)],
                "logical_bytes_each": matched[0].logical_bytes,
                "sha256": digest,
                "estimated_reclaimable_bytes": reclaim,
                "estimate_dimension": "allocated",
                "confidence": "confirmed" if reclaim != "unknown" else "high",
                "risk": "R4",
                "caveats": [
                    "no_cleanup_is_authorized",
                    "dependencies_and_intent_require_review",
                    "shared_extents_or_reflinks_may_reduce_physical_reclaim",
                ] + (["sparse_file_prevents_reclaim_estimate"] if reclaim == "unknown" else []),
            })

    for name, records in sorted(same_name.items()):
        sizes = {record.logical_bytes for record in records}
        if len(records) >= 2 and len(sizes) > 1:
            groups.append({
                "classification": "same_name_only",
                "name": name,
                "files": [record.to_dict() for record in sorted(records, key=lambda item: item.path)[:limits.result_limit]],
                "estimated_reclaimable_bytes": "unknown",
                "estimate_dimension": "allocated",
                "confidence": "low",
                "risk": "R4",
                "caveats": ["same_name_is_not_duplicate_content"],
            })

    groups.sort(key=lambda group: (group["classification"], -int(group.get("logical_bytes_each", 0)), str(group.get("name", ""))))
    if len(groups) > limits.result_limit:
        groups = groups[:limits.result_limit]
        envelope.limit("result_limit")
    envelope.data["result"] = {
        "groups": groups,
        "candidate_files": len(outcome.candidates),
        "quick_fingerprint_bytes_read": quick_bytes_read,
        "full_hash_bytes_read": full_bytes_read,
        "full_verification_requested": verify_full,
        "hardlink_reclaim_policy": "zero",
        "overlap_policy": "each_file_belongs_to_one_content_hash_group",
    }
    return envelope.finish()

