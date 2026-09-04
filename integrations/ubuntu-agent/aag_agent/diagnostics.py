"""Bounded read-only diagnostic orchestration for Ubuntu troubleshooting."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .observations import ObservationError, observe

BUNDLE_SCHEMA = "aag-diagnostic-bundle-v1"
MAX_OBSERVATIONS = 8
MAX_TOTAL_SECONDS = 30.0
MAX_BUNDLE_BYTES = 256_000
MAX_FOLLOW_UP_DEPTH = 1
MAX_PROFILE_REQUESTS = 2


@dataclass(frozen=True)
class Profile:
    allowed_inputs: frozenset[str]
    required_inputs: frozenset[str]


PROFILES = {
    "general_system": Profile(frozenset(), frozenset()),
    "performance": Profile(frozenset(), frozenset()),
    "service": Profile(frozenset({"service", "manager"}), frozenset({"service", "manager"})),
    "application_start": Profile(frozenset({"service", "manager", "pid"}), frozenset()),
    "network": Profile(frozenset({"interface"}), frozenset()),
    "storage_mount": Profile(frozenset({"path"}), frozenset({"path"})),
    "docker": Profile(frozenset({"container"}), frozenset()),
    "package": Profile(frozenset({"package"}), frozenset({"package"})),
    "boot_health": Profile(frozenset(), frozenset()),
}


def _requests(profile: str, inputs: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    if profile == "general_system":
        return [("kernel", {}), ("uptime", {}), ("memory", {}), ("filesystem", {"path": "/"}), ("failed_units", {"manager": "system"}), ("boot_events", {})]
    if profile == "performance":
        return [("uptime", {}), ("memory", {}), ("processes", {}), ("filesystem", {"path": "/"}), ("failed_units", {"manager": "system"})]
    if profile == "service":
        target = {"service": inputs["service"], "manager": inputs["manager"]}
        return [("systemd", target), ("journal", {**target, "lines": 50})]
    if profile == "application_start":
        requests: list[tuple[str, dict[str, Any]]] = []
        if inputs.get("service") is not None:
            target = {"service": inputs["service"], "manager": inputs.get("manager", "user")}
            requests.extend([("systemd", target), ("journal", {**target, "lines": 50})])
        if inputs.get("pid") is not None:
            requests.append(("process", {"pid": inputs["pid"]}))
        return requests
    if profile == "network":
        requests = [("network_overview", {}), ("routes", {})]
        if inputs.get("interface") is not None:
            requests.append(("network", {"interface": inputs["interface"]}))
        return requests
    if profile == "storage_mount":
        return [("block_devices", {}), ("mount", {"path": inputs["path"]}), ("filesystem", {"path": inputs["path"]})]
    if profile == "docker":
        requests = [("docker_overview", {})]
        if inputs.get("container") is not None:
            requests.append(("docker", {"container": inputs["container"]}))
        return requests
    if profile == "package":
        return [("package", {"package": inputs["package"]})]
    if profile == "boot_health":
        return [("failed_units", {"manager": "system"}), ("boot_events", {})]
    raise ObservationError("unsupported_profile")


def _public_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    """Remove raw command output; retain normalized facts and provenance."""
    allowed = {
        "schema", "domain", "observed_at", "duration_ms", "target", "provenance",
        "status", "returncode", "normalization_error", "truncated",
        "timeout_seconds", "error", "read_only", "mutated",
    }
    return {key: item[key] for key in allowed if key in item}


def diagnose(
    profile: str,
    inputs: Mapping[str, Any] | None = None,
    *,
    observer: Callable[..., dict[str, Any]] = observe,
    max_total_seconds: float = MAX_TOTAL_SECONDS,
) -> dict[str, Any]:
    """Run one trusted profile. No caller-controlled binary or argv exists."""
    captured_at = time.time()
    started = time.monotonic()
    base = {"schema": BUNDLE_SCHEMA, "profile": profile, "captured_at": captured_at, "read_only": True, "mutated": False}
    if profile not in PROFILES:
        return {**base, "status": "UNSUPPORTED", "duration_ms": 0.0, "observations": [], "facts": {}, "limitations": [], "errors": [{"code": "unsupported_profile"}]}
    if inputs is not None and not isinstance(inputs, Mapping):
        return {**base, "status": "ERROR", "duration_ms": 0.0, "observations": [], "facts": {}, "limitations": [], "errors": [{"code": "invalid_input", "detail": "inputs_must_be_object"}]}
    values = dict(inputs or {})
    definition = PROFILES[profile]
    if set(values) - definition.allowed_inputs or definition.required_inputs - set(values) or any(value is None for key, value in values.items() if key in definition.required_inputs):
        return {**base, "status": "ERROR", "duration_ms": 0.0, "observations": [], "facts": {}, "limitations": [], "errors": [{"code": "invalid_input", "detail": "profile_input_schema"}]}
    if profile == "application_start" and not any(values.get(key) is not None for key in ("service", "pid")):
        return {**base, "status": "INDETERMINATE", "duration_ms": 0.0, "observations": [], "facts": {}, "limitations": ["service_or_pid_required_for_observation"], "errors": []}
    try:
        requests = _requests(profile, values)
    except (KeyError, ObservationError) as exc:
        return {**base, "status": "ERROR", "duration_ms": 0.0, "observations": [], "facts": {}, "limitations": [], "errors": [{"code": "invalid_input", "detail": str(exc)}]}
    if len(requests) > MAX_OBSERVATIONS:
        return {**base, "status": "ERROR", "duration_ms": 0.0, "observations": [], "facts": {}, "limitations": [], "errors": [{"code": "profile_limit_exceeded"}]}

    observations, facts, errors = [], {}, []
    for index, (domain, query) in enumerate(requests):
        remaining = max_total_seconds - (time.monotonic() - started)
        if remaining <= 0:
            errors.append({"code": "total_timeout", "collector": domain})
            break
        try:
            raw = observer(domain, query, timeout=min(15.0, remaining))
        except ObservationError as exc:
            raw = {"schema": "aag-observation-v1", "domain": domain, "target": query, "status": "invalid_input", "error": str(exc), "read_only": True, "mutated": False}
        except Exception as exc:  # injected collectors must also fail closed
            raw = {"schema": "aag-observation-v1", "domain": domain, "target": query, "status": "collector_error", "error": type(exc).__name__, "read_only": True, "mutated": False}
        normalized_facts = raw.get("facts")
        item = _public_observation(raw)
        observations.append(item)
        key = domain if domain not in facts else f"{domain}_{index + 1}"
        if item.get("status") == "completed" and item.get("normalization_error") is None:
            facts[key] = {"state": "OBSERVED", "value": normalized_facts}
        else:
            state = "UNOBSERVABLE" if item.get("status") in {"permission_denied", "missing_binary"} else "ERROR"
            facts[key] = {"state": state, "value": None}
            code = item.get("normalization_error") or item.get("status", "collector_error")
            errors.append({"code": code, "collector": domain, "detail": item.get("error")})

    status = "OBSERVED" if not errors else ("INDETERMINATE" if observations else "ERROR")
    limitations = ["facts_are_observations_not_root-cause_claims", "unknown_or_unobservable_is_not_failure"]
    if profile in {"service", "application_start"}:
        limitations.append("active_service_state_does_not_prove_application_health")
    bundle = {**base, "status": status, "duration_ms": round((time.monotonic() - started) * 1000, 3), "limits": {"maximum_observations": MAX_OBSERVATIONS, "maximum_total_seconds": max_total_seconds, "maximum_bundle_bytes": MAX_BUNDLE_BYTES, "maximum_follow_up_depth": MAX_FOLLOW_UP_DEPTH}, "observations": observations, "facts": facts, "limitations": limitations, "errors": errors}
    if len(json.dumps(bundle, ensure_ascii=False, default=str).encode("utf-8")) > MAX_BUNDLE_BYTES:
        return {**base, "status": "ERROR", "duration_ms": bundle["duration_ms"], "observations": [], "facts": {}, "limitations": ["bundle_withheld_after_size_limit"], "errors": [{"code": "output_truncated"}]}
    return bundle


def diagnose_many(
    requests: list[Mapping[str, Any]],
    *,
    observer: Callable[..., dict[str, Any]] = observe,
    max_total_seconds: float = MAX_TOTAL_SECONDS,
) -> dict[str, Any]:
    """Run up to two model-selected profiles under one global budget."""
    started = time.monotonic()
    captured_at = time.time()
    base = {"schema": "aag-diagnostic-session-v1", "captured_at": captured_at, "read_only": True, "mutated": False}
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_PROFILE_REQUESTS or not all(isinstance(item, Mapping) and set(item) == {"profile", "inputs"} for item in requests):
        return {**base, "status": "ERROR", "duration_ms": 0.0, "bundles": [], "errors": [{"code": "invalid_diagnostic_requests"}]}
    try:
        planned = [_requests(str(item["profile"]), dict(item["inputs"])) for item in requests]
    except (TypeError, ValueError, KeyError, ObservationError):
        return {**base, "status": "ERROR", "duration_ms": 0.0, "bundles": [], "errors": [{"code": "invalid_diagnostic_requests"}]}
    if sum(len(items) for items in planned) > MAX_OBSERVATIONS:
        return {**base, "status": "ERROR", "duration_ms": 0.0, "bundles": [], "errors": [{"code": "global_observation_limit_exceeded"}]}
    bundles: list[dict[str, Any]] = []
    observations_used = 0
    for request in requests:
        remaining = max_total_seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        bundle = diagnose(str(request["profile"]), request["inputs"], observer=observer, max_total_seconds=remaining)
        observations_used += len(bundle.get("observations", []))
        bundles.append(bundle)
    errors = [error for bundle in bundles for error in bundle.get("errors", [])]
    result = {**base, "status": "OBSERVED" if not errors and len(bundles) == len(requests) else "INDETERMINATE", "duration_ms": round((time.monotonic() - started) * 1000, 3), "limits": {"maximum_profiles": MAX_PROFILE_REQUESTS, "maximum_observations": MAX_OBSERVATIONS, "maximum_total_seconds": max_total_seconds, "maximum_output_bytes": MAX_BUNDLE_BYTES, "maximum_follow_up_depth": MAX_FOLLOW_UP_DEPTH}, "bundles": bundles, "errors": errors}
    if len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")) > MAX_BUNDLE_BYTES:
        return {**base, "status": "ERROR", "duration_ms": result["duration_ms"], "bundles": [], "errors": [{"code": "output_truncated"}]}
    return result
