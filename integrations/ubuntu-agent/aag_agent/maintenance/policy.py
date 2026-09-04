"""Fail-closed protected-resource policy and dependency graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import DEFAULT_POLICY_PATH, MaintenanceConfig
from .models import ProtectionClass, RecommendationClass, stable_fingerprint, utc_now


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ResourceRule:
    resource_id: str
    path: Path
    match: str
    component: str
    description: str
    protection_class: str
    allowed_scan_modes: tuple[str, ...]
    content_hashing_allowed: bool
    cleanup_may_be_proposed: bool
    expected_mount: bool
    expected_service: str | None
    dependencies: tuple[str, ...]
    dependents: tuple[str, ...]
    source: str
    confidence: str


@dataclass(frozen=True)
class ProtectionDecision:
    canonical_path: str
    resource_id: str
    component: str | None
    protection_class: str
    allowed_scan_modes: tuple[str, ...]
    content_hashing_allowed: bool
    cleanup_may_be_proposed: bool
    cleanup_classification: str
    dependency_status: str
    dependencies: tuple[str, ...]
    dependents: tuple[str, ...]
    provenance: tuple[str, ...]
    confidence: str
    last_verified_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("allowed_scan_modes", "dependencies", "dependents", "provenance"):
            data[key] = list(data[key])
        return data


RULE_FIELDS = {
    "resource_id", "path", "match", "component", "description",
    "protection_class", "allowed_scan_modes", "content_hashing_allowed",
    "cleanup_may_be_proposed", "expected_mount", "expected_service",
    "dependencies", "dependents", "source", "confidence",
}

SCAN_MODES = {"summary", "metadata", "duplicate_quick", "duplicate_full"}


class ProtectedResourcePolicy:
    def __init__(
        self,
        config: MaintenanceConfig,
        *,
        policy_path: Path = DEFAULT_POLICY_PATH,
        registry_path: Path | None = None,
    ) -> None:
        self.config = config
        self.policy_path = policy_path
        self.registry_path = registry_path or config.external_registry_path
        raw = self._read_local_policy(policy_path)
        self.rules = tuple(sorted(self._parse_rules(raw["resources"]), key=lambda rule: len(rule.path.parts), reverse=True))
        self.default_class = raw["default_class"]
        self.registry_status, self.registry_components, registry_fingerprint = self._read_registry(self.registry_path)
        self.fingerprint = stable_fingerprint({
            "local": raw,
            "registry": registry_fingerprint,
        })

    @staticmethod
    def _read_local_policy(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PolicyError("protected_policy_missing") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyError("protected_policy_unreadable") from exc
        if not isinstance(raw, dict) or set(raw) != {"schema", "default_class", "unknown_cleanup_policy", "resources"}:
            raise PolicyError("protected_policy_schema_invalid")
        if raw["schema"] != "aag-maintenance-protected-resources-v1" or raw["default_class"] != "unknown" or raw["unknown_cleanup_policy"] != "review_required":
            raise PolicyError("protected_policy_header_invalid")
        if not isinstance(raw["resources"], list):
            raise PolicyError("protected_policy_resources_invalid")
        return raw

    @staticmethod
    def _parse_rules(resources: Iterable[Any]) -> list[ResourceRule]:
        rules: list[ResourceRule] = []
        ids: set[str] = set()
        for item in resources:
            if not isinstance(item, dict) or set(item) != RULE_FIELDS:
                raise PolicyError("protected_resource_fields_invalid")
            if item["resource_id"] in ids:
                raise PolicyError("duplicate_resource_id")
            ids.add(item["resource_id"])
            path = Path(item["path"])
            if not path.is_absolute() or ".." in path.parts:
                raise PolicyError("protected_resource_path_invalid")
            if item["match"] not in {"exact", "subtree"}:
                raise PolicyError("protected_resource_match_invalid")
            if item["protection_class"] not in {value.value for value in ProtectionClass}:
                raise PolicyError("protection_class_invalid")
            modes = item["allowed_scan_modes"]
            if not isinstance(modes, list) or not set(modes) <= SCAN_MODES:
                raise PolicyError("allowed_scan_modes_invalid")
            if not all(isinstance(item[name], bool) for name in ("content_hashing_allowed", "cleanup_may_be_proposed", "expected_mount")):
                raise PolicyError("protected_resource_boolean_invalid")
            for name in ("dependencies", "dependents"):
                if not isinstance(item[name], list) or not all(isinstance(x, str) and x for x in item[name]):
                    raise PolicyError("protected_resource_dependencies_invalid")
            rules.append(ResourceRule(
                resource_id=item["resource_id"],
                path=path.resolve(strict=False),
                match=item["match"],
                component=item["component"],
                description=item["description"],
                protection_class=item["protection_class"],
                allowed_scan_modes=tuple(item["allowed_scan_modes"]),
                content_hashing_allowed=item["content_hashing_allowed"],
                cleanup_may_be_proposed=item["cleanup_may_be_proposed"],
                expected_mount=item["expected_mount"],
                expected_service=item["expected_service"],
                dependencies=tuple(item["dependencies"]),
                dependents=tuple(item["dependents"]),
                source=item["source"],
                confidence=item["confidence"],
            ))
        return rules

    @staticmethod
    def _read_registry(path: Path) -> tuple[str, dict[str, dict[str, Any]], str]:
        try:
            raw_bytes = path.read_bytes()
            raw = json.loads(raw_bytes.decode("utf-8"))
        except FileNotFoundError:
            return "missing", {}, "missing"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return "malformed", {}, "malformed"
        if not isinstance(raw, dict) or raw.get("schema") != "aag-component-registry-v1" or not isinstance(raw.get("components"), list):
            return "malformed", {}, "malformed"
        components: dict[str, dict[str, Any]] = {}
        for item in raw["components"]:
            if not isinstance(item, dict) or not isinstance(item.get("identity"), str):
                return "malformed", {}, "malformed"
            components[item["identity"]] = item
        return "loaded", components, "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    @staticmethod
    def canonicalize(path: str | Path) -> Path:
        value = str(path)
        if not value or "\x00" in value:
            raise PolicyError("invalid_path")
        candidate = Path(value)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise PolicyError("path_must_be_absolute_without_traversal")
        try:
            return candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise PolicyError("path_canonicalization_failed") from exc

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def validate_scope(self, path: str | Path) -> Path:
        canonical = self.canonicalize(path)
        roots = tuple(root.resolve(strict=False) for root in self.config.allowed_scope_roots)
        if not any(self._under(canonical, root) for root in roots):
            raise PolicyError("path_outside_allowed_scope")
        return canonical

    def is_excluded(self, path: str | Path) -> bool:
        canonical = self.canonicalize(path)
        return any(self._under(canonical, root.resolve(strict=False)) for root in self.config.exclusions)

    def _rule_for(self, canonical: Path) -> ResourceRule | None:
        for rule in self.rules:
            if canonical == rule.path or (rule.match == "subtree" and rule.path in canonical.parents):
                return rule
        return None

    def classify(self, path: str | Path) -> ProtectionDecision:
        canonical = self.canonicalize(path)
        rule = self._rule_for(canonical)
        if rule is None:
            return ProtectionDecision(
                canonical_path=str(canonical),
                resource_id="unknown-resource",
                component=None,
                protection_class=ProtectionClass.UNKNOWN,
                allowed_scan_modes=("summary", "metadata"),
                content_hashing_allowed=False,
                cleanup_may_be_proposed=False,
                cleanup_classification=RecommendationClass.REVIEW_REQUIRED,
                dependency_status="unknown",
                dependencies=(),
                dependents=(),
                provenance=("default_fail_closed",),
                confidence="unknown",
                last_verified_at=utc_now(),
            )
        registry = self.registry_components.get(rule.component)
        registry_known = registry is not None and self.registry_status == "loaded"
        dependencies = tuple(dict.fromkeys((*rule.dependencies, *((registry or {}).get("dependencies") or []))))
        dependents = tuple(dict.fromkeys((*rule.dependents, *((registry or {}).get("dependents") or []))))
        dependency_known = registry_known or bool(dependencies or dependents) or not rule.component.startswith("unregistered_")
        if rule.protection_class in {ProtectionClass.CRITICAL, ProtectionClass.PROTECTED}:
            cleanup = RecommendationClass.PROTECTED
        elif (
            rule.cleanup_may_be_proposed
            and rule.protection_class in {ProtectionClass.GENERATED_OUTPUT, ProtectionClass.CACHE_CANDIDATE}
            and self.registry_status == "loaded"
            and dependency_known
        ):
            cleanup = RecommendationClass.LOW_RISK_CANDIDATE
        else:
            cleanup = RecommendationClass.REVIEW_REQUIRED
        return ProtectionDecision(
            canonical_path=str(canonical),
            resource_id=rule.resource_id,
            component=rule.component,
            protection_class=rule.protection_class,
            allowed_scan_modes=rule.allowed_scan_modes,
            content_hashing_allowed=rule.content_hashing_allowed,
            cleanup_may_be_proposed=rule.cleanup_may_be_proposed,
            cleanup_classification=cleanup,
            dependency_status="known" if dependency_known else "unknown",
            dependencies=dependencies,
            dependents=dependents,
            provenance=(rule.source, f"external_registry:{self.registry_status}"),
            confidence=rule.confidence,
            last_verified_at=utc_now(),
        )

    def dependency_graph(self) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []
        for identity, item in sorted(self.registry_components.items()):
            nodes[identity] = {
                "identity": identity,
                "name": item.get("name"),
                "risk": item.get("mutation_risk"),
                "confidence": item.get("confidence", "unknown"),
                "provenance": "external_registry",
            }
            for dependency in item.get("dependencies") or []:
                edges.append({"from": identity, "to": dependency, "relationship": "depends_on", "provenance": "external_registry", "confidence": item.get("confidence", "unknown")})
        for rule in self.rules:
            nodes.setdefault(rule.component, {"identity": rule.component, "name": rule.component, "risk": "unknown", "confidence": rule.confidence, "provenance": rule.source})
            for dependency in rule.dependencies:
                edges.append({"from": rule.component, "to": dependency, "relationship": "depends_on", "provenance": rule.source, "confidence": rule.confidence})
        unique = {(edge["from"], edge["to"], edge["relationship"], edge["provenance"]): edge for edge in edges}
        return {
            "schema": "aag-maintenance-dependency-graph-v1",
            "registry_status": self.registry_status,
            "nodes": [nodes[key] for key in sorted(nodes)],
            "edges": [unique[key] for key in sorted(unique)],
            "complete": False,
            "limitations": ["partial_graph", "absence_of_known_edge_does_not_prove_no_dependency"],
        }
