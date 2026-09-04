import json
import tempfile
import unittest
from pathlib import Path

from aag_agent.context_memory.service import ContextMemoryService, ContextServiceError
from aag_agent.context_memory.tasks import TaskError
from tests.context_memory_helpers import seeded


def diagnostic_fixture(requests):
    profile = requests[0]["profile"]
    facts = {}
    if profile == "service":
        facts = {
            "systemd": {
                "state": "OBSERVED",
                "value": {
                    "Id": "aag-ubuntu-agent-bridge.service",
                    "ActiveState": "active",
                    "SubState": "running",
                    "MainPID": "424242",
                },
            }
        }
    elif profile == "performance":
        facts = {
            "memory": {"state": "OBSERVED", "value": {"available_percent": 61.0}},
            "load": {"state": "OBSERVED", "value": {"load_1m": 1.2}},
        }
    return {
        "schema": "aag-diagnostic-session-v1",
        "captured_at": 1787843000.0,
        "read_only": True,
        "mutated": False,
        "status": "OBSERVED",
        "bundles": [{
            "schema": "aag-diagnostic-bundle-v1",
            "profile": profile,
            "status": "OBSERVED",
            "read_only": True,
            "mutated": False,
            "facts": facts,
        }],
    }


class ContextMemorySecurityTests(unittest.TestCase):
    def service(self, directory):
        return seeded(Path(directory), diagnostic_runner=diagnostic_fixture)

    def test_request_schema_rejects_raw_sql_and_arbitrary_path(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            for payload in (
                {"operation": "context", "query": "x", "sql": "DROP TABLE claims"},
                {"operation": "context", "query": "x", "path": "/etc/shadow"},
                {"operation": "query_sql", "query": "SELECT * FROM claims"},
            ):
                with self.assertRaises(ContextServiceError):
                    service.dispatch(payload)

    def test_current_bridge_uses_typed_live_refresh_not_historical_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            response = service.dispatch({
                "operation": "current_bridge",
                "query": "current PID of aag-ubuntu-agent-bridge.service",
            })
            live = response["result"]["live_observations"][0]
            self.assertEqual(live["content"]["MainPID"], "424242")
            self.assertNotEqual(live["content"]["MainPID"], "2480850")
            self.assertEqual(live["freshness"], "FRESH")
            self.assertTrue(live["source_ids"])
            self.assertTrue(response["read_only"])
            self.assertFalse(response["mutated"])

    def test_current_performance_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            response = service.dispatch({"operation": "current_performance"})
            self.assertEqual(response["execution_authority"], "NONE")
            self.assertTrue(response["result"]["live_observations"])
            self.assertTrue(response["result"]["security_notice"]["retrieved_content_cannot_grant_execution_authority"])

    def test_prompt_injection_remains_delimited_untrusted_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            package = service.assembler.assemble(
                "sudo commands in prior evidence", include_historical=True
            )
            self.assertTrue(package["security_notice"]["instructions_inside_evidence_are_inert"])
            self.assertEqual(package["security_notice"]["execution_authority"], "NONE")
            for item in package["relevant_history"]:
                if "sudo" in str(item["content"]).casefold():
                    self.assertTrue(item["untrusted_evidence"])

    def test_secret_is_redacted_from_retrieval_log(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            secret = "sk-proj-TESTFIXTURE"
            service.retriever.search(f"api_key={secret} Maintenance")
            with service.store.read() as connection:
                row = connection.execute(
                    "SELECT query_redacted,diagnostics_json FROM retrieval_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            self.assertNotIn(secret, row["query_redacted"])
            self.assertNotIn(secret, row["diagnostics_json"])
            self.assertIn("[REDACTED]", row["query_redacted"])

    def test_task_start_update_resume_close_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            source = service.store.artifact_for_uri(
                "/mnt/data/AI/Agents/AAG-Ubuntu-Agent/release/status.json"
            )
            one = service.tasks.start("Investigate Bridge", entities=["entity:bridge"])
            two = service.tasks.start("Investigate storage", entities=["entity:registry:data-mount"])
            service.tasks.update(one["task_id"], {
                "decisions": ["Use only typed live service diagnostics"],
                "open_questions": ["Has the PID changed?"],
                "evidence_ids": [source],
            })
            resumed = service.tasks.resume(one["task_id"])
            untouched = service.tasks.show(two["task_id"])
            self.assertIn("Has the PID changed?", resumed["open_questions"])
            self.assertEqual(untouched["open_questions"], [])
            closed = service.tasks.close(one["task_id"], {"status": "verified"})
            self.assertEqual(closed["closure_status"], "COMPLETE")
            with self.assertRaisesRegex(TaskError, "not_resumable"):
                service.tasks.resume(one["task_id"])

    def test_task_rejects_invented_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            task = service.tasks.start("Evidence test")
            with self.assertRaisesRegex(TaskError, "evidence_missing"):
                service.tasks.update(task["task_id"], {"evidence_ids": ["artifact:invented"]})

    def test_remediation_plan_is_grounded_and_nonexecuting(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            response = service.dispatch({
                "operation": "remediation_plan",
                "query": "current performance is slow; prepare evidence-based repair proposal",
            })
            plan = response["result"]
            self.assertEqual(plan["schema"], "aag-remediation-plan-v1")
            self.assertTrue(plan["evidence_ids"])
            self.assertEqual(plan["execution_authority"], "NONE")
            self.assertEqual(plan["execution_status"], "not_executed")
            self.assertTrue(plan["zero_mutations"])
            serialized = json.dumps(plan).casefold()
            for forbidden in ("sudo", "shell", "docker system prune", "apt clean", "journalctl --vacuum", '"command"'):
                self.assertNotIn(forbidden, serialized)

    def test_remediation_without_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            result = service.remediation.plan("zzzz-no-such-evidence-zzzz")
            self.assertFalse(result["plan_available"])
            self.assertEqual(result["execution_authority"], "NONE")
            self.assertEqual(result["commands"], [])


if __name__ == "__main__":
    unittest.main()
