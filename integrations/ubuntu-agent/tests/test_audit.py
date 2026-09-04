import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aag_agent.audit import AuditError, append_event, checkpoint_path, record, verify_chain, verify_records


class AuditTests(unittest.TestCase):
    def chain(self):
        first = record("bridge.readiness_failure", "execution_started", {"target": "aag-ubuntu-agent-bridge.service"}, sequence=1, timestamp=1.0)
        second = record("bridge.readiness_failure", "execution_finished", {"mutated": True}, previous_hash=first["record_hash"], sequence=2, timestamp=2.0)
        return first, second

    def test_valid_chain_is_deterministically_verified(self):
        first, second = self.chain()
        result = verify_records([first, second])
        self.assertEqual(result, {"valid": True, "record_count": 2, "last_hash": second["record_hash"]})

    def test_append_event_builds_and_verifies_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            first = append_event(path, "bridge.readiness_failure", "execution_started", {}, timestamp=1.0)
            second = append_event(path, "bridge.readiness_failure", "execution_finished", {}, timestamp=2.0)
            self.assertEqual(second["previous_hash"], first["record_hash"])
            self.assertEqual(verify_chain(path)["record_count"], 2)

    def test_missing_record_detected_by_sequence_or_previous_hash(self):
        _, second = self.chain()
        with self.assertRaises(AuditError): verify_records([second])

    def test_altered_record_detected(self):
        first, second = self.chain(); altered = copy.deepcopy(first); altered["details"]["target"] = "evil.service"
        with self.assertRaisesRegex(AuditError, "record_hash_mismatch"): verify_records([altered, second])

    def test_reordered_records_detected(self):
        first, second = self.chain()
        with self.assertRaises(AuditError): verify_records([second, first])

    def test_malformed_record_and_json_detected(self):
        first, _ = self.chain(); malformed = dict(first); del malformed["event"]
        with self.assertRaisesRegex(AuditError, "malformed_record_fields"): verify_records([malformed])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"; path.write_text("{broken\n")
            with self.assertRaisesRegex(AuditError, "malformed_json"): verify_chain(path)

    def test_malformed_types_detected(self):
        first, _ = self.chain()
        for field, value, error in [
            ("timestamp", float("nan"), "invalid_timestamp"),
            ("contract_id", "", "invalid_contract_id"),
            ("event", None, "invalid_event"),
            ("details", [], "invalid_details"),
        ]:
            malformed = dict(first); malformed[field] = value
            with self.assertRaisesRegex(AuditError, error): verify_records([malformed])

    def test_corrupt_existing_chain_prevents_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            first = append_event(path, "bridge.readiness_failure", "execution_started", {}, timestamp=1.0)
            first["details"]["tampered"] = True
            path.write_text(json.dumps(first) + "\n")
            with self.assertRaisesRegex(AuditError, "record_hash_mismatch"):
                append_event(path, "bridge.readiness_failure", "execution_finished", {}, timestamp=2.0)

    def test_checkpoint_detects_deleted_tail_and_missing_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            append_event(path, "bridge.readiness_failure", "execution_started", {}, timestamp=1.0)
            append_event(path, "bridge.readiness_failure", "execution_finished", {}, timestamp=2.0)
            lines = path.read_text().splitlines()
            path.write_text(lines[0] + "\n")
            with self.assertRaisesRegex(AuditError, "checkpoint_chain_mismatch"):
                verify_chain(path)
            path.write_text("\n".join(lines) + "\n")
            checkpoint_path(path).unlink()
            with self.assertRaisesRegex(AuditError, "checkpoint_missing"):
                verify_chain(path)

    def test_checkpoint_tampering_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            append_event(path, "bridge.readiness_failure", "execution_started", {}, timestamp=1.0)
            checkpoint = checkpoint_path(path)
            item = json.loads(checkpoint.read_text()); item["record_count"] = 9
            checkpoint.write_text(json.dumps(item) + "\n")
            with self.assertRaisesRegex(AuditError, "checkpoint_chain_mismatch"):
                verify_chain(path)

    def test_concurrent_writers_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda number: append_event(path, "bridge.readiness_failure", "test", {"number": number}), range(8)))
            state = verify_chain(path)
            self.assertEqual(state["record_count"], 8)
            self.assertTrue(state["checkpoint"]["valid"])


if __name__ == "__main__": unittest.main()
