from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.remediation.models import RemediationValidationError
from aag_agent.remediation.registry import OperationRegistry, OperationRegistryError
from aag_agent.remediation.store import RemediationStore, RemediationStoreError

ROOT = Path(__file__).parents[1]


class OperationRegistryTests(unittest.TestCase):
    def raw(self):
        return json.loads((ROOT / "config/remediation-operations-v1.json").read_text())

    def registry(self, directory: str, raw):
        path = Path(directory) / "registry.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return OperationRegistry(path)

    def test_exact_accepted_operation(self):
        registry = OperationRegistry()
        operation = registry.get("bridge.restart.readiness_failure", 1, execution=True)
        self.assertEqual(operation.target, "aag-ubuntu-agent-bridge.service")
        self.assertEqual(operation.risk, "R2")
        self.assertEqual(operation.approval_class, "USER_CONFIRMATION")

    def test_unknown_operation_and_version_fail_closed(self):
        registry = OperationRegistry()
        for operation_id, version in (("bridge.restart.any", 1), ("bridge.restart.readiness_failure", 2)):
            with self.subTest(operation_id=operation_id, version=version):
                with self.assertRaisesRegex(OperationRegistryError, "unknown_operation_or_version"):
                    registry.get(operation_id, version, execution=True)

    def test_unknown_operation_fields_rejected(self):
        for field, value in (
            ("command", "systemctl restart evil.service"),
            ("sql", "DROP TABLE plans"),
            ("path", "/etc/shadow"),
            ("shell", True),
            ("systemctl_flags", ["--system"]),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                raw = self.raw()
                raw["operations"][0][field] = value
                with self.assertRaisesRegex(OperationRegistryError, "invalid_operation_fields"):
                    self.registry(directory, raw)

    def test_executor_argv_target_and_primitive_injection_rejected(self):
        mutations = (
            ("fixed_argv", ["/usr/bin/systemctl", "--user", "restart", "evil.service"]),
            ("fixed_executable", "/bin/bash"),
            ("primitive", "run_command"),
        )
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                raw = self.raw()
                raw["operations"][0]["executor"][field] = value
                with self.assertRaises(OperationRegistryError):
                    self.registry(directory, raw)

    def test_risk_controls_approval_class(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = self.raw()
            raw["operations"][0]["risk_class"] = "R3"
            with self.assertRaisesRegex(OperationRegistryError, "risk_approval_mismatch"):
                self.registry(directory, raw)

    def test_bridge_evidence_ttl_and_required_fields_cannot_drift(self):
        for mutation in ("ttl", "fields"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                raw = self.raw()
                if mutation == "ttl":
                    raw["operations"][0]["required_evidence"]["max_age_seconds"] = 300
                else:
                    raw["operations"][0]["required_evidence"]["required_fields"].remove("main_pid")
                with self.assertRaisesRegex(OperationRegistryError, "bridge_operation_contract_drift"):
                    self.registry(directory, raw)

    def test_nonaccepted_operation_cannot_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = self.raw()
            raw["operations"][0]["lifecycle_state"] = "TESTED"
            registry = self.registry(directory, raw)
            with self.assertRaisesRegex(OperationRegistryError, "operation_not_accepted"):
                registry.get("bridge.restart.readiness_failure", 1, execution=True)

    def test_registry_malformed_missing_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OperationRegistryError, "unreadable"):
                OperationRegistry(Path(directory) / "missing.json")
        with tempfile.TemporaryDirectory() as directory:
            raw = self.raw()
            raw["unexpected"] = True
            with self.assertRaisesRegex(OperationRegistryError, "invalid_operation_registry"):
                self.registry(directory, raw)
        with tempfile.TemporaryDirectory() as directory:
            raw = self.raw()
            raw["operations"].append(copy.deepcopy(raw["operations"][0]))
            with self.assertRaisesRegex(OperationRegistryError, "duplicate_operation_version"):
                self.registry(directory, raw)


class RemediationStoreTests(unittest.TestCase):
    def test_schema_creation_idempotency_permissions_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RemediationStore(Path(directory) / "store.sqlite3")
            first = store.migrate()
            second = store.migrate()
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(store.integrity()["status"], "PASS")
            self.assertEqual(store.integrity()["database_mode"], "0o600")

    def test_migration_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RemediationStore(Path(directory) / "store.sqlite3")
            with patch("aag_agent.remediation.store.SCHEMA_SQL", "CREATE TABLE ok(x); INVALID SQL;"):
                with self.assertRaisesRegex(RemediationStoreError, "migration_failed"):
                    store.migrate()
            connection = sqlite3.connect(store.path)
            try:
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("ok", names)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            finally:
                connection.close()

    def test_database_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.sqlite3"
            target.touch()
            link = root / "link.sqlite3"
            link.symlink_to(target)
            with self.assertRaisesRegex(RemediationStoreError, "symlink"):
                RemediationStore(link)

    def test_foreign_keys_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RemediationStore(Path(directory) / "store.sqlite3")
            store.migrate()
            with self.assertRaises(sqlite3.IntegrityError), store.transaction() as connection:
                connection.execute(
                    "INSERT INTO plan_evidence VALUES ('missing','artifact:x','a','TEST','role')"
                )

    def test_invalid_transition_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RemediationStore(Path(directory) / "store.sqlite3")
            store.migrate()
            with store.transaction() as connection:
                connection.execute(
                    """INSERT INTO plans
                       (plan_id,plan_hash,registry_hash,operation_id,operation_version,
                        target_identity,context_plan_id,task_id,incident_id,evidence_set_hash,
                        precondition_spec_hash,backup_policy_hash,plan_json,state,created_at,updated_at)
                       VALUES (?,?,?,?,1,?,?,NULL,?,?,?,?,?,'PROPOSED',?,?)""",
                    (
                        "governed-plan:a", "a" * 64, "b" * 64, "op", "target",
                        "context", "incident", "c" * 64, "d" * 64, "e" * 64,
                        "{}", "now", "now",
                    ),
                )
            with self.assertRaises(RemediationValidationError), store.transaction() as connection:
                store.transition(connection, "governed-plan:a", "EXECUTING", "bad", {})


if __name__ == "__main__":
    unittest.main()
