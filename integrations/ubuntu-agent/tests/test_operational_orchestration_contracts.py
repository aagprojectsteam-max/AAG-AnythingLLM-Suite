from __future__ import annotations

import json
import unittest

from aag_agent.orchestration.contracts import (
    ContractError,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    bound_response,
    error_response,
    validate_request,
    validate_response,
)


def response():
    return {
        "schema": RESPONSE_SCHEMA,
        "request_id": "orchestration-request:" + "a" * 24,
        "intent": {"schema": "aag-orchestration-intent-v2", "intent": "CONTEXT_QUERY"},
        "status": "CONTEXT_ASSEMBLED",
        "unknowns": [],
        "commands": [],
        "approval_status": "NOT_REQUESTED",
        "execution_status": "not_executed",
        "execution_authority": "NONE",
        "host_resource_mutated": False,
        "read_only_host_access": True,
        "security_notice": {"approval_and_execution_are_not_exposed": True},
    }


class OperationalOrchestrationContractTests(unittest.TestCase):
    def test_strict_request_and_optional_continuation(self):
        task_id = "task:" + "a" * 24
        valid = validate_request({"schema": REQUEST_SCHEMA, "request": "המשך את המשימה", "continuation": {"task_id": task_id}})
        self.assertEqual(valid.task_id, task_id)
        self.assertEqual(valid.request, "המשך את המשימה")

    def test_unknown_fields_and_unknown_nested_fields_are_rejected(self):
        cases = (
            {"schema": REQUEST_SCHEMA, "request": "status", "path": "/etc"},
            {"schema": REQUEST_SCHEMA, "request": "status", "continuation": {"task_id": "task:" + "a" * 24, "target": "evil"}},
            {"schema": REQUEST_SCHEMA, "request": "status", "continuation": {}},
            {"schema": "wrong", "request": "status"},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ContractError):
                validate_request(payload)

    def test_size_nul_and_control_limits(self):
        for request in ("x" * 4097, "bad\0input", "bad\x01input", ""):
            with self.subTest(size=len(request)), self.assertRaises(ContractError):
                validate_request({"schema": REQUEST_SCHEMA, "request": request})

    def test_request_contract_has_no_infrastructure_surface(self):
        payload = {"schema": REQUEST_SCHEMA, "request": "service=evil path=/etc operation_id=evil.restart"}
        checked = validate_request(payload)
        self.assertIsNone(checked.task_id)
        self.assertEqual(set(payload), {"schema", "request"})
        serialized = json.dumps(payload)
        for field in ('"command":', '"shell":', '"argv":', '"sql":', '"approval":', '"token":'):
            self.assertNotIn(field, serialized)

    def test_response_authority_invariants(self):
        self.assertEqual(validate_response(response())["execution_authority"], "NONE")
        mutations = (
            {"commands": ["id"]}, {"approval_status": "APPROVED"},
            {"execution_status": "executed"}, {"execution_authority": "MUTATE"},
            {"host_resource_mutated": True}, {"read_only_host_access": False},
        )
        for change in mutations:
            item = {**response(), **change}
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_response(item)

    def test_response_ceiling_is_enforced_without_byte_slicing(self):
        item = response()
        item["unknowns"] = ["x" * 513000]
        with self.assertRaisesRegex(ContractError, "response_too_large"):
            validate_response(item)
        bounded = validate_response(bound_response(item))
        self.assertEqual(bounded["status"], "INDETERMINATE")
        self.assertEqual(bounded["data_completeness"]["status"], "TRUNCATED_BY_POLICY")
        self.assertEqual(bounded["evidence_ids"], [])
        self.assertEqual(bounded["source_catalog"], [])

    def test_errors_also_preserve_zero_authority(self):
        item = error_response("collector_unavailable")
        self.assertEqual(item["commands"], [])
        self.assertEqual(item["approval_status"], "NOT_REQUESTED")
        self.assertEqual(item["execution_authority"], "NONE")
        self.assertFalse(item["host_resource_mutated"])


if __name__ == "__main__":
    unittest.main()
