#!/usr/bin/env python3

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from compatibility import (  # noqa: E402
    CanonicalCall,
    composer_intent_from_message,
    compose_request,
    normalize_composer_candidate,
)
from visual_atlas import AtlasError, VisualAtlas  # noqa: E402


class VisualAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atlas = VisualAtlas()

    def test_catalog_is_manifest_backed_complete_and_path_free(self):
        catalog = self.atlas.catalog()
        self.assertEqual(catalog["schema"], "aag.visual-style-atlas.catalog.v1")
        self.assertEqual(catalog["total_entries"], 493)
        self.assertEqual(len(catalog["families"]), 28)
        self.assertEqual(sum(len(item["subfamilies"]) for item in catalog["families"]), 493)
        first = catalog["families"][0]["subfamilies"][0]
        self.assertEqual(first["id"], "photorealistic")
        self.assertEqual(first["label"], "Photorealistic")
        self.assertEqual(first["subfamily_id"], first["id"])
        self.assertEqual(first["subfamily_label"], first["label"])
        self.assertRegex(first["atlas"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(first["atlas"]["thumbnail_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(first["atlas"]["sha256"], first["atlas"]["thumbnail_sha256"])
        encoded = json.dumps(catalog)
        self.assertNotIn("output_path", encoded)
        self.assertNotIn("thumbnail_path", encoded)
        self.assertNotIn(str(PROJECT), encoded)

    def test_assets_resolve_only_by_canonical_ids_and_role(self):
        available = self.atlas.entry("photography", "cinematic")["assets_available"]
        if available:
            thumb, thumb_type = self.atlas.asset("photography", "cinematic", "thumbnail")
            preview, preview_type = self.atlas.asset("photography", "cinematic", "preview")
            self.assertTrue(thumb.is_file())
            self.assertTrue(preview.is_file())
            self.assertEqual(thumb_type, "image/webp")
            self.assertEqual(preview_type, "image/png")
            self.assertLess(thumb.stat().st_size, preview.stat().st_size)
        else:
            with self.assertRaisesRegex(AtlasError, "metadata-only"):
                self.atlas.asset("photography", "cinematic", "thumbnail")
        with self.assertRaises(AtlasError):
            self.atlas.asset("..", "cinematic", "thumbnail")

    def test_auto_matching_and_no_style_fallback(self):
        cases = {
            "watercolor Jerusalem street": ("fine-art-traditional-media", "watercolor"),
            "children's book illustration": ("illustration", "childrens-book"),
            "vintage travel poster": ("retro-vintage", "vintage-travel-poster"),
            "simple coloring page": ("coloring-page-line-art", "simple"),
            "תרשים טכני": ("infographic-educational", "technical-explainer"),
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                plan = self.atlas.select(request)
                self.assertTrue(plan["used"])
                self.assertLessEqual(len(plan["selections"]), 2)
                self.assertEqual(
                    (plan["selections"][0]["family_id"], plan["selections"][0]["subfamily_id"]),
                    expected,
                )
        plain = self.atlas.select("an old Jerusalem street with three people")
        self.assertFalse(plain["used"])
        self.assertEqual(plain["reason"], "no_style_intent")

    def test_advanced_auto_includes_only_small_relevant_context(self):
        message, _ = compose_request({
            "mode": "advanced",
            "free_text": "a clean technical diagram",
            "operation": "generate",
        })
        intent = composer_intent_from_message(message)
        plan = intent["knowledge_modules"]["visual_atlas"]
        self.assertTrue(plan["used"])
        self.assertEqual(plan["mode"], "auto")
        self.assertEqual(len(plan["selections"]), 1)
        self.assertEqual(plan["selections"][0]["subfamily_id"], "technical-explainer")
        self.assertNotIn("canonical_scene", message)
        self.assertNotIn("generation_prompt", message)
        self.assertLess(len(message), 6000)

    def test_manual_browse_overrides_text_and_is_carried_by_signed_intent(self):
        message, _ = compose_request({
            "mode": "advanced",
            "free_text": "Jerusalem at sunset, very soft pastel colors, like an old travel poster",
            "operation": "generate",
            "visual_family": "fine-art-traditional-media",
            "visual_subfamily": "watercolor",
            "atlas_selection_mode": "manual_browse",
        })
        intent = composer_intent_from_message(message)
        plan = intent["knowledge_modules"]["visual_atlas"]
        self.assertEqual(plan["mode"], "manual_browse")
        self.assertEqual(plan["reason"], "explicit_user_selection")
        self.assertEqual(plan["selections"][0]["subfamily_id"], "watercolor")

        parameters = json.loads((PROJECT / "image-agent/schemas/provider-task.schema.json").read_text())
        tools = [{"type": "function", "function": {"name": "aag-image-task", "description": "test", "parameters": parameters}}]
        prompt = "A Jerusalem street at sunset with soft pastel colors, coherent architecture, balanced composition, natural spatial depth, polished watercolor presentation, scene-appropriate lighting, controlled shadows, and clear visual detail."
        candidate = CanonicalCall("aag-image-task", {
            "operation": "generate",
            "prompt": prompt,
            "source_policy": "auto",
            "preservation": "none",
        }, 0)
        call = normalize_composer_candidate(candidate, tools, intent)
        self.assertEqual(call.arguments["prompt"], prompt)
        self.assertEqual(
            call.arguments["request"],
            "AAG_ATLAS_SELECTION_V1 mode=manual_browse family=fine-art-traditional-media subfamily=watercolor",
        )

    def test_no_style_has_no_dynamic_atlas_payload(self):
        message, _ = compose_request({
            "mode": "advanced",
            "free_text": "a stone house on a quiet street",
            "operation": "generate",
        })
        intent = composer_intent_from_message(message)
        self.assertNotIn("knowledge_modules", intent)

    def test_manual_mode_requires_an_exact_valid_style(self):
        with self.assertRaisesRegex(Exception, "exact family and subfamily"):
            compose_request({
                "mode": "advanced",
                "free_text": "a house",
                "operation": "generate",
                "visual_family": "photography",
                "visual_subfamily": "auto",
                "atlas_selection_mode": "manual_browse",
            })

    def test_identity_and_upscale_are_not_style_reference_routes(self):
        identity = self.atlas.select(
            "same person in watercolor",
            mode="manual_taxonomy",
            family_id="fine-art-traditional-media",
            subfamily_id="watercolor",
            operation="transform",
            preservation="identity",
        )
        self.assertFalse(identity["used"])
        self.assertEqual(identity["reason"], "identity_reference_protected")
        upscale = self.atlas.select("watercolor", operation="upscale")
        self.assertFalse(upscale["used"])
        self.assertEqual(upscale["reason"], "operation_excluded")


if __name__ == "__main__":
    unittest.main()
