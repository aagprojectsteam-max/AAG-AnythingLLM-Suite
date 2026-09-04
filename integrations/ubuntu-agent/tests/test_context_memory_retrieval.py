import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aag_agent.context_memory.retrieval import freshness_state
from tests.context_memory_helpers import seeded


class ContextMemoryRetrievalTests(unittest.TestCase):
    def service(self, directory):
        return seeded(Path(directory))

    def test_current_maintenance_top_result_and_stage14_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            result = service.retriever.search("מה המצב הנוכחי של Maintenance Intelligence V1?")
            self.assertEqual(result["results"][0]["fact_key"], "maintenance.maturity")
            self.assertEqual(result["results"][0]["content"], "MAINTENANCE_INTELLIGENCE_V1_LIVE_VERIFIED")
            facts = {item.get("fact_key"): item.get("content") for item in result["results"]}
            self.assertEqual(facts["maintenance.skill_version"], "1.0.2")
            self.assertEqual(facts["maintenance.execution_authority"], "NONE")
            self.assertNotIn("stage14.failure", facts)
            self.assertFalse(any(item["lifecycle_state"] == "FAILED_ATTEMPT" for item in result["results"]))

    def test_historical_stage14_and_stage15_fix_are_retrieved(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            result = service.retriever.search(
                "מה נכשל ב-Stage 14 ואיך Stage 15 תיקן את זה?",
                include_historical=True,
            )
            facts = {item.get("fact_key") for item in result["results"]}
            self.assertIn("stage14.failure", facts)
            self.assertIn("stage15.remediation", facts)
            stage14 = next(item for item in result["results"] if item.get("fact_key") == "stage14.failure")
            self.assertEqual(stage14["temporal_scope"], "HISTORICAL")
            self.assertEqual(stage14["lifecycle_state"], "FAILED_ATTEMPT")
            self.assertTrue(stage14["source_ids"])

    def test_failed_attempt_is_returned_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            normal = service.retriever.search("qDslrDashboard Canon GUI")
            self.assertFalse(any(item.get("fact_key") == "incident.INC-0028" for item in normal["results"]))
            history = service.retriever.search(
                "איזה פתרון qDslrDashboard ניסינו בעבר ודחינו?",
                include_historical=True,
            )
            item = next(item for item in history["results"] if item.get("fact_key") == "incident.INC-0028")
            self.assertEqual(item["lifecycle_state"], "FAILED_ATTEMPT")
            self.assertEqual(item["selection_reason"], "failed_attempt_requested")

    def test_exact_path_service_hash_version_and_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            cases = {
                "/mnt/data/AI/Agents/AAG-Ubuntu-Agent": "agent.project_root",
                "aag-ubuntu-agent-bridge.service": "bridge.service_name",
                "44518dbc7133951f6423c5eb91812e9e4e02e88eaef2847ef166af0d64536866": None,
                "INC-0028": "incident.INC-0028",
                "version 1.0.2 Maintenance Intelligence": "maintenance.skill_version",
            }
            for query, fact in cases.items():
                result = service.retriever.search(query, include_historical=True)
                self.assertTrue(result["results"], query)
                if fact:
                    self.assertTrue(any(item.get("fact_key") == fact for item in result["results"]), query)
                if len(query) == 64:
                    self.assertEqual(result["results"][0]["selection_reason"], "exact_identifier_match")

    def test_hebrew_and_english_fts(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            hebrew = service.retriever.search("האם זה קרה לנו בעבר? כשל תוכנית ניקוי", include_historical=True)
            english = service.retriever.search("What failed in the cleanup plan grounding?", include_historical=True)
            for result in (hebrew, english):
                self.assertTrue(result["diagnostics"]["fts_query_used"])
                self.assertTrue(any(
                    "ground" in json.dumps(item["content"], ensure_ascii=False).casefold()
                    or item.get("fact_key") == "stage14.failure"
                    for item in result["results"]
                ))

    def test_relationship_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            result = service.retriever.search("AAG Ubuntu Agent Host Bridge")
            relationships = [item for item in result["results"] if item["kind"] == "relationship"]
            self.assertTrue(relationships)
            self.assertTrue(any(item["selection_reason"] == "relationship_expansion" for item in relationships))

    def test_ttl_fresh_stale_expired(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(
            freshness_state((now + timedelta(seconds=5)).isoformat(), now=now),
            "FRESH",
        )
        self.assertEqual(
            freshness_state((now - timedelta(seconds=60)).isoformat(), now=now),
            "STALE",
        )
        self.assertEqual(
            freshness_state((now - timedelta(seconds=600)).isoformat(), now=now),
            "EXPIRED",
        )
        self.assertEqual(freshness_state(None, now=now), "NOT_APPLICABLE")

    def test_context_separates_current_history_and_sources_are_real(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            package = service.assembler.assemble(
                "מה נכשל ב-Stage 14 ואיך Stage 15 תיקן את זה?",
                budget_tier="history",
                include_historical=True,
            )
            self.assertEqual(package["schema"], "aag-context-package-v1")
            self.assertTrue(package["failed_or_rejected_approaches"])
            self.assertFalse(any(
                item["lifecycle_state"] == "FAILED_ATTEMPT"
                for item in package["current_facts"]
            ))
            catalog = {item["artifact_id"] for item in package["source_catalog"]}
            for category in (
                "current_facts", "relevant_history", "verified_prior_fixes",
                "failed_or_rejected_approaches",
            ):
                for item in package[category]:
                    self.assertTrue(item["source_ids"])
                    self.assertTrue(set(item["source_ids"]) <= catalog)

    def test_context_budget_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            package = service.assembler.assemble(
                "Maintenance Intelligence Stage 14 Stage 15 Bridge AnythingLLM current history failure fix",
                budget_tier="exact",
                include_historical=True,
            )
            ids = [
                item["item_id"]
                for category in (
                    "current_facts", "relevant_history", "verified_prior_fixes",
                    "failed_or_rejected_approaches",
                )
                for item in package[category]
            ]
            self.assertEqual(len(ids), len(set(ids)))
            selected = sum(package["budget"]["selected_tokens_by_category"].values())
            self.assertLessEqual(selected, package["budget"]["hard_ceiling_tokens"])
            self.assertLessEqual(selected, package["budget"]["configured_budget_tokens"])
            self.assertEqual(package["budget"]["truncation_policy"], "whole_ranked_items_only")


if __name__ == "__main__":
    unittest.main()
