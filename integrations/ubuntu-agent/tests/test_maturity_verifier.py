from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from aag_agent.maturity import MaturityVerifier, verify_manifest


class FakeBridge:
    def observe(self):
        return {
            "classification": "HEALTHY", "main_pid": "777",
            "health_ready": True, "provenance": {"read_only": True},
        }


class MaturityVerifierTests(unittest.TestCase):
    def test_manifest_verifier_passes_and_detects_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "a.txt"; target.write_text("good")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            manifest = root / "manifest"; manifest.write_text(f"{digest}  a.txt\n")
            self.assertEqual(verify_manifest(manifest, root=root)["status"], "PASS")
            target.write_text("bad")
            self.assertEqual(verify_manifest(manifest, root=root)["status"], "FAIL")

    def test_manifest_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root / "manifest"
            manifest.write_text(f"{'0' * 64}  ../outside\n")
            result = verify_manifest(manifest, root=root)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["errors"][0]["code"], "manifest_path_escape")

    def test_complete_read_only_verification_passes_with_boundaries(self):
        result = MaturityVerifier(bridge_provider=FakeBridge()).run(live=False)
        self.assertEqual(result["status"], "PASS_WITH_EXPLICIT_BOUNDARIES")
        self.assertFalse(result["eligible_for_arbitrary_repair"])
        self.assertFalse(result["mutated"])
        self.assertEqual(result["execution_authority"], "NONE")

    def test_live_check_uses_exact_provider_and_reports_pid(self):
        result = MaturityVerifier(bridge_provider=FakeBridge()).run(live=True)
        self.assertEqual(result["checks"]["live_bridge"]["status"], "PASS")
        self.assertEqual(result["checks"]["live_bridge"]["main_pid"], "777")
        self.assertFalse(result["checks"]["live_bridge"]["mutated"])

    def test_installed_skills_match_canonical_files(self):
        result = MaturityVerifier(bridge_provider=FakeBridge()).run()
        self.assertEqual(result["checks"]["installed_skills"]["status"], "PASS")
        self.assertEqual(len(result["checks"]["installed_skills"]["skills"]), 4)
        orchestration = result["checks"]["installed_skills"]["skills"][-1]
        self.assertIn(orchestration["status"], {"PASS", "STAGED_NOT_LIVE"})

    def test_authority_is_zero_and_boundaries_are_explicit(self):
        result = MaturityVerifier(bridge_provider=FakeBridge()).run()
        authority = result["checks"]["authority"]
        self.assertEqual(authority["status"], "PASS")
        self.assertFalse(authority["model_execution_authority"])
        ids = {item["id"] for item in result["open_boundaries"]}
        self.assertTrue({"stage17_live_attempt", "arbitrary_repair", "external_audit_anchor"}.issubset(ids))
        if "LIVE_VERIFIED" in result["maturities"]["governed_orchestration"]:
            self.assertNotIn("governed_orchestration_live_integration", ids)
        else:
            self.assertIn("governed_orchestration_live_integration", ids)

    def test_no_database_or_manifest_path_cli_surface(self):
        text = Path("tools/maturity_check.py").read_text()
        self.assertNotIn("--database", text)
        self.assertNotIn("--manifest", text)
        self.assertNotIn("--root", text)
        self.assertNotIn("subprocess", text)


if __name__ == "__main__":
    unittest.main()
