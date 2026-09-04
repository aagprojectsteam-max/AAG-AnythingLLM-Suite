"""Automated contract onboarding checks; never promotes contracts."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from .contracts import ContractError, validate_contract
from .policy import evaluate


def review_contract(
    raw: Any,
    positive_fixtures: Iterable[Mapping[str, Any]] = (),
    negative_fixtures: Iterable[Mapping[str, Any]] = (),
    *,
    executor_probe: Callable[[], bool] | None = None,
    verifier_probe: Callable[[], bool] | None = None,
    rollback_probe: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        contract = validate_contract(raw)
        checks.append({"name": "schema_and_static_safety", "passed": True})
    except ContractError as exc:
        return {"status": "REJECTED", "checks": [{"name": "schema_and_static_safety", "passed": False, "error": str(exc)}], "promoted": False}
    for index, fixture in enumerate(positive_fixtures):
        checks.append({"name": f"positive_fixture_{index}", "passed": evaluate(contract, fixture, now=fixture.get("observed_at"))["allowed"]})
    for index, fixture in enumerate(negative_fixtures):
        checks.append({"name": f"negative_fixture_{index}", "passed": not evaluate(contract, fixture, now=fixture.get("observed_at", 0))["allowed"]})
    for name, probe in (
        ("intercepted_executor", executor_probe),
        ("post_verifier", verifier_probe),
        ("rollback_declaration", rollback_probe),
    ):
        passed = False
        if probe is not None:
            try:
                passed = probe() is True
            except Exception:
                passed = False
        checks.append({"name": name, "passed": passed})
    passed = all(item["passed"] for item in checks)
    return {"status": "TESTED" if passed else "REJECTED", "checks": checks, "promoted": False, "acceptance_requires_human_decision": True}
