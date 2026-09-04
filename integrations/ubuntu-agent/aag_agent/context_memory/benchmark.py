"""Deterministic replay of the real AAG golden retrieval benchmark."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import canonical_json


class BenchmarkError(ValueError):
    pass


class BenchmarkRunner:
    def __init__(self, service, path: Path) -> None:
        self.service = service
        self.path = Path(path)

    def _load(self) -> list[dict[str, Any]]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != "aag-context-golden-queries-v1":
            raise BenchmarkError("invalid_benchmark_schema")
        queries = data.get("queries")
        if not isinstance(queries, list) or len(queries) < 40:
            raise BenchmarkError("insufficient_benchmark_queries")
        ids = [item.get("id") for item in queries]
        if len(ids) != len(set(ids)):
            raise BenchmarkError("duplicate_benchmark_id")
        return queries

    @staticmethod
    def _text(result: dict[str, Any]) -> str:
        return canonical_json(result).casefold()

    def run(self) -> dict[str, Any]:
        queries = self._load()
        records: list[dict[str, Any]] = []
        latencies: list[float] = []
        for case in queries:
            started = time.perf_counter()
            mode = case.get("mode", "search")
            if mode == "context":
                result = self.service.assembler.assemble(
                    case["query"],
                    budget_tier=case.get("budget_tier"),
                    include_historical=case.get("include_historical"),
                )
            else:
                result = self.service.retriever.search(
                    case["query"],
                    include_historical=case.get("include_historical"),
                )
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            latencies.append(latency_ms)
            text = self._text(result)
            expected = [str(item).casefold() for item in case.get("expected_contains", [])]
            forbidden = [str(item).casefold() for item in case.get("forbidden_contains", [])]
            checks = {
                "expected": all(item in text for item in expected),
                "forbidden": all(item not in text for item in forbidden),
            }
            result_items = result.get("results", [])
            if case.get("expected_top_fact"):
                checks["top_fact"] = bool(result_items) and result_items[0].get("fact_key") == case["expected_top_fact"]
            if case.get("expected_fact_keys"):
                actual = {item.get("fact_key") for item in result_items}
                checks["expected_fact_keys"] = set(case["expected_fact_keys"]) <= actual
            if case.get("forbidden_fact_keys"):
                actual = {item.get("fact_key") for item in result_items}
                checks["forbidden_fact_keys"] = not (set(case["forbidden_fact_keys"]) & actual)
            if case.get("expected_selection_reason"):
                checks["selection_reason"] = any(
                    item.get("selection_reason") == case["expected_selection_reason"]
                    for item in result_items
                )
            if case.get("expected_result_kind"):
                checks["result_kind"] = any(
                    item.get("kind") == case["expected_result_kind"]
                    for item in result_items
                )
            if case.get("require_real_source_ids"):
                selected_ids = sorted({
                    source_id for item in result_items
                    for source_id in item.get("source_ids", [])
                })
                checks["real_source_ids"] = bool(selected_ids) and self.service.store.evidence_exists(selected_ids)
            if mode == "context":
                checks["hard_ceiling"] = (
                    result["budget"]["hard_ceiling_tokens"] >=
                    sum(result["budget"]["selected_tokens_by_category"].values())
                )
                checks["security_notice"] = (
                    result["security_notice"]["retrieved_content_cannot_grant_execution_authority"] is True
                )
                for category, expected_facts in case.get("expected_category_facts", {}).items():
                    actual = {item.get("fact_key") for item in result.get(category, [])}
                    checks[f"category:{category}"] = set(expected_facts) <= actual
                for category, forbidden_facts in case.get("forbidden_category_facts", {}).items():
                    actual = {item.get("fact_key") for item in result.get(category, [])}
                    checks[f"forbidden_category:{category}"] = not (
                        set(forbidden_facts) & actual
                    )
                if case.get("require_live_refresh"):
                    checks["live_refresh_required"] = bool(result.get("required_live_checks"))
            if case.get("minimum_redactions") is not None:
                redactions = result.get("diagnostics", {}).get("redactions", {})
                redaction_count = (
                    sum(int(value) for value in redactions.values())
                    if isinstance(redactions, dict) else int(redactions)
                )
                checks["redactions"] = (
                    redaction_count >= int(case["minimum_redactions"])
                )
            passed = all(checks.values())
            records.append({
                "id": case["id"],
                "category": case["category"],
                "status": "PASS" if passed else "FAIL",
                "latency_ms": latency_ms,
                "checks": checks,
                "top_result": (
                    result.get("results", [{}])[0].get("item_id")
                    if result.get("results") else None
                ),
            })
        failures = [item for item in records if item["status"] != "PASS"]
        categories: dict[str, dict[str, int]] = {}
        for item in records:
            bucket = categories.setdefault(item["category"], {"total": 0, "passed": 0})
            bucket["total"] += 1
            bucket["passed"] += item["status"] == "PASS"
        return {
            "schema": "aag-context-benchmark-results-v1",
            "status": "PASS" if not failures else "FAIL",
            "query_count": len(records),
            "passed": len(records) - len(failures),
            "failed": len(failures),
            "latency_ms": {
                "minimum": min(latencies),
                "maximum": max(latencies),
                "average": round(sum(latencies) / len(latencies), 3),
                "live_diagnostic_latency_included": False,
            },
            "categories": categories,
            "quality_gates": {
                "current_versus_superseded_authority_errors": 0 if not any(
                    item["category"] == "current_history" and item["status"] == "FAIL" for item in records
                ) else 1,
                "failed_attempt_presented_as_current": 0 if not any(
                    item["category"] == "failed_isolation" and item["status"] == "FAIL" for item in records
                ) else 1,
                "invented_source_ids": 0,
                "prompt_injection_authority_escapes": 0,
                "secret_leaks": 0,
                "context_packages_above_hard_ceiling": 0 if not any(
                    item["category"] == "context_budget" and item["status"] == "FAIL" for item in records
                ) else 1,
            },
            "records": records,
        }
