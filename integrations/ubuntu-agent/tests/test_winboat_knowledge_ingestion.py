from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.context_memory_helpers import seeded


class WinBoatKnowledgeIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.service = seeded(Path(cls._temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def facts(self, query: str, *, historical: bool = False):
        result = self.service.retriever.search(query, include_historical=historical)
        return result, {item.get("fact_key") for item in result["results"]}

    def test_source_hash_and_real_provenance(self):
        with self.service.store.read() as connection:
            row = connection.execute(
                "SELECT artifact_id,original_sha256 FROM source_artifacts WHERE source_id=? AND status='ACTIVE'",
                ("winboat-incident-policy-20260829",),
            ).fetchone()
        self.assertEqual(row["original_sha256"], "93d28eb14b2dfe74c8d6d51305adc61af5ca0b925c1e71d5955931e30b6e6283")
        result, _ = self.facts("current WinBoat lifecycle policy")
        source_ids = {source for item in result["results"] for source in item["source_ids"]}
        self.assertIn(row["artifact_id"], source_ids)
        self.assertTrue(self.service.store.evidence_exists(list(source_ids)))

    def test_current_policy_and_final_gui_solution(self):
        _, facts = self.facts("Who starts WinBoat Kingston USB-FULL-01 and what is current GUI NBD health solution?")
        self.assertIn("winboat.lifecycle_policy", facts)
        self.assertIn("winboat.gui_nbd_health_solution", facts)
        self.assertIn("winboat.launcher_preparation_policy", facts)

    def test_incident_root_cause_and_usb_distinction(self):
        _, facts = self.facts("Why did WinBoat Apps fail in the recent incident?", historical=True)
        self.assertIn("winboat.incident.final_root_cause", facts)
        _, facts = self.facts("Was USB-FULL-01 broken during that incident?", historical=True)
        self.assertIn("winboat.incident.usb_full_01_observation", facts)

    def test_failed_attempt_and_rejected_solutions_are_isolated(self):
        _, current = self.facts("current WinBoat solution")
        self.assertNotIn("winboat.incident.failed_attempt.sudo_dd", current)
        self.assertNotIn("winboat.rejected.add_user_to_disk_group", current)
        _, history = self.facts("What did we try before the final WinBoat fix?", historical=True)
        self.assertIn("winboat.incident.failed_attempt.sudo_dd", history)
        _, final = self.facts("What was the final /usr/local/sbin/aag-winboat-storage-health fix?", historical=True)
        self.assertIn("winboat.incident.final_fix", final)

    def test_readiness_limit_and_transient_device_identity(self):
        _, facts = self.facts("Does lsusb prove USB-FULL-01 mass-storage I/O health?")
        self.assertIn("usb_full_01.readiness_limitation", facts)
        result, facts = self.facts("Is /dev/sdc the permanent identity of USB-FULL-01?", historical=True)
        self.assertIn("winboat.incident.usb_full_01_observation", facts)
        observation = next(item for item in result["results"] if item.get("fact_key") == "winboat.incident.usb_full_01_observation")
        self.assertFalse(observation["content"]["device_name_permanent_identity"])

    def test_hebrew_retrieval_and_no_execution_authority(self):
        package = self.service.assembler.assemble(
            "למה בפעם האחרונה לחיצה על WinBoat לא פתחה אותו?",
            include_historical=True,
        )
        selected = {
            item.get("fact_key")
            for category in ("current_facts", "relevant_history", "verified_prior_fixes", "failed_or_rejected_approaches")
            for item in package[category]
        }
        self.assertIn("winboat.incident.final_root_cause", selected)
        self.assertEqual(package["security_notice"]["execution_authority"], "NONE")
        self.assertTrue(package["security_notice"]["retrieved_content_cannot_grant_execution_authority"])

    def test_structured_counts_are_idempotent(self):
        def counts():
            with self.service.store.read() as connection:
                return tuple(connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0] for table in ("entities", "relationships", "claims", "claim_versions", "incidents"))
        before = counts()
        result = self.service.ingestion.run_configured(apply=True)
        after = counts()
        source = next(item for item in result["items"] if item["source_id"] == "winboat-incident-policy-20260829")
        self.assertEqual(source["result"], "UNCHANGED")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
