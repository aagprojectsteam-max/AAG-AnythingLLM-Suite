#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from compatibility import (  # noqa: E402
    COMPOSER_SUBFAMILIES,
    COMPOSER_TAXONOMY,
    CanonicalCall,
    CompatibilityError,
    adapter_instruction,
    build_adapter_payload,
    build_candidate_adapter_payload,
    canonical_to_openai_response,
    composer_preview,
    compose_request,
    composer_intent_from_message,
    composer_user_request_from_message,
    extract_native_candidate,
    normalize_tools,
    normalize_candidate_with_trusted_arguments,
    normalize_composer_candidate,
    openai_sse_events,
    parse_canonical_call,
    schema_hash,
    text_sanity,
    validate_json_schema,
    validate_native_tool_response,
    validate_composer_intent_call,
    validate_ordinary_response,
    validate_preserved_arguments,
    validate_required_arguments,
)
from server import AuditLog, CapabilityManager, RuntimeSession  # noqa: E402


def tool(name="safe_tool", schema=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "A bounded test tool.",
            "parameters": schema
            or {
                "type": "object",
                "properties": {"value": {"type": "string", "minLength": 1}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }


def response(content="Normal readable response", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "behavioral-test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


class TextSanityTests(unittest.TestCase):
    def test_normal_english(self):
        self.assertTrue(text_sanity("Hello, ordinary chat works.").ok)

    def test_normal_hebrew(self):
        self.assertTrue(text_sanity("שלום, הצ'אט עובד כרגיל.").ok)

    def test_raw_unused_rejected(self):
        self.assertEqual(text_sanity("hello <unused20>").code, "RAW_UNUSED_TOKEN")

    def test_tool_control_rejected(self):
        self.assertEqual(text_sanity("<tool_call>{}").code, "TOOL_CONTROL_TOKEN_LEAK")

    def test_pipe_control_rejected(self):
        self.assertEqual(text_sanity("<|turn> hello").code, "TOOL_CONTROL_TOKEN_LEAK")

    def test_generic_internal_token_rejected(self):
        self.assertEqual(text_sanity("<|im_end|>").code, "INTERNAL_SPECIAL_TOKEN_LEAK")

    def test_empty_rejected(self):
        self.assertEqual(text_sanity("  ").code, "EMPTY_OUTPUT")

    def test_binary_control_rejected(self):
        self.assertEqual(text_sanity("hello\x00world").code, "BINARY_CONTROL_OUTPUT")

    def test_non_alnum_rejected(self):
        self.assertEqual(text_sanity("... !!!").code, "UNUSABLE_NON_ALNUM_OUTPUT")

    def test_pathological_word_repetition_rejected(self):
        self.assertEqual(text_sanity(("repeat " * 40).strip()).code, "PATHOLOGICAL_REPETITION")

    def test_pathological_character_repetition_rejected(self):
        self.assertEqual(text_sanity("a" * 60).code, "PATHOLOGICAL_REPETITION")

    def test_reasoning_leak_rejected(self):
        value = response("Readable")
        value["choices"][0]["message"]["reasoning_content"] = "<unused7>"
        with self.assertRaisesRegex(CompatibilityError, "protocol-level"):
            validate_ordinary_response(value)


class SchemaTests(unittest.TestCase):
    def test_object_schema_accepts(self):
        validate_json_schema({"value": "yes"}, tool()["function"]["parameters"])

    def test_required_rejected(self):
        with self.assertRaises(CompatibilityError):
            validate_json_schema({}, tool()["function"]["parameters"])

    def test_additional_property_rejected(self):
        with self.assertRaises(CompatibilityError):
            validate_json_schema({"value": "yes", "extra": 1}, tool()["function"]["parameters"])

    def test_one_of(self):
        schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        validate_json_schema("ok", schema)
        with self.assertRaises(CompatibilityError):
            validate_json_schema([], schema)

    def test_array_bounds(self):
        schema = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2}
        validate_json_schema([1, 2], schema)
        with self.assertRaises(CompatibilityError):
            validate_json_schema([], schema)

    def test_local_ref(self):
        schema = {"$defs": {"name": {"type": "string", "const": "yes"}}, "$ref": "#/$defs/name"}
        validate_json_schema("yes", schema)

    def test_remote_ref_rejected(self):
        with self.assertRaisesRegex(CompatibilityError, "local"):
            validate_json_schema({}, {"$ref": "https://example.invalid/schema"})

    def test_duplicate_tool_rejected(self):
        with self.assertRaises(CompatibilityError):
            normalize_tools([tool(), tool()])

    def test_schema_hash_order_stable(self):
        self.assertEqual(schema_hash([tool()]), schema_hash([json.loads(json.dumps(tool()))]))


class CanonicalToolTests(unittest.TestCase):
    def test_valid_canonical_call(self):
        call = parse_canonical_call('{"tool_name":"safe_tool","arguments":{"value":"ok"}}', [tool()])
        self.assertEqual(call.tool_name, "safe_tool")
        self.assertEqual(call.repair_count, 0)

    def test_one_fence_repair(self):
        call = parse_canonical_call('```json\n{"tool_name":"safe_tool","arguments":{"value":"ok"}}\n```', [tool()])
        self.assertEqual(call.repair_count, 1)

    def test_prose_not_extracted(self):
        with self.assertRaisesRegex(CompatibilityError, "strict JSON"):
            parse_canonical_call('Here: {"tool_name":"safe_tool","arguments":{"value":"ok"}}', [tool()])

    def test_additional_key_rejected(self):
        with self.assertRaisesRegex(CompatibilityError, "only"):
            parse_canonical_call('{"tool_name":"safe_tool","arguments":{"value":"ok"},"extra":1}', [tool()])

    def test_unauthorized_tool_rejected(self):
        with self.assertRaisesRegex(CompatibilityError, "exact tool set"):
            parse_canonical_call('{"tool_name":"evil_tool","arguments":{}}', [tool()])

    def test_bad_arguments_rejected(self):
        with self.assertRaises(CompatibilityError):
            parse_canonical_call('{"tool_name":"safe_tool","arguments":{"value":""}}', [tool()])

    def test_raw_control_cannot_be_canonical(self):
        with self.assertRaises(CompatibilityError):
            parse_canonical_call('<tool_call>{"tool_name":"safe_tool","arguments":{"value":"ok"}}', [tool()])

    def test_valid_native_tool(self):
        native = response(
            None,
            [{"id": "call-1", "type": "function", "function": {"name": "safe_tool", "arguments": '{"value":"ok"}'}}],
        )
        self.assertEqual(validate_native_tool_response(native, [tool()])[0].tool_name, "safe_tool")

    def test_native_unknown_tool_rejected(self):
        native = response(
            None,
            [{"id": "call-1", "type": "function", "function": {"name": "evil", "arguments": "{}"}}],
        )
        with self.assertRaises(CompatibilityError):
            validate_native_tool_response(native, [tool()])

    def test_native_malformed_json_rejected_without_repair(self):
        native = response(
            None,
            [{"id": "call-1", "type": "function", "function": {"name": "safe_tool", "arguments": "{'value':'ok'}"}}],
        )
        with self.assertRaises(CompatibilityError):
            validate_native_tool_response(native, [tool()])

    def test_native_special_token_inside_argument_is_rejected(self):
        native = response(
            None,
            [{"id": "call-1", "type": "function", "function": {"name": "safe_tool", "arguments": '{"value":"<unused20>"}'}}],
        )
        with self.assertRaisesRegex(CompatibilityError, "protocol-level"):
            validate_native_tool_response(native, [tool()])

    def test_conflicting_native_calls_are_rejected(self):
        call = {"id": "call-1", "type": "function", "function": {"name": "safe_tool", "arguments": '{"value":"ok"}'}}
        with self.assertRaisesRegex(CompatibilityError, "Exactly one"):
            validate_native_tool_response(response(None, [call, {**call, "id": "call-2"}]), [tool()])

    def test_canonical_converts_to_openai(self):
        call = parse_canonical_call('{"tool_name":"safe_tool","arguments":{"value":"ok"}}', [tool()])
        converted = canonical_to_openai_response(response('{"tool_name":"safe_tool","arguments":{"value":"ok"}}'), call)
        function = converted["choices"][0]["message"]["tool_calls"][0]["function"]
        self.assertEqual(function["name"], "safe_tool")
        self.assertEqual(json.loads(function["arguments"]), {"value": "ok"})

    def test_sse_never_contains_adapter_wrapper(self):
        call = parse_canonical_call('{"tool_name":"safe_tool","arguments":{"value":"ok"}}', [tool()])
        converted = canonical_to_openai_response(response("temporary"), call)
        stream = b"".join(openai_sse_events(converted)).decode()
        self.assertIn('"tool_calls"', stream)
        self.assertNotIn('"tool_name"', stream)


class AdapterTests(unittest.TestCase):
    def test_instruction_contains_actual_schema(self):
        instruction = adapter_instruction([tool()])
        self.assertIn("safe_tool", instruction)
        self.assertIn("additionalProperties", instruction)

    def test_payload_removes_native_controls(self):
        payload = {
            "model": "behavioral-test-model",
            "messages": [{"role": "user", "content": "use the tool"}],
            "tools": [tool()],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "stream": True,
        }
        adapted = build_adapter_payload(payload, [tool()])
        self.assertNotIn("tools", adapted)
        self.assertNotIn("tool_choice", adapted)
        self.assertFalse(adapted["stream"])

    def test_tool_result_role_is_flattened(self):
        payload = {
            "model": "behavioral-test-model",
            "messages": [{"role": "tool", "tool_call_id": "abc", "content": "done"}],
            "tools": [tool()],
        }
        adapted = build_adapter_payload(payload, [tool()])
        self.assertEqual(adapted["messages"][-1]["role"], "user")
        self.assertIn("abc", adapted["messages"][-1]["content"])

    def test_non_text_part_rejected(self):
        payload = {
            "model": "behavioral-test-model",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}],
            "tools": [tool()],
        }
        with self.assertRaises(CompatibilityError):
            build_adapter_payload(payload, [tool()])

    def test_rejected_native_candidate_compacts_without_original_prompt(self):
        schema = tool(
            "image_tool",
            {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 10},
                    "quality": {"type": "string", "enum": ["auto", "quality"]},
                },
                "required": ["prompt", "quality"],
                "additionalProperties": False,
            },
        )
        native = response(
            None,
            [{
                "id": "candidate",
                "type": "function",
                "function": {
                    "name": "image_tool",
                    "arguments": json.dumps({"prompt": "A long professional creative prompt", "quality": "invalid"}),
                },
            }],
        )
        candidate = extract_native_candidate(native, [schema])
        payload = {
            "model": "behavioral-test-model",
            "messages": [{"role": "system", "content": "ORIGINAL PROFESSIONAL PROMPT " * 1000}],
            "max_tokens": 900,
        }
        adapted = build_candidate_adapter_payload(
            payload,
            [schema],
            candidate,
            preserve_arguments={"prompt": candidate.arguments["prompt"]},
            required_arguments={"quality": "quality"},
        )
        encoded = json.dumps(adapted)
        self.assertNotIn("ORIGINAL PROFESSIONAL PROMPT", encoded)
        self.assertIn("A long professional creative prompt", encoded)
        self.assertEqual(adapted["max_tokens"], 512)

    def test_candidate_repair_preservation_is_fail_closed(self):
        valid = CanonicalCall("safe_tool", {"value": "unchanged"}, 0)
        validate_preserved_arguments(valid, {"value": "unchanged"})
        validate_required_arguments(valid, {"value": "unchanged"})
        with self.assertRaisesRegex(CompatibilityError, "protected"):
            validate_preserved_arguments(valid, {"value": "changed"})
        with self.assertRaisesRegex(CompatibilityError, "authoritative"):
            validate_required_arguments(valid, {"value": "changed"})

    def test_trusted_projection_preserves_prompt_and_uses_only_live_fields(self):
        schema = tool(
            "image_tool",
            {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 10},
                    "quality": {"type": "string", "enum": ["auto", "quality"]},
                    "count": {"type": "integer", "minimum": 1, "maximum": 2},
                },
                "required": ["prompt", "quality"],
                "additionalProperties": False,
            },
        )
        candidate = CanonicalCall(
            "image_tool",
            {"prompt": "A long professional creative prompt", "quality": "invalid", "count": 2, "invented": True},
            0,
        )
        normalized = normalize_candidate_with_trusted_arguments(
            candidate,
            [schema],
            {"quality": "quality"},
            omit_arguments={"count"},
        )
        self.assertEqual(
            normalized.arguments,
            {"prompt": "A long professional creative prompt", "quality": "quality"},
        )

    def test_batch_trusted_projection_preserves_every_prompt(self):
        schema = tool(
            "aag-image-batch",
            {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["multi_generate"]},
                    "collection_brief": {"type": "string", "minLength": 1},
                    "count": {"type": "integer", "minimum": 2, "maximum": 10},
                    "quality": {"type": "string", "enum": ["auto", "fast"]},
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string", "minLength": 1},
                                "aspect_ratio": {"type": "string", "enum": ["auto", "1:1"]},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                            "required": ["prompt"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["operation", "collection_brief", "count", "quality", "items"],
                "additionalProperties": False,
            },
        )
        candidate = CanonicalCall(
            "aag-image-batch",
            {
                "operation": "multi_generate",
                "collection_brief": "Three coordinated leaves",
                "count": 3,
                "quality": "auto",
                "items": [
                    {"prompt": f"Professional leaf prompt {index}", "aspect_ratio": "auto", "width": 512, "height": 512}
                    for index in range(3)
                ],
            },
            0,
        )
        intent = {
            "operation": "generate",
            "count": 3,
            "aspect_ratio": "1:1",
            "quality": "fast",
            "source_policy": "auto",
            "preservation": "none",
            "scale": "none",
        }
        normalized = normalize_composer_candidate(candidate, [schema], intent)
        self.assertEqual(normalized.arguments["count"], 3)
        self.assertEqual(normalized.arguments["quality"], "fast")
        self.assertEqual(
            [item["prompt"] for item in normalized.arguments["items"]],
            [f"Professional leaf prompt {index}" for index in range(3)],
        )
        self.assertTrue(all(item["aspect_ratio"] == "1:1" for item in normalized.arguments["items"]))
        self.assertTrue(all("width" not in item and "height" not in item for item in normalized.arguments["items"]))


class ComposerTests(unittest.TestCase):
    def test_auto_mode_is_free_text_only(self):
        message, attachments = compose_request({"mode": "auto", "free_text": "A cinematic fox", "count": 4})
        self.assertEqual(message, "A cinematic fox")
        self.assertEqual(attachments, [])

    def test_advanced_authoritative_contract(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "A cinematic fox",
                "operation": "generate",
                "visual_family": "photography",
                "visual_subfamily": "editorial",
                "aspect_ratio": "16:9",
                "count": 3,
                "quality": "quality",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            }
        )
        self.assertIn("authoritative user-selected constraints", message)
        self.assertIn("complete professional creative FLUX prompt", message)
        self.assertIn("AUTHORITATIVE_CONTENT_PRESERVATION=", message)
        self.assertIn(
            "Preserve every requested subject, object, action, relationship, quantity, and named attribute.",
            message,
        )
        self.assertIn('"count":3', message)
        self.assertIn("A cinematic fox", message)
        intent = composer_intent_from_message(message)
        self.assertEqual(
            intent["semantics"]["explicit_constraints"]["visual_family"],
            "photography",
        )
        self.assertEqual(
            intent["semantics"]["explicit_constraints"]["aspect_ratio"],
            "16:9",
        )
        self.assertIn(
            "background", intent["semantics"]["model_discretion_fields"]
        )

    def test_advanced_full_auto_is_model_discretion_and_preserves_exact_text(self):
        user_request = "  שועל רודף אחרי חתול\n"
        message, _ = compose_request(
            {"mode": "advanced", "free_text": user_request}
        )
        intent = composer_intent_from_message(message)
        semantics = intent["semantics"]
        self.assertEqual(
            semantics["explicit_constraints"],
            {"operation": "generate", "count": 1},
        )
        self.assertIn("visual_family", semantics["model_discretion_fields"])
        self.assertIn("aspect_ratio", semantics["model_discretion_fields"])
        self.assertIn("quality", semantics["model_discretion_fields"])
        self.assertNotIn("source_policy", semantics["model_discretion_fields"])
        self.assertEqual(composer_user_request_from_message(message), user_request)

    def test_partial_selection_is_authoritative_and_other_fields_are_discretion(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "שועל רודף אחרי חתול",
                "visual_family": "fantasy",
                "aspect_ratio": "16:9",
            }
        )
        semantics = composer_intent_from_message(message)["semantics"]
        self.assertEqual(semantics["explicit_constraints"]["visual_family"], "fantasy")
        self.assertEqual(semantics["explicit_constraints"]["aspect_ratio"], "16:9")
        self.assertIn("visual_subfamily", semantics["model_discretion_fields"])
        self.assertIn("quality", semantics["model_discretion_fields"])

    def test_explicit_no_visible_text_is_authoritative_not_not_applicable(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "A clean illustration",
                "visible_text": "none",
            }
        )
        semantics = composer_intent_from_message(message)["semantics"]
        self.assertEqual(semantics["explicit_constraints"]["visible_text"], "none")
        self.assertNotIn("visible_text", semantics["model_discretion_fields"])

    def test_not_applicable_fields_are_absent_from_semantic_requirements(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Upscale the latest image",
                "operation": "upscale",
                "source_policy": "previous_artifact",
                "preservation": "none",
                "scale": "auto",
            }
        )
        semantics = composer_intent_from_message(message)["semantics"]
        self.assertEqual(
            semantics["explicit_constraints"]["source_policy"],
            "previous_artifact",
        )
        self.assertIn("scale", semantics["model_discretion_fields"])
        for field in ("visual_family", "aspect_ratio", "quality", "background"):
            self.assertNotIn(field, semantics["explicit_constraints"])
            self.assertNotIn(field, semantics["model_discretion_fields"])
        self.assertEqual(
            semantics["source_preservation"],
            {
                "mode": "upscale_preserve_appearance",
                "preserve_unspecified_source_properties": True,
                "style_change_authorized": False,
            },
        )

    def test_edit_preserve_omits_unspecified_creation_controls(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Keep the composition but make the background nighttime.",
                "operation": "transform",
                "edit_mode": "preserve",
                "source_policy": "previous_artifact",
                "preservation": "subject",
            }
        )
        intent = composer_intent_from_message(message)
        semantics = intent["semantics"]
        self.assertEqual(intent["edit_mode"], "preserve")
        self.assertEqual(
            semantics["explicit_constraints"],
            {
                "operation": "transform",
                "count": 1,
                "edit_mode": "preserve",
                "source_policy": "previous_artifact",
                "preservation": "subject",
            },
        )
        self.assertEqual(semantics["model_discretion_fields"], [])
        self.assertEqual(
            semantics["source_preservation"],
            {
                "mode": "preserve_current_appearance",
                "preserve_unspecified_source_properties": True,
                "style_change_authorized": False,
            },
        )
        for field in (
            "visual_family", "visual_subfamily", "background", "aspect_ratio",
            "quality", "visible_text", "seed", "source_instruction",
        ):
            self.assertNotIn(field, semantics["explicit_constraints"])
            self.assertNotIn(field, semantics["model_discretion_fields"])
        self.assertIn("preserve every source property not explicitly changed", message)
        self.assertIn("Upscale never authorizes creative redesign", message)

    def test_edit_restyle_auto_style_is_model_discretion_only_for_style(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Keep the same scene and subjects.",
                "operation": "transform",
                "edit_mode": "restyle",
                "source_policy": "previous_artifact",
                "preservation": "subject",
            }
        )
        semantics = composer_intent_from_message(message)["semantics"]
        self.assertEqual(
            semantics["model_discretion_fields"],
            ["visual_family", "visual_subfamily"],
        )
        self.assertTrue(semantics["source_preservation"]["style_change_authorized"])
        self.assertEqual(semantics["source_preservation"]["mode"], "restyle_image")
        for field in ("background", "aspect_ratio", "quality", "visible_text", "seed"):
            self.assertNotIn(field, semantics["explicit_constraints"])
            self.assertNotIn(field, semantics["model_discretion_fields"])

    def test_edit_restyle_explicit_style_is_authoritative(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Keep the same scene and subjects.",
                "operation": "transform",
                "edit_mode": "restyle",
                "visual_family": "fine-art-traditional-media",
                "visual_subfamily": "oil",
                "source_policy": "previous_artifact",
                "preservation": "subject",
            }
        )
        semantics = composer_intent_from_message(message)["semantics"]
        self.assertEqual(
            semantics["explicit_constraints"]["visual_family"],
            "fine-art-traditional-media",
        )
        self.assertEqual(semantics["explicit_constraints"]["visual_subfamily"], "oil")
        self.assertNotIn("visual_family", semantics["model_discretion_fields"])
        self.assertNotIn("visual_subfamily", semantics["model_discretion_fields"])

    def test_edit_preserve_rejects_style_control_conflict(self):
        with self.assertRaisesRegex(CompatibilityError, "cannot include style"):
            compose_request(
                {
                    "mode": "advanced",
                    "free_text": "Change only the lighting.",
                    "operation": "transform",
                    "edit_mode": "preserve",
                    "visual_family": "fantasy",
                    "source_policy": "previous_artifact",
                    "preservation": "subject",
                }
            )

    def test_legacy_edit_style_infers_restyle_without_weakening_validation(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Restyle the existing image.",
                "operation": "transform",
                "visual_family": "fine-art-traditional-media",
                "visual_subfamily": "oil",
                "source_policy": "previous_artifact",
                "preservation": "subject",
            }
        )
        self.assertEqual(composer_intent_from_message(message)["edit_mode"], "restyle")

    def test_create_from_reference_identity_uses_signed_scene_semantics(self):
        reference_bytes = b"trusted latest artifact bytes"
        attachment = {
            "name": "img-11111111-1111-1111-1111-111111111111.png",
            "mime": "image/png",
            "contentString": "data:image/png;base64,dHJ1c3RlZCBsYXRlc3QgYXJ0aWZhY3QgYnl0ZXM=",
        }
        digest = hashlib.sha256(reference_bytes).hexdigest()
        message, attachments = compose_request(
            {
                "mode": "advanced",
                "free_text": "תעשה את האדם שבתמונה רוכב על חמור",
                "operation": "transform",
                "edit_mode": "not_applicable",
                "reference_purpose": "identity",
                "reference_source": "latest_thread_artifact",
                "reference_artifact_sha256": digest,
                "visual_family": "photography",
                "visual_subfamily": "cinematic",
                "source_policy": "current_attachment",
                "source_index": 1,
                "preservation": "identity",
                "attachments": [attachment],
            }
        )
        self.assertEqual(attachments, [attachment])
        intent = composer_intent_from_message(message)
        semantics = intent["semantics"]
        self.assertEqual(intent["operation"], "transform")
        self.assertEqual(intent["reference_purpose"], "identity")
        self.assertEqual(intent["reference_source"], "latest_thread_artifact")
        self.assertEqual(intent["reference_artifact_sha256"], digest)
        self.assertEqual(
            semantics["explicit_constraints"]["composer_operation"],
            "create_from_reference",
        )
        self.assertEqual(semantics["explicit_constraints"]["preservation"], "identity")
        self.assertEqual(semantics["explicit_constraints"]["visual_family"], "photography")
        self.assertEqual(semantics["explicit_constraints"]["visual_subfamily"], "cinematic")
        self.assertTrue(semantics["reference_creation"]["new_scene_generation_authorized"])
        self.assertTrue(semantics["reference_creation"]["preserve_person_identity"])
        self.assertFalse(semantics["reference_creation"]["preserve_source_composition_by_default"])
        self.assertNotIn("source_preservation", semantics)
        self.assertIn("never downgrade to subject preservation", message)
        self.assertIn("Do not preserve the source composition by default", message)

    def test_create_from_reference_general_visual_does_not_claim_identity(self):
        attachment = {
            "name": "reference.png",
            "mime": "image/png",
            "contentString": "data:image/png;base64,aGVsbG8=",
        }
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Create a new composition inspired by this reference.",
                "operation": "transform",
                "edit_mode": "not_applicable",
                "reference_purpose": "general_visual",
                "reference_source": "current_upload",
                "source_policy": "current_attachment",
                "source_index": 1,
                "preservation": "subject",
                "attachments": [attachment],
            }
        )
        semantics = composer_intent_from_message(message)["semantics"]
        self.assertEqual(semantics["explicit_constraints"]["preservation"], "subject")
        self.assertFalse(semantics["reference_creation"]["preserve_person_identity"])
        self.assertTrue(semantics["reference_creation"]["preserve_general_visual_reference"])
        self.assertIn("visual_family", semantics["model_discretion_fields"])
        self.assertIn("aspect_ratio", semantics["model_discretion_fields"])

    def test_create_from_reference_missing_or_mismatched_reference_fails_closed(self):
        base = {
            "mode": "advanced",
            "free_text": "Create a new scene with the same person.",
            "operation": "transform",
            "edit_mode": "not_applicable",
            "reference_purpose": "identity",
            "reference_source": "current_upload",
            "source_policy": "current_attachment",
            "source_index": 1,
            "preservation": "identity",
        }
        with self.assertRaises(CompatibilityError) as missing:
            compose_request(base)
        self.assertEqual(missing.exception.code, "REFERENCE_IMAGE_MISSING")

        attachment = {
            "name": "img-11111111-1111-1111-1111-111111111111.png",
            "mime": "image/png",
            "contentString": "data:image/png;base64,aGVsbG8=",
        }
        with self.assertRaises(CompatibilityError) as changed:
            compose_request(
                {
                    **base,
                    "reference_source": "latest_thread_artifact",
                    "reference_artifact_sha256": "0" * 64,
                    "attachments": [attachment],
                }
            )
        self.assertEqual(changed.exception.code, "REFERENCE_IMAGE_INVALID")

    def test_create_from_reference_identity_cannot_downgrade_preservation(self):
        attachment = {
            "name": "reference.png",
            "mime": "image/png",
            "contentString": "data:image/png;base64,aGVsbG8=",
        }
        with self.assertRaisesRegex(CompatibilityError, "preservation route"):
            compose_request(
                {
                    "mode": "advanced",
                    "free_text": "Create a new scene with the same person.",
                    "operation": "transform",
                    "edit_mode": "not_applicable",
                    "reference_purpose": "identity",
                    "reference_source": "current_upload",
                    "source_policy": "current_attachment",
                    "source_index": 1,
                    "preservation": "subject",
                    "attachments": [attachment],
                }
            )

    def test_auto_execution_fields_preserve_supported_model_choices(self):
        message, _ = compose_request(
            {"mode": "advanced", "free_text": "A cinematic fox"}
        )
        intent = composer_intent_from_message(message)
        schema = tool(
            "aag-image-task",
            {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["generate"]},
                    "prompt": {"type": "string"},
                    "source_policy": {"type": "string", "enum": ["auto"]},
                    "preservation": {"type": "string", "enum": ["none"]},
                    "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "auto"]},
                    "quality": {"type": "string", "enum": ["auto", "balanced", "quality"]},
                },
                "required": ["operation", "prompt", "source_policy", "preservation"],
                "additionalProperties": False,
            },
        )
        candidate = CanonicalCall(
            "aag-image-task",
            {
                "operation": "generate",
                "prompt": "A complete professional cinematic fox prompt",
                "source_policy": "auto",
                "preservation": "none",
                "aspect_ratio": "16:9",
                "quality": "balanced",
            },
            0,
        )
        normalized = normalize_composer_candidate(candidate, [schema], intent)
        self.assertEqual(normalized.arguments["aspect_ratio"], "16:9")
        self.assertEqual(normalized.arguments["quality"], "balanced")

    def test_edit_requires_source(self):
        with self.assertRaisesRegex(CompatibilityError, "source image"):
            compose_request(
                {
                    "mode": "advanced",
                    "free_text": "Change the sky",
                    "operation": "transform",
                    "visual_family": "auto",
                    "aspect_ratio": "auto",
                    "count": 1,
                    "quality": "auto",
                    "source_policy": "current_attachment",
                    "preservation": "subject",
                    "scale": "none",
                }
            )

    def test_valid_source(self):
        data = "data:image/png;base64,iVBORw0KGgo="
        _, attachments = compose_request(
            {
                "mode": "advanced",
                "free_text": "Change the sky",
                "operation": "transform",
                "visual_family": "auto",
                "aspect_ratio": "auto",
                "count": 1,
                "quality": "auto",
                "source_policy": "current_attachment",
                "preservation": "subject",
                "scale": "none",
                "attachments": [{"name": "source.png", "mime": "image/png", "contentString": data}],
            }
        )
        self.assertEqual(len(attachments), 1)

    def test_task_intent_validation(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "A cinematic fox",
                "operation": "generate",
                "aspect_ratio": "16:9",
                "count": 1,
                "quality": "quality",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            }
        )
        intent = composer_intent_from_message(message)
        call = CanonicalCall(
            "aag-image-task",
            {
                "operation": "generate",
                "prompt": "A complete professional prompt",
                "source_policy": "auto",
                "preservation": "none",
                "aspect_ratio": "16:9",
                "quality": "quality",
            },
            0,
        )
        validate_composer_intent_call(intent, call)
        with self.assertRaisesRegex(CompatibilityError, "aspect_ratio"):
            validate_composer_intent_call(intent, CanonicalCall(call.tool_name, {**call.arguments, "aspect_ratio": "1:1"}, 0))

    def test_batch_intent_validation(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Five forest scenes",
                "operation": "generate",
                "aspect_ratio": "4:3",
                "count": 5,
                "quality": "balanced",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            }
        )
        intent = composer_intent_from_message(message)
        call = CanonicalCall(
            "aag-image-batch",
            {
                "operation": "multi_generate",
                "collection_brief": "Five related forest scenes",
                "count": 5,
                "quality": "balanced",
                "items": [{"prompt": f"Professional forest scene {index}", "aspect_ratio": "4:3"} for index in range(5)],
            },
            0,
        )
        validate_composer_intent_call(intent, call)
        with self.assertRaisesRegex(CompatibilityError, "count"):
            validate_composer_intent_call(intent, CanonicalCall(call.tool_name, {**call.arguments, "count": 4}, 0))

    def test_invalid_count(self):
        with self.assertRaises(CompatibilityError):
            compose_request({"mode": "advanced", "free_text": "fox", "count": 20})

    def test_invalid_attachment_mime(self):
        with self.assertRaises(CompatibilityError):
            compose_request(
                {
                    "mode": "auto",
                    "free_text": "fox",
                    "attachments": [{"name": "x.svg", "mime": "image/svg+xml", "contentString": "data:image/svg+xml;base64,AA=="}],
                }
            )

    def test_full_taxonomy_is_complete_and_unique(self):
        families = COMPOSER_TAXONOMY["raw"]["families"]
        pairs = [(family["id"], entry["id"]) for family in families for entry in family["subfamilies"]]
        self.assertEqual(len(families), 28)
        self.assertEqual(len(pairs), 493)
        self.assertEqual(len(set(pairs)), len(pairs))
        self.assertTrue(all(family["classification"] == "MODEL_HINT_ONLY" for family in families))
        self.assertTrue(all(family["subfamily_classification"] == "MODEL_HINT_ONLY" for family in families))

    def test_cross_family_subfamily_tamper_rejected(self):
        with self.assertRaisesRegex(CompatibilityError, "subfamily"):
            compose_request(
                {
                    "mode": "advanced",
                    "free_text": "A portrait",
                    "visual_family": "photography",
                    "visual_subfamily": "watercolor",
                }
            )

    def test_unknown_hidden_field_and_exact_text_mode_rejected(self):
        with self.assertRaises(CompatibilityError):
            compose_request({"mode": "advanced", "free_text": "fox", "hidden_tool": "shell"})
        with self.assertRaises(CompatibilityError):
            compose_request({"mode": "advanced", "free_text": "fox", "visible_text": "exact_text_required"})

    def test_custom_batch_count_six_is_supported(self):
        message, _ = compose_request(
            {
                "mode": "advanced",
                "free_text": "Six coordinated botanical cards",
                "operation": "generate",
                "count": 6,
                "batch_relationship": "coordinated_series",
            }
        )
        intent = composer_intent_from_message(message)
        self.assertEqual(intent["count"], 6)
        self.assertEqual(intent["creative_direction"]["batch_relationship"], "coordinated_series")

    def test_multi_upload_source_index_is_signed(self):
        attachment = {"name": "source.png", "mime": "image/png", "contentString": "data:image/png;base64,iVBORw0KGgo="}
        message, attachments = compose_request(
            {
                "mode": "advanced",
                "free_text": "Turn the second object blue",
                "operation": "transform",
                "source_policy": "current_attachment",
                "source_index": 2,
                "preservation": "subject",
                "attachments": [attachment, {**attachment, "name": "source-2.png"}],
            }
        )
        self.assertEqual(len(attachments), 2)
        self.assertEqual(composer_intent_from_message(message)["source_index"], 2)

    def test_unauthorized_source_index_rejected(self):
        attachment = {"name": "source.png", "mime": "image/png", "contentString": "data:image/png;base64,iVBORw0KGgo="}
        with self.assertRaisesRegex(CompatibilityError, "Select one"):
            compose_request(
                {
                    "mode": "advanced",
                    "free_text": "Edit source",
                    "operation": "transform",
                    "source_policy": "current_attachment",
                    "source_index": 2,
                    "preservation": "subject",
                    "attachments": [attachment],
                }
            )

    def test_previous_artifact_and_three_x_upscale_supported(self):
        message, attachments = compose_request(
            {
                "mode": "advanced",
                "free_text": "Upscale faithfully",
                "operation": "upscale",
                "source_policy": "previous_artifact",
                "preservation": "none",
                "scale": 3,
            }
        )
        self.assertEqual(attachments, [])
        intent = composer_intent_from_message(message)
        self.assertEqual(intent["source_policy"], "previous_artifact")
        self.assertEqual(intent["scale"], 3)

    def test_identity_requires_one_current_source(self):
        attachment = {"name": "person.png", "mime": "image/png", "contentString": "data:image/png;base64,iVBORw0KGgo="}
        with self.assertRaisesRegex(CompatibilityError, "exactly one"):
            compose_request(
                {
                    "mode": "advanced",
                    "free_text": "Keep the same person",
                    "operation": "transform",
                    "source_policy": "current_attachment",
                    "source_index": 1,
                    "preservation": "identity",
                    "attachments": [attachment, {**attachment, "name": "person-2.png"}],
                }
            )

    def test_friendly_preview_contains_no_raw_envelope(self):
        preview = composer_preview(
            {
                "mode": "advanced",
                "free_text": "A clean product portrait",
                "visual_family": "product-commercial",
                "visual_subfamily": "hero-product",
                "aspect_ratio": "1:1",
                "quality": "fast",
            }
        )
        joined = "\n".join(preview["lines"])
        self.assertIn("Product / Commercial / Hero product", joined)
        self.assertNotIn("AAG_COMPOSER", joined)
        self.assertNotIn("{", joined)

    def test_auto_ignores_known_advanced_fields_without_leak(self):
        message, _ = compose_request(
            {
                "mode": "auto",
                "free_text": "A clean reset",
                "visual_family": "photography",
                "count": 10,
                "quality": "quality",
            }
        )
        self.assertEqual(message, "A clean reset")


class RuntimeAndCapabilityTests(unittest.TestCase):
    def test_runtime_fingerprint_uses_behavioral_session_not_model_routing(self):
        process = subprocess.Popen(["/usr/bin/sleep", "30"])
        self.addCleanup(process.kill)
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            raw = Path(f"/proc/{process.pid}/stat").read_text()
            starttime = raw.rsplit(") ", 1)[1].split()[19]
            values = {
                "pid": str(process.pid),
                "starttime": starttime,
                "model": "/arbitrary/model/path.gguf",
                "executable": "/usr/bin/sleep",
                "context": "8192",
                "profile": "normal",
            }
            for name, value in values.items():
                (state / name).write_text(value)
            digest, meta = RuntimeSession(state, values["executable"]).fingerprint()
            self.assertEqual(len(digest), 64)
            self.assertNotIn("model", meta)
            self.assertIn("model_sha256", meta)
        process.terminate()
        process.wait(timeout=3)

    def test_model_specific_names_absent_from_primary_logic(self):
        primary = (HERE / "compatibility.py").read_text().lower() + (HERE / "server.py").read_text().lower()
        for forbidden in ("gemma", "qwen", "mistral", "phi-", "llama3"):
            self.assertNotIn(forbidden, primary)

    def test_capability_native_path(self):
        class FakeRuntime:
            def fingerprint(self):
                return "session-a", {"pid": "1"}

        class FakeUpstream:
            def __init__(self):
                self.calls = 0

            def json(self, payload, timeout=None):
                self.calls += 1
                if payload.get("tools"):
                    nonce = payload["tools"][0]["function"]["parameters"]["properties"]["nonce"]["const"]
                    return response(None, [{"id": "p", "type": "function", "function": {"name": "aag_compatibility_noop", "arguments": json.dumps({"nonce": nonce})}}])
                return response("Normal human-readable chat works.")

        with tempfile.TemporaryDirectory() as temporary:
            upstream = FakeUpstream()
            manager = CapabilityManager(upstream, FakeRuntime(), AuditLog(Path(temporary) / "audit.ndjson"))
            payload = {"model": "behavioral-test-model"}
            self.assertTrue(manager.ensure_basic(payload).ok)
            self.assertTrue(manager.ensure_chat(payload).ok)
            result = manager.ensure_tools(payload, [tool()])
            self.assertEqual(result.capability, "NATIVE_TOOLS")
            self.assertEqual(result.mode, "NATIVE")
            self.assertEqual(manager.ensure_tools(payload, [tool()]).capability, "NATIVE_TOOLS")
            self.assertEqual(upstream.calls, 3)

    def test_capability_adapter_fallback(self):
        class FakeRuntime:
            def fingerprint(self):
                return "session-b", {"pid": "2"}

        class FakeUpstream:
            def json(self, payload, timeout=None):
                if payload.get("tools"):
                    return response("I cannot call a tool natively.")
                system = payload.get("messages", [{}])[0].get("content", "")
                if "AAG MODEL-NEUTRAL TOOL ADAPTER" in system:
                    nonce = payload["messages"][-1]["content"].split()[-1].rstrip(".")
                    return response(json.dumps({"tool_name": "aag_compatibility_noop", "arguments": {"nonce": nonce}}))
                return response("Normal human-readable chat works.")

        with tempfile.TemporaryDirectory() as temporary:
            manager = CapabilityManager(FakeUpstream(), FakeRuntime(), AuditLog(Path(temporary) / "audit.ndjson"))
            result = manager.ensure_tools({"model": "behavioral-test-model"}, [tool()])
            self.assertEqual(result.capability, "GENERIC_ADAPTER_TOOLS")
            self.assertEqual(result.mode, "GENERIC_ADAPTER")

    def test_chat_failure_blocks_tool_probe(self):
        class FakeRuntime:
            def fingerprint(self):
                return "session-c", {"pid": "3"}

        class FakeUpstream:
            def json(self, payload, timeout=None):
                return response("<unused20>")

        with tempfile.TemporaryDirectory() as temporary:
            manager = CapabilityManager(FakeUpstream(), FakeRuntime(), AuditLog(Path(temporary) / "audit.ndjson"))
            payload = {"model": "behavioral-test-model"}
            basic = manager.ensure_basic(payload)
            self.assertFalse(basic.ok)
            self.assertEqual(basic.capability, "BASIC_TEXT_INCOMPATIBLE")
            chat = manager.ensure_chat(payload)
            self.assertFalse(chat.ok)
            self.assertEqual(chat.code, "BASIC_TEXT_GATE_FAILED")
            with self.assertRaisesRegex(Exception, "not evaluated"):
                manager.ensure_tools(payload, [tool()])

    def test_ordinary_chat_failure_after_basic_sanity_blocks_tool_probe(self):
        class FakeRuntime:
            def fingerprint(self):
                return "session-d", {"pid": "4"}

        class FakeUpstream:
            def __init__(self):
                self.calls = 0

            def json(self, payload, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    return response("Basic readable text works.")
                return response("<tool_call>{}")

        with tempfile.TemporaryDirectory() as temporary:
            manager = CapabilityManager(FakeUpstream(), FakeRuntime(), AuditLog(Path(temporary) / "audit.ndjson"))
            payload = {"model": "behavioral-test-model"}
            self.assertTrue(manager.ensure_basic(payload).ok)
            chat = manager.ensure_chat(payload)
            self.assertFalse(chat.ok)
            self.assertEqual(chat.capability, "CHAT_INCOMPATIBLE")
            self.assertEqual(chat.code, "TOOL_CONTROL_TOKEN_LEAK")
            with self.assertRaisesRegex(Exception, "not evaluated"):
                manager.ensure_tools(payload, [tool()])

    def test_sane_chat_with_no_valid_tool_path_is_incompatible(self):
        class FakeRuntime:
            def fingerprint(self):
                return "session-e", {"pid": "5"}

        class FakeUpstream:
            def json(self, payload, timeout=None):
                return response("Readable prose that is not a valid tool call.")

        with tempfile.TemporaryDirectory() as temporary:
            manager = CapabilityManager(FakeUpstream(), FakeRuntime(), AuditLog(Path(temporary) / "audit.ndjson"))
            result = manager.ensure_tools({"model": "behavioral-test-model"}, [tool()])
            self.assertEqual(result.capability, "TOOL_INCOMPATIBLE")
            self.assertEqual(result.mode, "INCOMPATIBLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
