import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compatibility import compose_request, CompatibilityError
from server import Application


class GenerationProvenanceTests(unittest.TestCase):
    def boundary(self):
        obj = SimpleNamespace(auth_token="generation-provenance-fixture-key")
        obj.trusted_composer_intent = lambda payload: Application.trusted_composer_intent(obj, payload)
        return obj

    def test_signed_requested_values_language_and_safe_projection(self):
        app = self.boundary()
        request = "קערה אדומה על שולחן"
        raw, _ = compose_request({"mode": "advanced", "free_text": request, "quality": "auto", "ui_language": "he", "ui_version": "1.3", "expert_mode": False})
        signed = Application._sign_composer_message(app, raw)
        result = Application.generation_provenance(app, {"envelope": signed, "request": request})
        self.assertEqual(result["quality"], "auto")
        self.assertEqual(result["language"], "he")
        self.assertEqual(result["creative_direction"]["visual_family"], "auto")
        self.assertEqual(result["composer_version"], "1.3")
        for hidden in ("expert_prompt", "user_request_sha256", "signature", "knowledge_modules", "semantics", "original_user_request"):
            self.assertNotIn(hidden, result)

    def test_signature_tampering_missing_binding_and_changed_request_fail_closed(self):
        app = self.boundary()
        raw, _ = compose_request({"mode": "advanced", "free_text": "one red bowl", "quality": "fast"})
        signed = Application._sign_composer_message(app, raw)
        for data in ({"envelope": raw, "request": "one red bowl"}, {"envelope": signed.replace('"quality":"fast"', '"quality":"quality"'), "request": "one red bowl"}, {"envelope": signed, "request": "different request"}, {"envelope": "ordinary prose", "request": "ordinary prose"}):
            with self.assertRaises(CompatibilityError):
                Application.generation_provenance(app, data)

    def test_invalid_language_rejected_and_language_is_optional_for_existing_clients(self):
        with self.assertRaises(CompatibilityError):
            compose_request({"mode": "advanced", "free_text": "bowl", "ui_language": "private"})
        app = self.boundary()
        raw, _ = compose_request({"mode": "advanced", "free_text": "bowl"})
        result = Application.generation_provenance(app, {"envelope": Application._sign_composer_message(app, raw), "request": "bowl"})
        self.assertNotIn("language", result)
        self.assertNotIn("composer_version", result)
