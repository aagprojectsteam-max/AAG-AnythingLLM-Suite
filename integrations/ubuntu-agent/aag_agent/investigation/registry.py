"""Strict loader for project-owned diagnostic playbooks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aag_agent.diagnostics import PROFILES, _requests
from aag_agent.remediation.bridge import BRIDGE_TARGET

from .models import IDENTIFIER, InvestigationValidationError, canonical_json

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
DEFAULT_REGISTRY = PROJECT_ROOT / "config/diagnostic-playbooks-v1.json"
TOP_FIELDS = {"schema", "registry_version", "execution_authority", "read_only", "playbooks"}
PLAYBOOK_FIELDS = {
    "playbook_id", "version", "description", "target_identity", "lifecycle_state",
    "diagnostic_steps", "hypotheses", "stop_policy", "remediation_handoff",
}
STEP_FIELDS = {"step_id", "collector", "profile", "inputs", "freshness_seconds"}
HYPOTHESIS_FIELDS = {
    "hypothesis_id", "statement", "predicate", "selection_reason", "next_check",
}
PREDICATE_FIELDS = {"kind", "fact", "path", "operator", "threshold", "numerator_path", "denominator_path"}
PREDICATE_KINDS = {"percent", "ratio", "nonempty", "equals", "list_numeric"}
OPERATORS = {"ge", "gt", "le", "lt", "eq", "ne"}
FIXED_PLAYBOOK_BOUNDARIES = {
    "bridge.readiness_investigation": {
        "target_identity": BRIDGE_TARGET,
        "steps": [("exact_bridge_observer_v1", "bridge_exact", {"service": BRIDGE_TARGET})],
    },
    "system.performance_investigation": {
        "target_identity": "local-ubuntu-host",
        "steps": [("typed_diagnostics_v1", "performance", {})],
    },
    "storage.root_pressure_investigation": {
        "target_identity": "/",
        "steps": [("typed_diagnostics_v1", "storage_mount", {"path": "/"})],
    },
}


@dataclass(frozen=True)
class Playbook:
    data: Mapping[str, Any]
    registry_sha256: str

    @property
    def playbook_id(self) -> str:
        return str(self.data["playbook_id"])

    @property
    def version(self) -> int:
        return int(self.data["version"])


class PlaybookRegistry:
    def __init__(self, path: Path = DEFAULT_REGISTRY) -> None:
        self.path = Path(path)
        if self.path.is_symlink():
            raise InvestigationValidationError("registry_symlink_forbidden")
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvestigationValidationError("invalid_registry_json") from exc
        self._validate(payload)
        self.payload = payload
        self._playbooks = {
            (item["playbook_id"], item["version"]): Playbook(item, self.sha256)
            for item in payload["playbooks"]
        }

    @staticmethod
    def _exact_fields(value: Mapping[str, Any], allowed: set[str], error: str) -> None:
        if not isinstance(value, Mapping) or set(value) != allowed:
            raise InvestigationValidationError(error)

    def _validate(self, payload: Any) -> None:
        self._exact_fields(payload, TOP_FIELDS, "invalid_registry_schema")
        if payload["schema"] != "aag-diagnostic-playbook-registry-v1" or payload["registry_version"] != 1:
            raise InvestigationValidationError("unsupported_registry_version")
        if payload["execution_authority"] != "NONE" or payload["read_only"] is not True:
            raise InvestigationValidationError("registry_authority_violation")
        if not isinstance(payload["playbooks"], list) or not 1 <= len(payload["playbooks"]) <= 16:
            raise InvestigationValidationError("invalid_playbook_count")
        seen: set[tuple[str, int]] = set()
        for playbook in payload["playbooks"]:
            self._exact_fields(playbook, PLAYBOOK_FIELDS, "invalid_playbook_fields")
            key = (playbook["playbook_id"], playbook["version"])
            if (
                not isinstance(key[0], str) or IDENTIFIER.fullmatch(key[0]) is None
                or not isinstance(key[1], int) or key[1] != 1 or key in seen
                or playbook["lifecycle_state"] != "ACCEPTED"
            ):
                raise InvestigationValidationError("invalid_playbook_identity")
            seen.add(key)
            if not isinstance(playbook["target_identity"], str) or len(playbook["target_identity"]) > 256:
                raise InvestigationValidationError("invalid_target_identity")
            boundary = FIXED_PLAYBOOK_BOUNDARIES.get(playbook["playbook_id"])
            if boundary is None or playbook["target_identity"] != boundary["target_identity"]:
                raise InvestigationValidationError("playbook_boundary_not_allowlisted")
            steps = playbook["diagnostic_steps"]
            if not isinstance(steps, list) or not 1 <= len(steps) <= 2:
                raise InvestigationValidationError("invalid_diagnostic_steps")
            observation_count = 0
            for step in steps:
                self._exact_fields(step, STEP_FIELDS, "invalid_step_fields")
                if step["collector"] not in {"typed_diagnostics_v1", "exact_bridge_observer_v1"}:
                    raise InvestigationValidationError("unknown_collector")
                if not isinstance(step["freshness_seconds"], int) or not 5 <= step["freshness_seconds"] <= 600:
                    raise InvestigationValidationError("invalid_freshness")
                if step["collector"] == "exact_bridge_observer_v1":
                    if step["profile"] != "bridge_exact" or step["inputs"] != {"service": BRIDGE_TARGET}:
                        raise InvestigationValidationError("bridge_step_not_exact")
                    observation_count += 2
                else:
                    if step["profile"] not in PROFILES or not isinstance(step["inputs"], dict):
                        raise InvestigationValidationError("invalid_diagnostic_profile")
                    try:
                        observation_count += len(_requests(step["profile"], step["inputs"]))
                    except Exception as exc:
                        raise InvestigationValidationError("invalid_diagnostic_inputs") from exc
            actual_steps = [(item["collector"], item["profile"], item["inputs"]) for item in steps]
            if actual_steps != boundary["steps"]:
                raise InvestigationValidationError("diagnostic_step_boundary_drift")
            if observation_count > 8:
                raise InvestigationValidationError("global_observation_limit_exceeded")
            hypotheses = playbook["hypotheses"]
            if not isinstance(hypotheses, list) or not 1 <= len(hypotheses) <= 12:
                raise InvestigationValidationError("invalid_hypothesis_count")
            hypothesis_ids: set[str] = set()
            for hypothesis in hypotheses:
                self._exact_fields(hypothesis, HYPOTHESIS_FIELDS, "invalid_hypothesis_fields")
                hypothesis_id = hypothesis["hypothesis_id"]
                if not isinstance(hypothesis_id, str) or IDENTIFIER.fullmatch(hypothesis_id) is None or hypothesis_id in hypothesis_ids:
                    raise InvestigationValidationError("invalid_hypothesis_id")
                hypothesis_ids.add(hypothesis_id)
                predicate = hypothesis["predicate"]
                if not isinstance(predicate, Mapping) or set(predicate) - PREDICATE_FIELDS:
                    raise InvestigationValidationError("invalid_predicate_fields")
                if predicate.get("kind") not in PREDICATE_KINDS or predicate.get("operator") not in OPERATORS:
                    raise InvestigationValidationError("invalid_predicate")
                if not isinstance(predicate.get("fact"), str):
                    raise InvestigationValidationError("invalid_predicate_fact")
                for path_field in ("path", "numerator_path", "denominator_path"):
                    if path_field in predicate and (
                        not isinstance(predicate[path_field], list)
                        or not all(isinstance(part, (str, int)) for part in predicate[path_field])
                        or len(predicate[path_field]) > 8
                    ):
                        raise InvestigationValidationError("invalid_predicate_path")
                if "threshold" in predicate and not isinstance(predicate["threshold"], (int, float, str, bool)):
                    raise InvestigationValidationError("invalid_predicate_threshold")
            stop = playbook["stop_policy"]
            if not isinstance(stop, Mapping) or set(stop) != {"max_steps", "max_seconds", "stop_after_supported"}:
                raise InvestigationValidationError("invalid_stop_policy")
            if stop["max_steps"] != len(steps) or not isinstance(stop["max_seconds"], int) or not 1 <= stop["max_seconds"] <= 30 or not isinstance(stop["stop_after_supported"], bool):
                raise InvestigationValidationError("invalid_stop_policy")
            handoff = playbook["remediation_handoff"]
            if not isinstance(handoff, Mapping) or set(handoff) != {"allowed", "operation_id", "operation_version", "required_hypothesis"}:
                raise InvestigationValidationError("invalid_remediation_handoff")
            if handoff["allowed"]:
                if (
                    handoff != {
                        "allowed": True,
                        "operation_id": "bridge.restart.readiness_failure",
                        "operation_version": 1,
                        "required_hypothesis": "bridge.readiness_failure",
                    }
                    or playbook["target_identity"] != BRIDGE_TARGET
                ):
                    raise InvestigationValidationError("untrusted_remediation_handoff")
            elif any(handoff[key] is not None for key in ("operation_id", "operation_version", "required_hypothesis")):
                raise InvestigationValidationError("disabled_handoff_must_be_empty")
        canonical_json(payload)

    def get(self, playbook_id: str, version: int = 1) -> Playbook:
        if not isinstance(playbook_id, str) or IDENTIFIER.fullmatch(playbook_id) is None or not isinstance(version, int):
            raise InvestigationValidationError("invalid_playbook_request")
        try:
            return self._playbooks[(playbook_id, version)]
        except KeyError as exc:
            raise InvestigationValidationError("playbook_not_allowlisted") from exc

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "playbook_id": item.playbook_id,
                "version": item.version,
                "description": item.data["description"],
                "target_identity": item.data["target_identity"],
                "read_only": True,
                "execution_authority": "NONE",
            }
            for item in self._playbooks.values()
        ]
