import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compatibility import CompatibilityError, compose_request, composer_intent_from_message, composer_preview  # noqa: E402

ATTACHMENT = {"name": "old.png", "mime": "image/png", "contentString": "data:image/png;base64,iVBORw0KGgo="}


class ComposerColorizeTests(unittest.TestCase):
    def test_colorize_from_current_upload_keeps_quality_tier(self):
        message, attachments = compose_request(
            {
                "mode": "advanced",
                "free_text": "Colorize this old family photo",
                "operation": "colorize",
                "source_policy": "current_attachment",
                "source_index": 1,
                "preservation": "none",
                "quality": "quality",
                "attachments": [ATTACHMENT],
            }
        )
        self.assertEqual(len(attachments), 1)
        intent = composer_intent_from_message(message)
        self.assertEqual(intent["operation"], "colorize")
        self.assertEqual(intent["quality"], "quality")
        self.assertEqual(intent["preservation"], "none")
        self.assertEqual(intent["scale"], "none")
        self.assertEqual(intent["semantics"]["source_preservation"]["mode"], "colorize_restore_natural_color")
        preview = composer_preview({"mode": "advanced", "free_text": "x", "operation": "colorize", "source_policy": "previous_artifact", "preservation": "none"})
        self.assertTrue(any(line.startswith("Operation: Colorize") for line in preview["lines"]), preview)

    def test_colorize_previous_artifact_without_uploads(self):
        message, attachments = compose_request(
            {"mode": "advanced", "free_text": "Colorize the last image", "operation": "colorize", "source_policy": "previous_artifact", "preservation": "none"}
        )
        self.assertEqual(attachments, [])
        self.assertEqual(composer_intent_from_message(message)["source_policy"], "previous_artifact")

    def test_colorize_rejects_creative_and_upscale_controls(self):
        base = {"mode": "advanced", "free_text": "Colorize", "operation": "colorize", "source_policy": "previous_artifact", "preservation": "none"}
        for bad, pattern in [
            ({**base, "scale": 2}, "no upscale factor"),
            ({**base, "preservation": "subject"}, "preservation none"),
            ({**base, "visual_family": "product-commercial", "visual_subfamily": "hero-product"}, "do not apply"),
            ({**base, "aspect_ratio": "16:9"}, "do not apply"),
            ({**base, "edit_mode": "restyle"}, "Edit mode"),
            ({**base, "count": 2}, "one output"),
        ]:
            with self.assertRaisesRegex(CompatibilityError, pattern, msg=str(bad)):
                compose_request(bad)

    def test_colorize_current_upload_requires_an_attachment(self):
        with self.assertRaisesRegex(CompatibilityError, "source image"):
            compose_request({"mode": "advanced", "free_text": "Colorize", "operation": "colorize", "source_policy": "current_attachment", "preservation": "none"})


if __name__ == "__main__":
    unittest.main()


class ScaffoldArgumentSanityTests(unittest.TestCase):
    def test_repetitive_svg_scaffold_is_not_prose(self):
        from compatibility import validate_argument_text_sanity, markup_argument_sanity
        svg = '<svg viewBox="0 0 1000 1000"><g fill="none" stroke="black">' + ''.join(f'<rect x="{100 + i * 85}" y="300" width="30" height="110"/>' for i in range(9)) + "</g></svg>"
        validate_argument_text_sanity({"operation": "generate", "prompt": "a friendly coloring page", "scaffold": svg})
        self.assertEqual(markup_argument_sanity(svg).code, "SANE_MARKUP")
        with self.assertRaises(CompatibilityError) as ctx:
            validate_argument_text_sanity({"prompt": ("repeat " * 40).strip()})
        self.assertEqual(ctx.exception.code, "PATHOLOGICAL_REPETITION")
        with self.assertRaises(CompatibilityError) as ctx:
            validate_argument_text_sanity({"scaffold": "not markup at all"})
        self.assertEqual(ctx.exception.code, "MARKUP_ARGUMENT_INVALID")
        with self.assertRaises(CompatibilityError) as ctx:
            validate_argument_text_sanity({"scaffold": "<svg><tool_call></svg>"})
        self.assertEqual(ctx.exception.code, "TOOL_CONTROL_TOKEN_LEAK")
        with self.assertRaises(CompatibilityError) as ctx:
            validate_argument_text_sanity({"scaffold": "<svg>" + "x" * 30000 + "</svg>"})
        self.assertEqual(ctx.exception.code, "OUTPUT_TOO_LARGE")
