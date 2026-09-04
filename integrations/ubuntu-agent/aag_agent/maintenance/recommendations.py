"""Conservative dry-run maintenance plans; no execution path exists."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .config import MaintenanceConfig, ScanBudget
from .filesystem import ScanLimits, scan_tree
from .history import HistoryError, HistoryStore
from .models import Envelope, StructuredError
from .policy import PolicyError, ProtectedResourcePolicy


def _item_id(category: str, target: str) -> str:
    digest = hashlib.sha256(f"maintenance-v1\0{category}\0{target}".encode("utf-8", errors="surrogateescape")).hexdigest()
    return "maint-v1-" + digest[:20]


def maintenance_plan(
    path: str | Path,
    config: MaintenanceConfig,
    policy: ProtectedResourcePolicy,
    budget: ScanBudget,
    *,
    history: HistoryStore | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limits = ScanLimits.from_budget(budget, overrides)
    envelope = Envelope("maintenance.plan", scope={"path": str(path), "mode": "dry_run_only"}, policy_fingerprint=policy.fingerprint)
    try:
        outcome = scan_tree(path, policy, limits, scan_mode="summary")
    except (PolicyError, ValueError, RuntimeError) as exc:
        envelope.error(StructuredError(str(exc), "Maintenance-plan scan rejected or unavailable", path=str(path), operation="plan_scan", recoverable=False))
        return envelope.finish(failed=True)
    envelope.data["completeness"]["entries_examined"] = outcome.entries_examined
    for error in outcome.errors:
        envelope.error(error)
    for limit in outcome.limits_reached:
        envelope.limit(limit)
    try:
        growth = (history or HistoryStore(config.history_path)).latest_growth(str(outcome.root))
    except HistoryError:
        growth = {"comparable": False, "reason": "history_unavailable"}
    growth_by_path = {
        item["path"]: item
        for item in growth.get("contributors", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }

    items: list[dict[str, Any]] = []
    estimated_total = 0
    for target, aggregate in sorted(outcome.top.items(), key=lambda item: (-item[1].allocated_bytes, item[0]))[:limits.result_limit]:
        decision = policy.classify(target)
        classification = str(decision.cleanup_classification)
        eligible = classification == "LOW_RISK_CANDIDATE" and decision.cleanup_may_be_proposed
        estimate: int | str = aggregate.allocated_bytes if eligible else "unknown"
        if isinstance(estimate, int):
            estimated_total += estimate
        item = {
            "item_id": _item_id(decision.protection_class, target),
            "category": decision.protection_class,
            "classification": classification,
            "target": target,
            "reason": (
                "The path is positively registered as generated output and may be reviewed for retention."
                if eligible else
                "Protection or dependency evidence is insufficient for a cleanup proposal."
            ),
            "evidence_refs": ["filesystem:scan-summary", f"policy:{decision.resource_id}"],
            "logical_bytes": aggregate.logical_bytes,
            "allocated_bytes": aggregate.allocated_bytes,
            "growth_since_previous": growth_by_path.get(target, "unknown"),
            "estimated_reclaimable_bytes": estimate,
            "estimate_dimension": "allocated",
            "estimate_caveats": [
                "estimate_is_not_a_deletion_instruction",
                "shared_extents_and_active_use_may_reduce_reclaim",
                "nested_candidates_are_not_added",
            ],
            "confidence": decision.confidence if eligible else "unknown",
            "risk": "R4",
            "dependency_status": decision.dependency_status,
            "known_dependencies": list(decision.dependencies),
            "unknown_dependencies": decision.dependency_status != "known",
            "protection_policy": decision.to_dict(),
            "required_approval_level": "strong_confirmation_required" if eligible else "not_eligible_in_v1",
            "required_backup_or_rollback": "verified backup or explicit retention decision required before any future deletion",
            "dry_run_description": "Review the registered target, ownership, active use, backup state, and exact selected files; make no changes in V1.",
            "proposed_verification": [
                "re-scan the exact target before any future action",
                "verify dependent applications and mounts",
                "measure free space after any separately authorized future action",
            ],
            "rollback_concept": "restore only from a verified backup; no V1 rollback action is implemented",
            "why_not_executed": "Maintenance Intelligence V1 has zero cleanup authority.",
            "execution_status": "not_executed",
        }
        items.append(item)
    envelope.data["recommendations"] = [
        {
            "recommendation_id": item["item_id"],
            "category": item["category"],
            "summary": item["reason"],
            "rationale": item["why_not_executed"],
            "classification": item["classification"],
            "risk": item["risk"],
            "confidence": item["confidence"],
            "evidence_refs": item["evidence_refs"],
        }
        for item in items
    ]
    envelope.data["result"] = {
        "schema": "aag-maintenance-plan-v1",
        "mode": "dry_run_only",
        "execution_authority": "NONE",
        "execution_status": "not_executed",
        "root": str(outcome.root),
        "mount_identity": outcome.mount_identity,
        "items": items,
        "estimated_reclaimable_bytes": estimated_total,
        "estimate_dimension": "allocated",
        "estimate_policy": "disjoint_top_level_positive_candidates_only",
        "zero_mutations": True,
        "growth_context": growth,
    }
    return envelope.finish()


def explain_plan_item(plan: dict[str, Any], item_id: str) -> dict[str, Any]:
    envelope = Envelope("maintenance.explain", scope={"item_id": item_id}, policy_fingerprint=plan.get("policy_fingerprint", "unknown"))
    for item in plan.get("result", {}).get("items", []):
        if item.get("item_id") == item_id:
            envelope.data["result"] = {
                "item": item,
                "explanation": {
                    "observation": f"{item['target']} uses {item['allocated_bytes']} allocated bytes.",
                    "inference": item["reason"],
                    "confidence": item["confidence"],
                    "risk": item["risk"],
                    "coverage": plan.get("completeness", {}),
                    "not_executed": True,
                },
            }
            return envelope.finish()
    envelope.error(StructuredError("plan_item_not_found", "The requested maintenance-plan item was not found", operation="maintenance_explain", recoverable=False))
    return envelope.finish(failed=True)

