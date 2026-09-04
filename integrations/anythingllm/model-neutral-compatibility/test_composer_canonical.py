#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from compatibility import compose_request  # noqa: E402
from composer_canonical import composer_canonical_json  # noqa: E402
from server import Application  # noqa: E402


class ComposerCanonicalizationTests(unittest.TestCase):
    def setUp(self):
        self.boundary = SimpleNamespace(auth_token="cross-runtime-test-key")

    def node(self, expression: str, value):
        module = (HERE.parent / "anythingllm" / "aagComposerHistory.js").as_posix()
        script = f"""
const fs = require("fs");
const composer = require({json.dumps(module)});
const value = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({expression}));
"""
        result = subprocess.run(
            ["node", "-e", script],
            input=json.dumps(value, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def sign(self, data: dict) -> str:
        message, _attachments = compose_request(data)
        return Application._sign_composer_message(self.boundary, message)

    def trust(self, message: str):
        return Application.trusted_composer_intent(
            self.boundary,
            {"messages": [{"role": "user", "content": message}]},
        )

    def test_rfc8785_numbers_match_javascript(self):
        values = [
            1.0,
            0.0,
            2.0,
            100.0,
            0.72,
            0.95,
            0.123456,
            0.000001,
            1e-7,
            1e20,
            1e21,
            333333333.33333329,
            4.5,
            0.002,
            1e-27,
            -0.0,
        ]
        python_values = [composer_canonical_json(value) for value in values]
        javascript_values = self.node(
            "value.map((item) => composer.composerCanonicalJson(item))",
            values,
        )
        self.assertEqual(python_values, javascript_values)
        self.assertEqual(python_values[:4], ["1", "0", "2", "100"])

    def test_multiple_atlas_confidences_round_trip_python_javascript_python(self):
        user_request = "A cinematic film still of a small spacecraft landing in a quiet desert at sunset."
        signed = self.sign(
            {
                "mode": "advanced",
                "free_text": user_request,
                "operation": "generate",
                "visual_family": "cinematic-film-still",
                "visual_subfamily": "feature-film-look",
                "atlas_selection_mode": "manual_taxonomy",
                "aspect_ratio": "16:9",
            }
        )
        self.assertGreater(len(signed), 4000)
        self.assertIn('"confidence":1', signed)
        self.assertNotIn('"confidence":1.0', signed)
        observed = self.node(
            "({visible: composer.visibleComposerPrompt(value), invocation: composer.composerInvocationPrompt(value)})",
            signed,
        )
        self.assertEqual(observed, {"visible": user_request, "invocation": user_request})
        intent = self.trust(signed)
        self.assertEqual(intent["knowledge_modules"]["visual_atlas"]["confidence"], 1)
        selected = intent["knowledge_modules"]["visual_atlas"]["selections"]
        self.assertGreaterEqual(len(selected), 1)
        self.assertTrue(all(item["confidence"] == 1 for item in selected))

    def test_envelope_variants_remain_cross_runtime_visible(self):
        attachment = {
            "name": "source.png",
            "mime": "image/png",
            "contentString": "data:image/png;base64,iVBORw0KGgo=",
        }
        cases = [
            {
                "name": "english-manual-browse",
                "data": {
                    "mode": "advanced",
                    "free_text": "A soft watercolor village at dawn",
                    "visual_family": "fine-art-traditional-media",
                    "visual_subfamily": "watercolor",
                    "atlas_selection_mode": "manual_browse",
                },
            },
            {
                "name": "hebrew-manual",
                "data": {
                    "mode": "advanced",
                    "free_text": "רחוב ירושלמי עתיק בצבעי מים",
                    "visual_family": "fine-art-traditional-media",
                    "visual_subfamily": "watercolor",
                    "atlas_selection_mode": "manual_taxonomy",
                },
            },
            {
                "name": "advanced-auto-style",
                "data": {
                    "mode": "advanced",
                    "free_text": "Make this look like an old travel poster",
                    "atlas_selection_mode": "auto",
                },
            },
            {
                "name": "no-style",
                "data": {
                    "mode": "advanced",
                    "free_text": "A red cup on a plain table",
                },
            },
            {
                "name": "human-identity",
                "data": {
                    "mode": "advanced",
                    "free_text": "Show this same person walking in a garden",
                    "operation": "transform",
                    "edit_mode": "not_applicable",
                    "reference_purpose": "identity",
                    "reference_source": "current_upload",
                    "source_policy": "current_attachment",
                    "preservation": "identity",
                    "attachments": [attachment],
                },
            },
            {
                "name": "transform",
                "data": {
                    "mode": "advanced",
                    "free_text": "Remove the sign and preserve everything else",
                    "operation": "transform",
                    "edit_mode": "preserve",
                    "source_policy": "current_attachment",
                    "preservation": "subject",
                    "attachments": [attachment],
                },
            },
            {
                "name": "batch",
                "data": {
                    "mode": "advanced",
                    "free_text": "Two coordinated children's book illustrations",
                    "operation": "generate",
                    "count": 2,
                    "visual_family": "illustration",
                    "visual_subfamily": "childrens-book",
                    "atlas_selection_mode": "manual_taxonomy",
                },
            },
        ]
        for case in cases:
            with self.subTest(case=case["name"]):
                signed = self.sign(case["data"])
                visible = self.node("composer.visibleComposerPrompt(value)", signed)
                self.assertEqual(visible, case["data"]["free_text"])
                self.assertIsNotNone(self.trust(signed))

        auto_message, auto_attachments = compose_request(
            {"mode": "auto", "free_text": "ordinary natural-language request"}
        )
        self.assertEqual(auto_message, "ordinary natural-language request")
        self.assertEqual(auto_attachments, [])
        self.assertEqual(
            self.node("composer.composerInvocationPrompt(value)", auto_message),
            auto_message,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
