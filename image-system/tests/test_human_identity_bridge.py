import importlib.util
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["AAG_HUMAN_IDENTITY_RUNTIME"] = str(ROOT / "human-identity")

spec = importlib.util.spec_from_file_location(
    "aag_human_identity_bridge",
    ROOT / "human-identity/bin/process_inbox.py",
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class HumanIdentityBridgeTest(unittest.TestCase):
    def message(self):
        config = json.loads((ROOT / "human-identity/config/PRODUCTION-CONFIG.json").read_text())
        return {
            "schema_version": "aag.human-identity.bridge-request.v2",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "parent_job_id": "aag-22222222-2222-4222-8222-222222222222",
            "child_job_id": "aag-33333333-3333-4333-8333-333333333333",
            "reference_kind": "historical_validation_fixture",
            "fixture_id": "authorized-adult-01",
            "identity_domain": "adult",
            "prompt": config["prompts"]["adult"],
            "reference_sha256": "a" * 64,
            "original_sha256": "8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61",
            "reference_width": 896,
            "reference_height": 1152,
            "source_index": 1,
            "seed": 7,
            "width": 896,
            "height": 1152,
            "contract_id": "structured-close-b",
            "contract_b_sha256": bridge.CONTRACT_SHA,
            "release": bridge.RELEASE,
            "candidate_release": "0.9.0-preview.3-candidate.4-contract-b-confirmation",
            "route": "pulid-v1.1-juggernaut-xl-v9-single-original-structured-composition",
            "lease_token": "44444444-4444-4444-8444-444444444444",
            "caller": {"workspace_id": "w", "thread_id": "t", "user_id": "u", "invocation_id": "i"},
            "submitted_at": "2026-08-29T00:00:00Z",
        }

    def test_exact_contract_message_is_accepted(self):
        config = bridge.load_config()
        reference = bridge.validate_message(self.message(), config)
        self.assertEqual(reference["kind"], "historical_validation_fixture")
        self.assertEqual(reference["fixture"]["domain"], "adult")

        baby = self.message()
        baby.update(
            fixture_id="authorized-baby-01",
            identity_domain="baby",
            prompt=config["prompts"]["baby"],
            original_sha256="93665635711952c6a5da892bea90cc892b7c0a4a6748416e13a69ffd124eced6",
        )
        baby_reference = bridge.validate_message(baby, config)
        self.assertEqual(baby_reference["kind"], "historical_validation_fixture")
        self.assertEqual(baby_reference["fixture"]["domain"], "baby")

    def test_trusted_dynamic_reference_with_new_hash_is_accepted(self):
        config = bridge.load_config()
        value = self.message()
        value.update(reference_kind="trusted_runtime_reference", fixture_id=None, original_sha256="9" * 64)
        reference = bridge.validate_message(value, config)
        self.assertEqual(reference, {"kind": "trusted_runtime_reference", "fixture": None})

    def test_prompt_recipe_fixture_and_unknown_field_fail_closed(self):
        config = bridge.load_config()
        for mutate in (
            lambda value: value.update(prompt="changed"),
            lambda value: value.update(width=1024),
            lambda value: value.update(fixture_id="unknown"),
            lambda value: value.update(reference_kind="filesystem_path", reference_path="/tmp/person.png"),
            lambda value: value.update(extra="unexpected"),
        ):
            value = self.message()
            mutate(value)
            with self.assertRaises(bridge.BridgeFailure):
                bridge.validate_message(value, config)

    def test_secure_json_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("{}")
            linked = root / "linked.json"
            linked.symlink_to(outside)
            with self.assertRaises(OSError):
                bridge.secure_json(linked)

    def test_staged_reference_owner_workspace_thread_and_user_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            prior = bridge.STATE
            try:
                bridge.STATE = Path(directory)
                refs = bridge.STATE / "references"
                refs.mkdir()
                value = self.message()
                payload = b"trusted-current-attachment"
                digest = hashlib.sha256(payload).hexdigest()
                value.update(reference_sha256=digest, original_sha256="9" * 64, reference_width=20, reference_height=10)
                (refs / f"{value['request_id']}.png").write_bytes(payload)
                provenance = {
                    "schema_version": "aag.human-identity.staged-reference-provenance.v1",
                    "request_id": value["request_id"],
                    "caller": value["caller"],
                    "source": {"kind": "current_attachment", "index": 1, "original_sha256": "9" * 64, "normalized_sha256": digest, "width": 20, "height": 10, "format": "png"},
                }
                (refs / f"{value['request_id']}.provenance.json").write_text(json.dumps(provenance))
                self.assertEqual(bridge.verify_staged_reference(value), refs / f"{value['request_id']}.png")
                for field in ("workspace_id", "thread_id", "user_id"):
                    changed = dict(value)
                    changed["caller"] = {**value["caller"], field: "wrong"}
                    with self.assertRaisesRegex(bridge.BridgeFailure, "different trusted invocation scope"):
                        bridge.verify_staged_reference(changed)
            finally:
                bridge.STATE = prior

    def test_error_response_never_contains_private_material(self):
        response = bridge.error_response(self.message()["request_id"], "REFERENCE_NO_FACE", "Reference rejected")
        self.assertEqual(response["status"], "FAIL")
        self.assertNotIn("lease_token", response)
        self.assertNotIn("reference_path", response)
        self.assertNotIn("embedding", response)


if __name__ == "__main__":
    unittest.main()
