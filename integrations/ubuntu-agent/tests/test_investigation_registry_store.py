from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aag_agent.investigation.models import InvestigationValidationError
from aag_agent.investigation.registry import PlaybookRegistry
from aag_agent.investigation.store import InvestigationStore, InvestigationStoreError

ROOT = Path(__file__).parents[1]


class PlaybookRegistryTests(unittest.TestCase):
    def raw(self):
        return json.loads((ROOT / "config/diagnostic-playbooks-v1.json").read_text())

    def load(self, directory, raw):
        path = Path(directory) / "registry.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return PlaybookRegistry(path)

    def test_three_fixed_playbooks_load(self):
        registry = PlaybookRegistry()
        self.assertEqual(len(registry.list()), 3)
        self.assertEqual(registry.get("storage.root_pressure_investigation").data["diagnostic_steps"][0]["inputs"], {"path": "/"})

    def test_unknown_playbook_and_version_fail_closed(self):
        registry = PlaybookRegistry()
        for key, version in (("system.any", 1), ("system.performance_investigation", 2)):
            with self.subTest(key=key), self.assertRaisesRegex(InvestigationValidationError, "allowlisted"):
                registry.get(key, version)

    def test_unknown_top_and_playbook_fields_rejected(self):
        for location in ("top", "playbook"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as directory:
                raw = self.raw()
                (raw if location == "top" else raw["playbooks"][0])["command"] = "sudo true"
                with self.assertRaises(InvestigationValidationError):
                    self.load(directory, raw)

    def test_arbitrary_path_and_profile_rejected(self):
        for field, value in (("profile", "shell"), ("inputs", {"path": "/etc"})):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                raw = self.raw()
                raw["playbooks"][2]["diagnostic_steps"][0][field] = value
                with self.assertRaises(InvestigationValidationError):
                    self.load(directory, raw)

    def test_bridge_target_and_handoff_are_exact(self):
        for mutation in ("target", "operation", "service"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                raw = self.raw()
                item = raw["playbooks"][0]
                if mutation == "target": item["target_identity"] = "evil.service"
                if mutation == "operation": item["remediation_handoff"]["operation_id"] = "service.restart.any"
                if mutation == "service": item["diagnostic_steps"][0]["inputs"]["service"] = "evil.service"
                with self.assertRaises(InvestigationValidationError):
                    self.load(directory, raw)

    def test_duplicate_and_too_many_steps_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = self.raw(); raw["playbooks"].append(copy.deepcopy(raw["playbooks"][0]))
            with self.assertRaises(InvestigationValidationError): self.load(directory, raw)
        with tempfile.TemporaryDirectory() as directory:
            raw = self.raw(); raw["playbooks"][1]["diagnostic_steps"] *= 3
            with self.assertRaises(InvestigationValidationError): self.load(directory, raw)


class InvestigationStoreTests(unittest.TestCase):
    def test_migration_idempotent_permissions_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InvestigationStore(Path(directory) / "db.sqlite3")
            self.assertTrue(store.migrate()["created"])
            self.assertFalse(store.migrate()["created"])
            result = store.integrity()
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["database_mode"], "0o600")

    def test_migration_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InvestigationStore(Path(directory) / "db.sqlite3")
            with self.assertRaisesRegex(InvestigationStoreError, "migration_failed"):
                store.migrate(fault_sql="INVALID SQL;")
            connection = sqlite3.connect(store.path)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertNotIn("investigations", {row[0] for row in connection.execute("SELECT name FROM sqlite_master")})
            connection.close()

    def test_symlink_database_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"; target.touch()
            link = Path(directory) / "link"; link.symlink_to(target)
            with self.assertRaisesRegex(InvestigationStoreError, "symlink"):
                InvestigationStore(link)

    def test_foreign_keys_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InvestigationStore(Path(directory) / "db.sqlite3"); store.migrate()
            with self.assertRaises(sqlite3.IntegrityError), store.transaction() as connection:
                connection.execute("INSERT INTO investigation_steps(step_record_id,investigation_id,ordinal,step_id,collector,status) VALUES ('x','missing',1,'s','c','PENDING')")

    def test_event_chain_tamper_detected(self):
        from tests.investigation_helpers import InvestigationHarness
        with tempfile.TemporaryDirectory() as directory:
            harness = InvestigationHarness(Path(directory)); result = harness.run()
            with harness.store.transaction() as connection:
                connection.execute("UPDATE investigation_events SET details_json='{}' WHERE investigation_id=? AND sequence=1", (result["investigation_id"],))
            self.assertEqual(harness.store.integrity()["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
