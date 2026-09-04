import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["AAG_HUMAN_IDENTITY_RUNTIME"] = str(ROOT / "human-identity-scene")
spec = importlib.util.spec_from_file_location(
    "aag_scene_identity_bridge", ROOT / "human-identity-scene/bin/process_inbox.py"
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class SceneIdentityBridgeTest(unittest.TestCase):
    def message(self):
        prompt = (
            "Realistic photograph. Show the same girl riding a camel in the desert. "
            "Exactly one primary young child with the same recognizable face and age as the authorized reference. "
            "Exactly one camel; its complete head, neck and torso inside frame; the child visibly riding with coherent saddle contact; desert visible; no other people or camels."
        )
        return {
            "schema_version": "aag.human-identity.scene.bridge-request.v1",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "parent_job_id": "aag-22222222-2222-4222-8222-222222222222",
            "child_job_id": "aag-33333333-3333-4333-8333-333333333333",
            "reference_kind": "trusted_runtime_reference",
            "fixture_id": None,
            "identity_domain": "baby",
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "reference_sha256": "a" * 64,
            "original_sha256": "9" * 64,
            "reference_width": 1024,
            "reference_height": 768,
            "source_index": 1,
            "seed": 7,
            "width": 1152,
            "height": 896,
            "contract_id": "structured-scene-c",
            "scene_contract_sha256": bridge.CONTRACT_SHA,
            "scene_profile": "scene-c-landscape",
            "release": bridge.RELEASE,
            "route": "pulid-v1.1-juggernaut-xl-v9-single-original-scene",
            "lease_token": "44444444-4444-4444-8444-444444444444",
            "caller": {"workspace_id": "w", "thread_id": "t", "user_id": "u", "invocation_id": "i"},
            "submitted_at": "2026-08-30T00:00:00Z",
        }

    def test_dynamic_new_hash_and_bounded_profiles_are_accepted(self):
        config = bridge.load_config()
        value = self.message()
        self.assertEqual(bridge.validate_message(value, config), {"kind": "trusted_runtime_reference", "fixture": None})
        portrait = dict(value, scene_profile="scene-c-portrait", width=896, height=1152)
        self.assertEqual(bridge.validate_message(portrait, config)["kind"], "trusted_runtime_reference")

    def test_historical_fixtures_remain_named_regression_inputs(self):
        config = bridge.load_config()
        value = self.message()
        value.update(reference_kind="historical_validation_fixture", fixture_id="authorized-adult-01", identity_domain="adult", original_sha256="8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61")
        self.assertEqual(bridge.validate_message(value, config)["fixture"]["domain"], "adult")

    def test_prompt_hash_profile_and_unknown_fields_fail_closed(self):
        config = bridge.load_config()
        mutations = (
            lambda value: value.update(prompt_sha256="0" * 64),
            lambda value: value.update(width=1024),
            lambda value: value.update(scene_profile="scene-c-square"),
            lambda value: value.update(reference_kind="filesystem_path", reference_path="/tmp/person.png"),
            lambda value: value.update(extra="unexpected"),
        )
        for mutate in mutations:
            value = self.message()
            mutate(value)
            with self.assertRaises(bridge.BridgeFailure):
                bridge.validate_message(value, config)

    def test_staged_reference_is_bound_to_workspace_thread_and_user(self):
        with tempfile.TemporaryDirectory() as directory:
            prior = bridge.STATE
            try:
                bridge.STATE = Path(directory)
                refs = bridge.STATE / "references"
                refs.mkdir()
                value = self.message()
                payload = b"trusted-current-scene-reference"
                digest = hashlib.sha256(payload).hexdigest()
                value.update(reference_sha256=digest, reference_width=20, reference_height=10)
                (refs / f"{value['request_id']}.png").write_bytes(payload)
                provenance = {
                    "schema_version": "aag.human-identity.staged-reference-provenance.v1",
                    "request_id": value["request_id"],
                    "caller": value["caller"],
                    "source": {"kind": "current_attachment", "index": 1, "original_sha256": value["original_sha256"], "normalized_sha256": digest, "width": 20, "height": 10, "format": "png"},
                }
                (refs / f"{value['request_id']}.provenance.json").write_text(json.dumps(provenance))
                self.assertEqual(bridge.verify_staged_reference(value), refs / f"{value['request_id']}.png")
                for field in ("workspace_id", "thread_id", "user_id"):
                    changed = dict(value, caller={**value["caller"], field: "wrong"})
                    with self.assertRaisesRegex(bridge.BridgeFailure, "different trusted invocation scope"):
                        bridge.verify_staged_reference(changed)
            finally:
                bridge.STATE = prior

    def test_contract_hash_and_recipe_are_frozen_separately(self):
        contract = ROOT / "human-identity-scene/config/SCENE-CONTRACT.json"
        self.assertEqual(hashlib.sha256(contract.read_bytes()).hexdigest(), bridge.CONTRACT_SHA)
        self.assertEqual(json.loads(contract.read_text())["recipe"]["id_scale"], 1.2)
        config = json.loads((ROOT / "human-identity-scene/config/PRODUCTION-CONFIG.json").read_text())
        self.assertEqual(config["authorized_reference_roots"], ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-human-identity-scene-state/references"])

    def test_error_response_is_bounded(self):
        response = bridge.error_response(self.message()["request_id"], "SCENE_IDENTITY_FRAMING_UNSUPPORTED", "Unsupported scene framing")
        self.assertEqual(response["status"], "FAIL")
        self.assertNotIn("lease_token", response)
        self.assertNotIn("reference_path", response)


if __name__ == "__main__":
    unittest.main()
