import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from aag_agent.context_memory.config import load_config
from aag_agent.context_memory.memory import MemoryPipeline
from aag_agent.context_memory.models import canonical_json, stable_id, utc_now
from aag_agent.context_memory.store import ContextMemoryStore, ContextMemoryStoreError, SCHEMA_VERSION
from tests.context_memory_helpers import isolated


class ContextMemoryStoreTests(unittest.TestCase):
    def test_schema_creation_integrity_foreign_keys_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            result = store.integrity()
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["schema_version"], SCHEMA_VERSION)
            self.assertEqual(result["foreign_key_violations"], [])
            self.assertEqual(os.stat(store.path).st_mode & 0o777, 0o600)
            with store.read() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for required in (
                "sources", "source_artifacts", "documents", "document_chunks",
                "entities", "relationships", "claims", "claim_versions",
                "observations", "incidents", "tasks", "memory_candidates",
                "canonical_promotion_candidates", "conflicts", "ingestion_runs",
                "retrieval_runs", "context_packages", "redaction_records",
                "remediation_plans",
            ):
                self.assertIn(required, tables)

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            _, store, _ = isolated(Path(directory))
            before = store.integrity()["migration"]
            store.migrate()
            after = store.integrity()["migration"]
            self.assertEqual(before, after)
            with store.read() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1)

    def test_migration_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed.sqlite3"
            store = ContextMemoryStore(path)
            with self.assertRaisesRegex(ContextMemoryStoreError, "migration_failed"):
                store.migrate(fault_sql="INSERT INTO table_that_does_not_exist VALUES (1);")
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("schema_migrations", tables)
            finally:
                connection.close()

    def test_foreign_key_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            _, store, _ = isolated(Path(directory))
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute(
                        """INSERT INTO source_artifacts
                           (artifact_id,source_id,uri,original_sha256,normalized_sha256,
                            parser_version,byte_size,modified_at,ingested_at,temporal_scope,
                            lifecycle_state,verification_level,status)
                           VALUES ('a','missing','x',?,?, 'v',0,NULL,?,'HISTORICAL',
                                   'ACTIVE','DOCUMENTED','ACTIVE')""",
                        ("0" * 64, "0" * 64, utc_now()),
                    )

    def _seed_memory(self, store):
        with store.transaction() as connection:
            now = utc_now()
            connection.execute(
                "INSERT INTO sources VALUES ('evidence','verified_test','Evidence',90,1,?)",
                (now,),
            )
            connection.execute(
                """INSERT INTO source_artifacts VALUES
                   ('artifact:test','evidence','test://evidence',?,?, 'test-v1',1,NULL,?,
                    'CURRENT','ACTIVE','TEST_VERIFIED','ACTIVE')""",
                ("1" * 64, "1" * 64, now),
            )
            connection.execute(
                "INSERT INTO entities VALUES ('entity:test','component','Test component','ACTIVE',?)",
                (now,),
            )

    def test_llm_inference_cannot_self_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            config = replace(config, canonical_fact_keys=frozenset({"test.fact"}))
            memory = MemoryPipeline(store, config)
            candidate = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="inference",
                origin="LLM_OUTPUT", evidence_ids=["artifact:test"],
            )
            result = memory.promote(candidate["candidate_id"])
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["decision_reason"], "origin_cannot_self_promote")
            self.assertEqual(memory.get(candidate["candidate_id"])["epistemic_state"], "INFERRED")

    def test_user_report_remains_user_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            memory = MemoryPipeline(store, replace(config, canonical_fact_keys=frozenset({"test.fact"})))
            candidate = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="reported",
                origin="USER_STATEMENT", evidence_ids=["artifact:test"],
            )
            self.assertEqual(candidate["epistemic_state"], "USER_REPORTED")
            self.assertEqual(candidate["verification_level"], "USER_CONFIRMED")
            self.assertEqual(memory.promote(candidate["candidate_id"])["status"], "REJECTED")

    def test_tool_verified_candidate_promotes_with_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            memory = MemoryPipeline(store, replace(config, canonical_fact_keys=frozenset({"test.fact"})))
            candidate = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value={"state": "ok"},
                origin="TOOL_RESULT", evidence_ids=["artifact:test"],
            )
            result = memory.promote(candidate["candidate_id"])
            self.assertEqual(result["status"], "PROMOTED")
            with store.read() as connection:
                fact = connection.execute("SELECT * FROM current_canonical_facts").fetchone()
                self.assertEqual(json.loads(fact["value_json"]), {"state": "ok"})
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM evidence_links WHERE subject_type='claim_version' AND subject_id=?",
                        (result["promoted_version_id"],),
                    ).fetchone()[0],
                    1,
                )

    def test_conflicting_promotion_does_not_overwrite_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            memory = MemoryPipeline(store, replace(config, canonical_fact_keys=frozenset({"test.fact"})))
            first = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="A",
                origin="TOOL_RESULT", evidence_ids=["artifact:test"],
            )
            memory.promote(first["candidate_id"])
            second = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="B",
                origin="TOOL_RESULT", evidence_ids=["artifact:test"],
            )
            result = memory.promote(second["candidate_id"])
            self.assertEqual(result["status"], "CONFLICTED")
            with store.read() as connection:
                current = connection.execute("SELECT value_json FROM current_canonical_facts").fetchone()[0]
                self.assertEqual(json.loads(current), "A")
                self.assertEqual(connection.execute("SELECT count(*) FROM conflicts WHERE status='OPEN'").fetchone()[0], 1)

    def test_governed_conflict_reconciliation_versions_and_supersedes(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            memory = MemoryPipeline(store, replace(config, canonical_fact_keys=frozenset({"test.fact"})))
            first = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="A",
                origin="TOOL_RESULT", evidence_ids=["artifact:test"],
            )
            first_result = memory.promote(first["candidate_id"])
            second = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="B",
                origin="TOOL_RESULT", evidence_ids=["artifact:test"],
            )
            conflict = memory.promote(second["candidate_id"])
            with self.assertRaisesRegex(ValueError, "operator_governance_required"):
                memory.reconcile_conflict(
                    conflict["conflict_id"], decision="PROMOTE_CANDIDATE",
                    evidence_ids=["artifact:test"], operator_governed=False,
                )
            result = memory.reconcile_conflict(
                conflict["conflict_id"], decision="PROMOTE_CANDIDATE",
                evidence_ids=["artifact:test"], operator_governed=True,
            )
            self.assertEqual(result["status"], "RESOLVED")
            with store.read() as connection:
                versions = connection.execute(
                    "SELECT * FROM claim_versions ORDER BY version_number"
                ).fetchall()
                self.assertEqual(len(versions), 2)
                self.assertEqual(versions[0]["lifecycle_state"], "SUPERSEDED")
                self.assertEqual(versions[0]["canonical"], 0)
                self.assertEqual(versions[1]["supersedes_version_id"], first_result["promoted_version_id"])
                self.assertEqual(json.loads(versions[1]["value_json"]), "B")
                self.assertEqual(versions[1]["canonical"], 1)
                self.assertEqual(
                    connection.execute("SELECT status FROM conflicts").fetchone()[0],
                    "RESOLVED",
                )

    def test_live_drift_creates_conflict_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            memory = MemoryPipeline(store, replace(config, canonical_fact_keys=frozenset({"test.fact"})))
            candidate = memory.submit(
                entity_id="entity:test", fact_key="test.fact", value="canonical-A",
                origin="TOOL_RESULT", evidence_ids=["artifact:test"],
            )
            memory.promote(candidate["candidate_id"])
            observation_id = store.add_observation(
                entity_id="entity:test", fact_key="test.fact", value="observed-B",
                observed_at=utc_now(), expires_at=None,
                freshness_class="CONFIGURATION", source_id="evidence",
                artifact_id="artifact:test", read_only=True, mutated=False,
            )
            conflicts = store.list_conflicts()
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["observation_id"], observation_id)
            with store.read() as connection:
                current = connection.execute(
                    "SELECT value_json FROM current_canonical_facts"
                ).fetchone()[0]
                self.assertEqual(json.loads(current), "canonical-A")
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM evidence_links
                           WHERE subject_type='observation' AND subject_id=?""",
                        (observation_id,),
                    ).fetchone()[0],
                    1,
                )

    def test_nonexistent_source_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config, store, _ = isolated(Path(directory))
            self._seed_memory(store)
            memory = MemoryPipeline(store, config)
            with self.assertRaisesRegex(ValueError, "evidence_missing"):
                memory.submit(
                    entity_id="entity:test", fact_key="test.fact", value="x",
                    origin="TOOL_RESULT", evidence_ids=["artifact:invented"],
                )


if __name__ == "__main__":
    unittest.main()
