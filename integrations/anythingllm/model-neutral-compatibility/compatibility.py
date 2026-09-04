#!/usr/bin/env python3
"""Pure, model-neutral compatibility and Composer contracts.

This module contains no inference, network, process-control, or tool-execution
code. The HTTP boundary imports it for deterministic validation only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from composer_canonical import composer_canonical_json
from visual_atlas import AtlasError, get_visual_atlas


LAYER_VERSION = "aag-model-neutral-compatibility-v1.2"
MAX_TOOLS = 128
MAX_SCHEMA_BYTES = 262_144
MAX_CANONICAL_BYTES = 262_144
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RAW_UNUSED_RE = re.compile(r"<unused(?:_?token)?\d+>", re.IGNORECASE)
RAW_CONTROL_RE = re.compile(
    r"(?:<\|(?:tool_call|tool_response|turn|channel|think|bos|eos|pad|unk)\|?>|"
    r"<(?:/?tool_call|/?tool_response|tool_call\||tool_response\||\|tool|tool\|)>)",
    re.IGNORECASE,
)
GENERIC_INTERNAL_RE = re.compile(
    r"<\|(?:im_start|im_end|assistant|system|user|endoftext|start_header_id|end_header_id)\|>",
    re.IGNORECASE,
)


class CompatibilityError(ValueError):
    """A fail-closed compatibility contract violation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TextSanity:
    ok: bool
    code: str
    length: int
    sha256: str


@dataclass(frozen=True)
class CanonicalCall:
    tool_name: str
    arguments: dict[str, Any]
    repair_count: int


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def schema_hash(tools: list[dict[str, Any]]) -> str:
    return sha256_text(stable_json(normalize_tools(tools)))


def _has_pathological_repetition(text: str) -> bool:
    collapsed = re.sub(r"\s+", " ", text.strip())
    if re.search(r"(.)\1{47,}", collapsed, re.DOTALL):
        return True
    words = re.findall(r"\w+", collapsed.casefold(), flags=re.UNICODE)
    if len(words) >= 24 and Counter(words).most_common(1)[0][1] / len(words) >= 0.60:
        return True
    if len(words) >= 40:
        fourgrams = Counter(tuple(words[i : i + 4]) for i in range(len(words) - 3))
        if fourgrams and fourgrams.most_common(1)[0][1] >= 7:
            return True
    return False


def text_sanity(text: Any, *, allow_empty: bool = False) -> TextSanity:
    if not isinstance(text, str):
        return TextSanity(False, "NON_TEXT_OUTPUT", 0, sha256_text(repr(text)))
    digest = sha256_text(text)
    stripped = text.strip()
    if not stripped:
        return TextSanity(allow_empty, "EMPTY_ALLOWED" if allow_empty else "EMPTY_OUTPUT", 0, digest)
    if len(text.encode("utf-8", "replace")) > 2_000_000:
        return TextSanity(False, "OUTPUT_TOO_LARGE", len(text), digest)
    if RAW_UNUSED_RE.search(text):
        return TextSanity(False, "RAW_UNUSED_TOKEN", len(text), digest)
    if RAW_CONTROL_RE.search(text):
        return TextSanity(False, "TOOL_CONTROL_TOKEN_LEAK", len(text), digest)
    if GENERIC_INTERNAL_RE.search(text):
        return TextSanity(False, "INTERNAL_SPECIAL_TOKEN_LEAK", len(text), digest)
    if "\x00" in text or any(ord(char) < 9 for char in text):
        return TextSanity(False, "BINARY_CONTROL_OUTPUT", len(text), digest)
    if not any(char.isalnum() for char in text):
        return TextSanity(False, "UNUSABLE_NON_ALNUM_OUTPUT", len(text), digest)
    if _has_pathological_repetition(text):
        return TextSanity(False, "PATHOLOGICAL_REPETITION", len(text), digest)
    return TextSanity(True, "SANE_TEXT", len(text), digest)


def collect_visible_text(response: dict[str, Any]) -> list[str]:
    visible: list[str] = []
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise CompatibilityError("MALFORMED_UPSTREAM_RESPONSE", "Upstream response has no choices.")
    for choice in choices:
        if not isinstance(choice, dict):
            raise CompatibilityError("MALFORMED_UPSTREAM_RESPONSE", "Upstream choice is not an object.")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise CompatibilityError("MALFORMED_UPSTREAM_RESPONSE", "Upstream choice has no message.")
        for key in ("content", "reasoning_content", "reasoning"):
            value = message.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise CompatibilityError("MALFORMED_UPSTREAM_RESPONSE", f"{key} is not text.")
                visible.append(value)
    return visible


def validate_ordinary_response(response: dict[str, Any]) -> None:
    texts = collect_visible_text(response)
    nonempty = False
    for text in texts:
        result = text_sanity(text, allow_empty=True)
        if not result.ok:
            raise CompatibilityError(result.code, "The model emitted protocol-level or unusable output.")
        nonempty = nonempty or bool(text.strip())
    message = response["choices"][0]["message"]
    has_tool_calls = bool(message.get("tool_calls"))
    if not nonempty and not has_tool_calls:
        raise CompatibilityError("EMPTY_OUTPUT", "The model returned no usable text or tool call.")


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tool, dict) or tool.get("type") != "function":
        raise CompatibilityError("INVALID_TOOL_SCHEMA", "Every tool must be an OpenAI function tool.")
    function = tool.get("function")
    if not isinstance(function, dict):
        raise CompatibilityError("INVALID_TOOL_SCHEMA", "Tool function metadata is missing.")
    name = function.get("name")
    if not isinstance(name, str) or not TOOL_NAME_RE.fullmatch(name):
        raise CompatibilityError("INVALID_TOOL_SCHEMA", "Tool name is invalid.")
    description = function.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > 16_384:
        raise CompatibilityError("INVALID_TOOL_SCHEMA", "Tool description is invalid.")
    parameters = function.get("parameters", {"type": "object", "properties": {}})
    if not isinstance(parameters, (dict, bool)):
        raise CompatibilityError("INVALID_TOOL_SCHEMA", "Tool parameters must be a JSON Schema.")
    normalized = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
    if len(stable_json(normalized).encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise CompatibilityError("TOOL_SCHEMA_TOO_LARGE", "Tool schema exceeds the bounded limit.")
    return normalized


def normalize_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list) or not tools:
        raise CompatibilityError("INVALID_TOOL_SCHEMA", "At least one tool schema is required.")
    if len(tools) > MAX_TOOLS:
        raise CompatibilityError("TOO_MANY_TOOLS", "Tool count exceeds the bounded limit.")
    normalized = [_normalize_tool(tool) for tool in tools]
    names = [item["function"]["name"] for item in normalized]
    if len(names) != len(set(names)):
        raise CompatibilityError("DUPLICATE_TOOL_NAME", "Tool names must be unique.")
    return normalized


def _resolve_ref(schema: Any, root: Any) -> Any:
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    ref = schema["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise CompatibilityError("UNSUPPORTED_SCHEMA_REF", "Only local JSON Schema references are allowed.")
    current = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise CompatibilityError("INVALID_SCHEMA_REF", "JSON Schema reference cannot be resolved.")
        current = current[part]
    return current


def _matches_type(instance: Any, expected: str) -> bool:
    return {
        "null": instance is None,
        "boolean": isinstance(instance, bool),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(instance),
        "string": isinstance(instance, str),
        "array": isinstance(instance, list),
        "object": isinstance(instance, dict),
    }.get(expected, False)


def validate_json_schema(instance: Any, schema: Any, *, root: Any | None = None, path: str = "$") -> None:
    if schema is True:
        return
    if schema is False:
        raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is forbidden by schema.")
    if not isinstance(schema, dict):
        raise CompatibilityError("INVALID_TOOL_SCHEMA", f"Schema at {path} is not an object or boolean.")
    root = schema if root is None else root
    resolved = _resolve_ref(schema, root)
    if resolved is not schema:
        validate_json_schema(instance, resolved, root=root, path=path)
        return

    if "const" in schema and instance != schema["const"]:
        raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} does not match const.")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or instance not in enum:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is not an allowed enum value.")

    for keyword, expectation in (("allOf", "all"), ("anyOf", "any"), ("oneOf", "one")):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        if not isinstance(branches, list) or not branches:
            raise CompatibilityError("INVALID_TOOL_SCHEMA", f"{keyword} must be a non-empty array.")
        matches = 0
        for branch in branches:
            try:
                validate_json_schema(instance, branch, root=root, path=path)
                matches += 1
            except CompatibilityError as error:
                if error.code != "ARGUMENT_SCHEMA_MISMATCH":
                    raise
        if expectation == "all" and matches != len(branches):
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} does not satisfy allOf.")
        if expectation == "any" and matches == 0:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} does not satisfy anyOf.")
        if expectation == "one" and matches != 1:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} does not satisfy oneOf.")

    if "not" in schema:
        try:
            validate_json_schema(instance, schema["not"], root=root, path=path)
        except CompatibilityError as error:
            if error.code == "ARGUMENT_SCHEMA_MISMATCH":
                pass
            else:
                raise
        else:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} matches a forbidden schema.")

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not all(isinstance(item, str) for item in types) or not any(_matches_type(instance, item) for item in types):
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} has the wrong type.")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise CompatibilityError("INVALID_TOOL_SCHEMA", f"required at {path} is invalid.")
        missing = [name for name in required if name not in instance]
        if missing:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is missing required properties.")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise CompatibilityError("INVALID_TOOL_SCHEMA", f"properties at {path} is invalid.")
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_json_schema(value, properties[key], root=root, path=child_path)
            elif additional is False:
                raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{child_path} is not allowed.")
            elif isinstance(additional, dict) or isinstance(additional, bool):
                validate_json_schema(value, additional, root=root, path=child_path)
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if isinstance(min_properties, int) and len(instance) < min_properties:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} has too few properties.")
        if isinstance(max_properties, int) and len(instance) > max_properties:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} has too many properties.")

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} has too few items.")
        if isinstance(max_items, int) and len(instance) > max_items:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} has too many items.")
        if schema.get("uniqueItems") is True:
            serialized = [stable_json(item) for item in instance]
            if len(serialized) != len(set(serialized)):
                raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} items are not unique.")
        items = schema.get("items")
        if items is not None:
            for index, value in enumerate(instance):
                validate_json_schema(value, items, root=root, path=f"{path}[{index}]")

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is too short.")
        if isinstance(max_length, int) and len(instance) > max_length:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is too long.")
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise CompatibilityError("INVALID_TOOL_SCHEMA", f"pattern at {path} is invalid.")
            try:
                matched = re.search(pattern, instance)
            except re.error as error:
                raise CompatibilityError("INVALID_TOOL_SCHEMA", f"pattern at {path} is invalid.") from error
            if not matched:
                raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} does not match pattern.")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is below minimum.")
        if "maximum" in schema and instance > schema["maximum"]:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is above maximum.")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is below exclusiveMinimum.")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise CompatibilityError("ARGUMENT_SCHEMA_MISMATCH", f"{path} is above exclusiveMaximum.")


def tool_map(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized = normalize_tools(tools)
    return {tool["function"]["name"]: tool["function"]["parameters"] for tool in normalized}


def validate_argument_text_sanity(value: Any) -> None:
    """Reject protocol garbage in any nested tool argument before execution."""
    if isinstance(value, str):
        sanity = text_sanity(value, allow_empty=True)
        if not sanity.ok:
            raise CompatibilityError(sanity.code, "Tool arguments contain protocol-level or unusable output.")
    elif isinstance(value, dict):
        for item in value.values():
            validate_argument_text_sanity(item)
    elif isinstance(value, list):
        for item in value:
            validate_argument_text_sanity(item)


def _decode_json_object(text: str, *, allow_fence_repair: bool) -> tuple[dict[str, Any], int]:
    if len(text.encode("utf-8", "replace")) > MAX_CANONICAL_BYTES:
        raise CompatibilityError("CANONICAL_OUTPUT_TOO_LARGE", "Canonical tool output is too large.")
    candidate = text.strip()
    repair_count = 0
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        fence = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", candidate, flags=re.IGNORECASE | re.DOTALL)
        if not allow_fence_repair or not fence:
            raise CompatibilityError("CANONICAL_JSON_INVALID", "Canonical tool output is not strict JSON.")
        repair_count = 1
        try:
            value = json.loads(fence.group(1).strip())
        except json.JSONDecodeError as error:
            raise CompatibilityError("CANONICAL_JSON_INVALID", "The one bounded repair did not produce JSON.") from error
    if not isinstance(value, dict):
        raise CompatibilityError("CANONICAL_SHAPE_INVALID", "Canonical tool output must be one object.")
    return value, repair_count


def parse_canonical_call(text: str, tools: list[dict[str, Any]], *, allow_fence_repair: bool = True) -> CanonicalCall:
    sane = text_sanity(text)
    if not sane.ok:
        raise CompatibilityError(sane.code, "Canonical output contains protocol-level garbage.")
    value, repair_count = _decode_json_object(text, allow_fence_repair=allow_fence_repair)
    if set(value) != {"tool_name", "arguments"}:
        raise CompatibilityError("CANONICAL_SHAPE_INVALID", "Canonical object must contain only tool_name and arguments.")
    name = value["tool_name"]
    arguments = value["arguments"]
    schemas = tool_map(tools)
    if not isinstance(name, str) or name not in schemas:
        raise CompatibilityError("UNAUTHORIZED_TOOL", "Canonical tool name is not in the request's exact tool set.")
    if not isinstance(arguments, dict):
        raise CompatibilityError("CANONICAL_ARGUMENTS_INVALID", "Canonical arguments must be an object.")
    validate_argument_text_sanity(arguments)
    validate_json_schema(arguments, schemas[name])
    return CanonicalCall(name, arguments, repair_count)


def validate_native_tool_response(response: dict[str, Any], tools: list[dict[str, Any]]) -> list[CanonicalCall]:
    validate_ordinary_response(response)
    message = response["choices"][0]["message"]
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list) or len(raw_calls) != 1:
        raise CompatibilityError("NATIVE_TOOL_CALL_COUNT_INVALID", "Exactly one native tool call is required.")
    schemas = tool_map(tools)
    calls: list[CanonicalCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict) or raw_call.get("type") != "function":
            raise CompatibilityError("NATIVE_TOOL_CALL_INVALID", "Native tool call shape is invalid.")
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise CompatibilityError("NATIVE_TOOL_CALL_INVALID", "Native function payload is missing.")
        name = function.get("name")
        if not isinstance(name, str) or name not in schemas:
            raise CompatibilityError("UNAUTHORIZED_TOOL", "Native tool name is not in the request's exact tool set.")
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            raise CompatibilityError("NATIVE_ARGUMENTS_INVALID", "Native arguments must be a JSON string.")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as error:
            raise CompatibilityError("NATIVE_ARGUMENTS_INVALID", "Native arguments are not strict JSON.") from error
        if not isinstance(arguments, dict):
            raise CompatibilityError("NATIVE_ARGUMENTS_INVALID", "Native arguments must decode to an object.")
        validate_argument_text_sanity(arguments)
        validate_json_schema(arguments, schemas[name])
        calls.append(CanonicalCall(name, arguments, 0))
    return calls


def extract_native_candidate(response: dict[str, Any], tools: list[dict[str, Any]]) -> CanonicalCall:
    """Extract one authorized, strict-JSON native candidate without accepting it.

    This is used only as bounded input to the generic adapter after normal
    schema validation has rejected the native call.  Extraction is not
    authorization to execute: the repaired result still has to pass the full
    live schema, Composer trusted-intent validation, and the normal tool gate.
    """
    validate_ordinary_response(response)
    message = response["choices"][0]["message"]
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list) or len(raw_calls) != 1:
        raise CompatibilityError("NATIVE_TOOL_CALL_COUNT_INVALID", "Exactly one native tool call is required.")
    raw_call = raw_calls[0]
    if not isinstance(raw_call, dict) or raw_call.get("type") != "function":
        raise CompatibilityError("NATIVE_TOOL_CALL_INVALID", "Native tool call shape is invalid.")
    function = raw_call.get("function")
    if not isinstance(function, dict):
        raise CompatibilityError("NATIVE_TOOL_CALL_INVALID", "Native function payload is missing.")
    name = function.get("name")
    schemas = tool_map(tools)
    if not isinstance(name, str) or name not in schemas:
        raise CompatibilityError("UNAUTHORIZED_TOOL", "Native tool name is not in the request's exact tool set.")
    raw_arguments = function.get("arguments")
    if not isinstance(raw_arguments, str):
        raise CompatibilityError("NATIVE_ARGUMENTS_INVALID", "Native arguments must be a JSON string.")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        raise CompatibilityError("NATIVE_ARGUMENTS_INVALID", "Native arguments are not strict JSON.") from error
    if not isinstance(arguments, dict):
        raise CompatibilityError("NATIVE_ARGUMENTS_INVALID", "Native arguments must decode to an object.")
    validate_argument_text_sanity(arguments)
    return CanonicalCall(name, arguments, 0)


def adapter_instruction(tools: list[dict[str, Any]]) -> str:
    normalized = normalize_tools(tools)
    available = [
        {
            "name": tool["function"]["name"],
            "description": tool["function"]["description"],
            "parameters": tool["function"]["parameters"],
        }
        for tool in normalized
    ]
    return (
        "AAG MODEL-NEUTRAL TOOL ADAPTER V1.\n"
        "This is a tool-selection turn. Do not answer the user's task and do not invent a tool. "
        "Select exactly one function from AVAILABLE_TOOLS_JSON. Return exactly one JSON object with "
        "exactly these keys: {\"tool_name\":\"allowed name\",\"arguments\":{...}}. "
        "Arguments must satisfy that function's JSON Schema. No markdown, code fences, comments, prose, "
        "special tokens, XML, or additional keys.\nAVAILABLE_TOOLS_JSON="
        + stable_json(available)
    )


def _flatten_message(message: dict[str, Any]) -> dict[str, str]:
    role = message.get("role")
    content = message.get("content")
    if role not in {"system", "user", "assistant", "tool"}:
        raise CompatibilityError("INVALID_MESSAGE_ROLE", "Unsupported chat role.")
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                fragments.append(item["text"])
            else:
                raise CompatibilityError("UNSUPPORTED_MESSAGE_CONTENT", "Adapter supports text message parts only.")
        content = "\n".join(fragments)
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise CompatibilityError("UNSUPPORTED_MESSAGE_CONTENT", "Adapter message content must be text.")
    if role == "tool":
        tool_id = message.get("tool_call_id", "unknown")
        return {"role": "user", "content": f"Result returned by the previously requested tool ({tool_id}):\n{content}"}
    if role == "assistant" and message.get("tool_calls"):
        summaries: list[str] = []
        for call in message["tool_calls"]:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            summaries.append(
                "Previously requested tool "
                + str(function.get("name", "unknown"))
                + " with arguments "
                + str(function.get("arguments", "{}"))
            )
        content = "\n".join(filter(None, [content, *summaries]))
    return {"role": role, "content": content}


def build_adapter_payload(payload: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise CompatibilityError("INVALID_MESSAGES", "Chat messages must be a non-empty array.")
    flattened = [_flatten_message(message) for message in messages if isinstance(message, dict)]
    if len(flattened) != len(messages):
        raise CompatibilityError("INVALID_MESSAGES", "Every chat message must be an object.")
    instruction = adapter_instruction(tools)
    if flattened and flattened[0]["role"] == "system":
        flattened[0] = {"role": "system", "content": flattened[0]["content"] + "\n\n" + instruction}
    else:
        flattened.insert(0, {"role": "system", "content": instruction})
    adapted = deepcopy(payload)
    adapted["messages"] = flattened
    adapted.pop("tools", None)
    adapted.pop("tool_choice", None)
    adapted.pop("parallel_tool_calls", None)
    adapted.pop("response_format", None)
    adapted["stream"] = False
    adapted["temperature"] = 0
    adapted["max_tokens"] = min(int(payload.get("max_tokens", 512) or 512), 512)
    return adapted


def build_candidate_adapter_payload(
    payload: dict[str, Any],
    tools: list[dict[str, Any]],
    candidate: CanonicalCall,
    *,
    preserve_arguments: dict[str, Any] | None = None,
    required_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact, model-neutral repair turn from a rejected native call.

    The original professional system prompt is not compacted or rewritten.
    It has already produced the candidate.  This turn supplies the actual live
    schemas plus that candidate only, and asks the same model to normalize it.
    Callers can require selected generated arguments (notably the professional
    creative prompt) to survive byte-for-byte; enforcement is deterministic in
    ``validate_preserved_arguments`` after strict parsing.
    """
    normalized = normalize_tools(tools)
    schemas = tool_map(normalized)
    if candidate.tool_name not in schemas:
        raise CompatibilityError("UNAUTHORIZED_TOOL", "Candidate tool is not in the request's exact tool set.")
    preserved = preserve_arguments or {}
    required = required_arguments or {}
    if not isinstance(preserved, dict) or any(
        key not in candidate.arguments or candidate.arguments[key] != value for key, value in preserved.items()
    ):
        raise CompatibilityError("PRESERVED_ARGUMENT_INVALID", "Preserved adapter arguments are not from the native candidate.")
    if not isinstance(required, dict):
        raise CompatibilityError("REQUIRED_ARGUMENT_INVALID", "Required adapter arguments are invalid.")
    instruction = (
        adapter_instruction(normalized)
        + "\nThe supplied object is a rejected native candidate, not trusted output. Normalize it against the live "
        "schema. Do not redo the user's creative task. Preserve every key in PRESERVE_ARGUMENTS_JSON exactly, "
        "including all characters and length. Every key in REQUIRED_ARGUMENTS_JSON must have exactly the supplied "
        "value. Add, remove, or correct only other fields required for schema validity."
    )
    user = (
        "REJECTED_NATIVE_CANDIDATE_JSON="
        + stable_json({"tool_name": candidate.tool_name, "arguments": candidate.arguments})
        + "\nPRESERVE_ARGUMENTS_JSON="
        + stable_json(preserved)
        + "\nREQUIRED_ARGUMENTS_JSON="
        + stable_json(required)
    )
    adapted = {
        "model": payload.get("model", "local-model"),
        "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": user}],
        "stream": False,
        "temperature": 0,
        "max_tokens": min(int(payload.get("max_tokens", 512) or 512), 512),
    }
    return adapted


def normalize_candidate_with_trusted_arguments(
    candidate: CanonicalCall,
    tools: list[dict[str, Any]],
    required_arguments: dict[str, Any],
    *,
    omit_arguments: set[str] | None = None,
) -> CanonicalCall:
    """Deterministically project a strict candidate onto one live schema.

    This is deliberately narrower than an LLM repair. It is valid only when a
    separate trusted-intent envelope supplies every changed value. Unknown
    fields are removed according to ``additionalProperties: false``; no value
    is inferred. The result is still subjected to the complete live schema.
    """
    schemas = tool_map(tools)
    schema = schemas.get(candidate.tool_name)
    if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise CompatibilityError("DETERMINISTIC_NORMALIZATION_UNAVAILABLE", "Live schema cannot be safely projected.")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise CompatibilityError("DETERMINISTIC_NORMALIZATION_UNAVAILABLE", "Live schema properties are unavailable.")
    if not isinstance(required_arguments, dict) or any(key not in properties for key in required_arguments):
        raise CompatibilityError("REQUIRED_ARGUMENT_INVALID", "Trusted arguments are not fields in the live schema.")
    omitted = omit_arguments or set()
    if not isinstance(omitted, set) or any(not isinstance(key, str) for key in omitted):
        raise CompatibilityError("OMITTED_ARGUMENT_INVALID", "Omitted adapter arguments are invalid.")
    arguments = {
        key: value
        for key, value in candidate.arguments.items()
        if key in properties and key not in omitted
    }
    arguments.update(required_arguments)
    validate_argument_text_sanity(arguments)
    validate_json_schema(arguments, schema)
    return CanonicalCall(candidate.tool_name, arguments, 0)


def normalize_composer_candidate(
    candidate: CanonicalCall,
    tools: list[dict[str, Any]],
    intent: dict[str, Any],
) -> CanonicalCall:
    """Combine signed Composer dry fields with model-authored creative text.

    The candidate must already be one authorized strict-JSON call. This
    function never invents or rewrites a creative prompt. It accepts only the
    single-image or batch contract implied by the signed count, projects onto
    the corresponding live schema, and then runs that full schema.
    """
    operation = intent.get("operation")
    count = intent.get("count")
    aspect = intent.get("aspect_ratio")
    quality = intent.get("quality")
    final_output_quality = intent.get("final_output_quality", "standard")
    live_schema = tool_map(tools).get(candidate.tool_name, {})
    live_properties = live_schema.get("properties", {}) if isinstance(live_schema, dict) else {}
    supports_final_output_quality = "final_output_quality" in live_properties
    atlas_plan = intent.get("knowledge_modules", {}).get("visual_atlas")
    atlas_marker = None
    if isinstance(atlas_plan, dict) and atlas_plan.get("used") and str(atlas_plan.get("mode", "")).startswith("manual_"):
        selections = atlas_plan.get("selections")
        if not isinstance(selections, list) or len(selections) != 1:
            raise CompatibilityError("COMPOSER_INTENT_INVALID", "Manual Visual Atlas intent is invalid.")
        selected = selections[0]
        atlas_marker = (
            "AAG_ATLAS_SELECTION_V1 mode=" + str(atlas_plan["mode"])
            + " family=" + str(selected.get("family_id", ""))
            + " subfamily=" + str(selected.get("subfamily_id", ""))
        )
        if "request" not in live_properties:
            raise CompatibilityError("REQUIRED_ARGUMENT_INVALID", "The live image tool cannot carry the signed Visual Atlas selection.")
    if final_output_quality != "standard" and not supports_final_output_quality:
        raise CompatibilityError(
            "REQUIRED_ARGUMENT_INVALID",
            "The live image tool does not expose the signed final-output quality field.",
        )
    source_index = intent.get("source_index", "none")
    seed = intent.get("seed", "auto")
    if operation == "generate" and isinstance(count, int) and count >= 2:
        if candidate.tool_name != "aag-image-batch":
            raise CompatibilityError("COMPOSER_INTENT_MISMATCH", "Composer batch requires the batch tool.")
        items = candidate.arguments.get("items")
        if not isinstance(items, list) or len(items) != count:
            raise CompatibilityError("COMPOSER_INTENT_MISMATCH", "Composer batch requires exactly the signed item count.")
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("prompt"), str):
                raise CompatibilityError("COMPOSER_INTENT_MISMATCH", "Every batch item requires a model-authored prompt.")
            normalized_item = {
                key: value
                for key, value in item.items()
                if key in {"prompt", "aspect_ratio", "width", "height", "seed"}
            }
            normalized_item.pop("width", None)
            normalized_item.pop("height", None)
            if aspect != "auto":
                normalized_item["aspect_ratio"] = aspect
            normalized_items.append(normalized_item)
        required = {
            "operation": "multi_generate",
            "count": count,
            "items": normalized_items,
        }
        if atlas_marker:
            required["request"] = atlas_marker
        if supports_final_output_quality:
            required["final_output_quality"] = final_output_quality
        if quality != "auto":
            required["quality"] = quality
        call = normalize_candidate_with_trusted_arguments(candidate, tools, required)
        validate_composer_intent_call(intent, call)
        return call

    if candidate.tool_name != "aag-image-task":
        raise CompatibilityError("COMPOSER_INTENT_MISMATCH", "Composer single operation requires the task tool.")
    required = {
        "operation": operation,
        "source_policy": intent.get("source_policy"),
        "preservation": intent.get("preservation"),
    }
    if atlas_marker:
        required["request"] = atlas_marker
    if supports_final_output_quality:
        required["final_output_quality"] = final_output_quality
    if quality != "auto":
        required["quality"] = quality
    omitted = {"width", "height"}
    if aspect != "auto":
        required["aspect_ratio"] = aspect
    if count != 1:
        required["count"] = count
    else:
        omitted.add("count")
    if operation == "upscale":
        if intent.get("scale") != "auto":
            required["scale"] = intent.get("scale")
    else:
        omitted.add("scale")
    if source_index == "none":
        omitted.add("source_index")
    else:
        required["source_index"] = source_index
    if seed != "auto":
        required["seed"] = seed
    call = normalize_candidate_with_trusted_arguments(
        candidate,
        tools,
        required,
        omit_arguments=omitted,
    )
    prompt = candidate.arguments.get("prompt")
    if not isinstance(prompt, str) or call.arguments.get("prompt") != prompt:
        raise CompatibilityError("ADAPTER_PRESERVATION_MISMATCH", "Composer normalization changed the professional prompt.")
    validate_composer_intent_call(intent, call)
    return call


def validate_preserved_arguments(call: CanonicalCall, preserved: dict[str, Any]) -> None:
    if not isinstance(preserved, dict):
        raise CompatibilityError("PRESERVED_ARGUMENT_INVALID", "Preserved arguments are invalid.")
    for key, value in preserved.items():
        if key not in call.arguments or call.arguments[key] != value:
            raise CompatibilityError(
                "ADAPTER_PRESERVATION_MISMATCH",
                f"Generic adapter changed protected candidate argument {key}.",
            )


def validate_required_arguments(call: CanonicalCall, required: dict[str, Any]) -> None:
    if not isinstance(required, dict):
        raise CompatibilityError("REQUIRED_ARGUMENT_INVALID", "Required arguments are invalid.")
    for key, value in required.items():
        if key not in call.arguments or call.arguments[key] != value:
            raise CompatibilityError(
                "ADAPTER_REQUIRED_ARGUMENT_MISMATCH",
                f"Generic adapter did not preserve authoritative argument {key}.",
            )


def canonical_to_openai_response(response: dict[str, Any], call: CanonicalCall) -> dict[str, Any]:
    converted = deepcopy(response)
    message = converted["choices"][0]["message"]
    message["content"] = None
    message.pop("reasoning_content", None)
    message.pop("reasoning", None)
    message["tool_calls"] = [
        {
            "id": "call_aag_" + uuid.uuid4().hex[:24],
            "type": "function",
            "function": {
                "name": call.tool_name,
                "arguments": stable_json(call.arguments),
            },
        }
    ]
    converted["choices"][0]["finish_reason"] = "tool_calls"
    converted["aag_compatibility"] = {
        "mode": "GENERIC_ADAPTER",
        "repair_count": call.repair_count,
        "layer_version": LAYER_VERSION,
    }
    return converted


def openai_sse_events(response: dict[str, Any]) -> Iterable[bytes]:
    response_id = str(response.get("id") or ("chatcmpl-aag-" + uuid.uuid4().hex))
    model = str(response.get("model") or "local-model")
    created = int(response.get("created") or time.time())
    message = response["choices"][0]["message"]
    base = {"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model}
    role = {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    yield ("data: " + stable_json(role) + "\n\n").encode("utf-8")
    tool_calls = message.get("tool_calls")
    if tool_calls:
        call = tool_calls[0]
        delta = {
            **base,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call.get("id"),
                                "type": "function",
                                "function": call.get("function"),
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        finish_reason = "tool_calls"
    else:
        delta = {
            **base,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": message.get("content") or ""},
                    "finish_reason": None,
                }
            ],
        }
        finish_reason = response["choices"][0].get("finish_reason") or "stop"
    yield ("data: " + stable_json(delta) + "\n\n").encode("utf-8")
    finish = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
    yield ("data: " + stable_json(finish) + "\n\n").encode("utf-8")
    yield b"data: [DONE]\n\n"


COMPOSER_TAXONOMY_PATH = Path(__file__).resolve().parent / "composer" / "visual-taxonomy.json"
COMPOSER_VERSION = "1.1.0"


def _load_composer_taxonomy() -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    try:
        taxonomy = json.loads(COMPOSER_TAXONOMY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Composer taxonomy is unavailable or invalid.") from error
    families = taxonomy.get("families") if isinstance(taxonomy, dict) else None
    if not isinstance(families, list) or not families:
        raise RuntimeError("Composer taxonomy has no families.")
    family_labels: dict[str, str] = {"auto": "Auto"}
    subfamilies: dict[str, dict[str, str]] = {"auto": {"auto": "Auto"}}
    seen_pairs: set[str] = set()
    allowed_classifications = {"ACTIVE", "MODEL_HINT_ONLY", "UNSUPPORTED_IN_V1_1"}
    for family in families:
        if not isinstance(family, dict):
            raise RuntimeError("Composer taxonomy family is invalid.")
        family_id, label = family.get("id"), family.get("label")
        if (
            not isinstance(family_id, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", family_id)
            or family_id in family_labels
            or not isinstance(label, str)
            or not label
            or family.get("classification") not in allowed_classifications
            or family.get("subfamily_classification") not in allowed_classifications
        ):
            raise RuntimeError("Composer taxonomy family metadata is invalid.")
        family_labels[family_id] = label
        entries = family.get("subfamilies")
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("Composer taxonomy family has no subfamilies.")
        subfamilies[family_id] = {"auto": "Auto"}
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("Composer taxonomy subfamily is invalid.")
            entry_id, entry_label = entry.get("id"), entry.get("label")
            pair = f"{family_id}/{entry_id}"
            if (
                not isinstance(entry_id, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entry_id)
                or entry_id in subfamilies[family_id]
                or pair in seen_pairs
                or not isinstance(entry_label, str)
                or not entry_label
            ):
                raise RuntimeError("Composer taxonomy subfamily metadata is invalid.")
            seen_pairs.add(pair)
            subfamilies[family_id][entry_id] = entry_label
    return {"raw": taxonomy, "families": family_labels}, subfamilies


COMPOSER_TAXONOMY, COMPOSER_SUBFAMILIES = _load_composer_taxonomy()
COMPOSER_ENUMS = {
    "operation": {"generate", "transform", "upscale"},
    "edit_mode": {"not_applicable", "preserve", "restyle"},
    "reference_purpose": {"not_applicable", "identity", "general_visual"},
    "reference_source": {"not_applicable", "current_upload", "latest_thread_artifact"},
    "visual_family": set(COMPOSER_TAXONOMY["families"]),
    "aspect_ratio": {"auto", "1:1", "16:9", "9:16", "4:3", "3:2", "landscape", "portrait"},
    "quality": {"auto", "fast", "balanced", "quality"},
    "final_output_quality": {"standard", "enhanced_2x"},
    "source_policy": {"auto", "current_attachment", "previous_artifact"},
    "preservation": {"none", "subject", "identity"},
    "output_purpose": {"auto", "general", "wallpaper", "social", "poster", "product_commercial", "presentation", "print", "thumbnail", "banner"},
    "background": {"auto", "preserve_source", "solid_plain", "scene_background", "isolated_no_background"},
    "visible_text": {"auto", "none", "model_decides"},
    "batch_relationship": {"auto", "independent", "same_concept_different_compositions", "coordinated_series", "variations"},
    "atlas_selection_mode": {"auto", "manual_taxonomy", "manual_browse"},
}

COMPOSER_DEFAULTS = {
    "operation": "generate",
    "edit_mode": "not_applicable",
    "reference_purpose": "not_applicable",
    "reference_source": "not_applicable",
    "visual_family": "auto",
    "aspect_ratio": "auto",
    "quality": "auto",
    "final_output_quality": "standard",
    "source_policy": "auto",
    "preservation": "none",
    "output_purpose": "auto",
    "background": "auto",
    "visible_text": "auto",
    "batch_relationship": "auto",
    "atlas_selection_mode": "auto",
}
COMPOSER_COUNT_MIN = 1
COMPOSER_COUNT_MAX = 10
COMPOSER_SOURCE_MAX = 8
COMPOSER_ATTACHMENT_TOTAL_MAX = 22_000_000
COMPOSER_INPUT_KEYS = {
    "mode", "free_text", "operation", "edit_mode", "visual_family", "visual_subfamily", "aspect_ratio", "count", "quality", "final_output_quality",
    "source_policy", "source_index", "preservation", "scale", "seed", "output_purpose", "background", "visible_text",
    "batch_relationship", "reference_purpose", "reference_source", "reference_artifact_sha256",
    "source_instruction", "attachments", "atlas_selection_mode",
}
COMPOSER_INTENT_PREFIX = "AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1="
COMPOSER_USER_REQUEST_PREFIX = "USER_CREATIVE_DIRECTION=\n"
COMPOSER_SIGNATURE_PREFIX = "AAG_COMPOSER_INTENT_SIGNATURE_V1="


def _composer_semantics(
    values: dict[str, Any],
    *,
    count: int,
    subfamily: str,
    source_index: int | str,
    scale: int | str,
    seed: int | str,
    source_instruction: str,
) -> dict[str, Any]:
    """Project control state into applicable constraints and discretion."""
    operation = values["operation"]
    reference_creation = (
        operation == "transform"
        and values["reference_purpose"] != "not_applicable"
    )
    applicable = {
        "generate": [
            "visual_family", "visual_subfamily", "aspect_ratio", "quality",
            "output_purpose", "background", "visible_text",
        ]
        + (["batch_relationship"] if count >= 2 else [])
        + (["seed"] if count == 1 else []),
        "transform": ["edit_mode", "source_policy", "source_index", "preservation"],
        "upscale": ["source_policy", "source_index", "scale"],
    }[operation]
    control = {
        **values,
        "visual_subfamily": subfamily,
        "source_index": source_index,
        "scale": scale,
        "seed": seed,
        "source_instruction": source_instruction.strip(),
    }
    if reference_creation:
        applicable = [
            "reference_purpose", "reference_source", "source_policy",
            "source_index", "preservation", "visual_family",
            "visual_subfamily", "aspect_ratio", "quality", "output_purpose",
            "background", "visible_text",
        ]
    elif operation == "transform" and values["edit_mode"] == "restyle":
        applicable += ["visual_family", "visual_subfamily"]
    if operation == "transform" and not reference_creation:
        # Keep the external fallback compatible with earlier explicit Edit
        # controls, but never turn their Auto values into model requirements.
        for field in (
            "aspect_ratio", "quality", "seed", "output_purpose", "background",
            "visible_text", "source_instruction",
        ):
            value = control[field]
            if value not in {"auto", "none", ""} or (
                field == "visible_text" and value == "none"
            ):
                applicable.append(field)
    explicit: dict[str, Any] = {"operation": operation, "count": count}
    if reference_creation:
        explicit["composer_operation"] = "create_from_reference"
    discretion: list[str] = []
    for field in applicable:
        value = control[field]
        if value == "auto" or (field == "source_instruction" and value == ""):
            discretion.append(field)
        elif value not in {"none", ""} or field == "visible_text":
            explicit[field] = value
    semantics = {
        "explicit_constraints": explicit,
        "model_discretion_fields": sorted(discretion),
    }
    if reference_creation:
        identity = values["reference_purpose"] == "identity"
        semantics["reference_creation"] = {
            "mode": "create_from_reference",
            "new_scene_generation_authorized": True,
            "preserve_person_identity": identity,
            "preserve_general_visual_reference": not identity,
            "preserve_source_composition_by_default": False,
            "tool_operation": "transform",
        }
    elif operation == "transform":
        restyle = values["edit_mode"] == "restyle"
        semantics["source_preservation"] = {
            "mode": "restyle_image" if restyle else "preserve_current_appearance",
            "preserve_unspecified_source_properties": True,
            "style_change_authorized": restyle,
        }
    elif operation == "upscale":
        semantics["source_preservation"] = {
            "mode": "upscale_preserve_appearance",
            "preserve_unspecified_source_properties": True,
            "style_change_authorized": False,
        }
    return semantics


def compose_request(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(data, dict):
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Composer payload must be an object.")
    unexpected = sorted(set(data) - COMPOSER_INPUT_KEYS)
    if unexpected:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Composer payload contains an unsupported field.")
    mode = data.get("mode", "auto")
    if mode not in {"auto", "advanced"}:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Composer mode is invalid.")
    free_text = data.get("free_text", "")
    if not isinstance(free_text, str) or not free_text.strip() or len(free_text) > 12_000:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Free text is required and must be bounded.")
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > COMPOSER_SOURCE_MAX:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "At most eight current source images are allowed.")
    clean_attachments: list[dict[str, str]] = []
    attachment_chars = 0
    for item in attachments:
        if not isinstance(item, dict):
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Attachment is invalid.")
        name, mime, content = item.get("name"), item.get("mime"), item.get("contentString")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._ -]{1,128}", name):
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Attachment name is invalid.")
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Attachment type is not allowed.")
        if not isinstance(content, str) or not content.startswith(f"data:{mime};base64,"):
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Attachment data is invalid or too large.")
        attachment_chars += len(content)
        if attachment_chars > COMPOSER_ATTACHMENT_TOTAL_MAX:
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Current source images exceed the total size limit.")
        clean_attachments.append({"name": name, "mime": mime, "contentString": content})

    if mode == "auto":
        return free_text, clean_attachments

    values: dict[str, Any] = {}
    for field, allowed in COMPOSER_ENUMS.items():
        if field == "edit_mode" and field not in data:
            # Legacy/external Composer clients predate the closed Edit-mode
            # selector. Explicit legacy style means Restyle; otherwise Edit
            # safely defaults to source preservation.
            if data.get("operation", "generate") == "transform":
                value = (
                    "restyle"
                    if data.get("visual_family", "auto") != "auto"
                    or data.get("visual_subfamily", "auto") != "auto"
                    else "preserve"
                )
            else:
                value = "not_applicable"
        else:
            value = data.get(field, COMPOSER_DEFAULTS[field])
        if value not in allowed:
            raise CompatibilityError("COMPOSER_INPUT_INVALID", f"Composer field {field} is invalid.")
        values[field] = value
    count = data.get("count", 1)
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not COMPOSER_COUNT_MIN <= count <= COMPOSER_COUNT_MAX
    ):
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Image count is not supported.")
    scale = data.get("scale", "none")
    if scale not in {"none", "auto", 2, 3, 4}:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale factor is not supported.")
    seed = data.get("seed", "auto")
    if seed != "auto" and (
        not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2_147_483_647
    ):
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Seed is outside the supported range.")
    source_index = data.get("source_index", "none")
    if source_index != "none" and (
        not isinstance(source_index, int)
        or isinstance(source_index, bool)
        or not 1 <= source_index <= COMPOSER_SOURCE_MAX
    ):
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Source selection is invalid.")
    subfamily = data.get("visual_subfamily", "auto")
    source_instruction = data.get("source_instruction", "")
    reference_artifact_sha256 = data.get("reference_artifact_sha256", "none")
    if not isinstance(subfamily, str) or subfamily not in COMPOSER_SUBFAMILIES[values["visual_family"]]:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Visual subfamily is invalid.")
    if not isinstance(source_instruction, str) or len(source_instruction) > 1000:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Source instruction is invalid.")
    if reference_artifact_sha256 != "none" and not (
        isinstance(reference_artifact_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", reference_artifact_sha256)
    ):
        raise CompatibilityError("REFERENCE_IMAGE_INVALID", "Reference artifact integrity metadata is invalid.")
    reference_creation = values["reference_purpose"] != "not_applicable"
    if values["operation"] == "generate":
        if values["edit_mode"] != "not_applicable":
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Create and Batch do not accept an Edit mode.")
        if (
            reference_creation
            or values["reference_source"] != "not_applicable"
            or reference_artifact_sha256 != "none"
        ):
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Create and Batch do not accept reference controls.")
        if (
            clean_attachments
            or values["source_policy"] != "auto"
            or values["preservation"] != "none"
            or scale != "none"
            or source_index != "none"
        ):
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Generate requires no source, preservation none, and no upscale factor.")
        if count >= 2 and seed != "auto":
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Batch seeds are item-specific and remain in future expert mode.")
    elif values["operation"] == "transform":
        if reference_creation:
            if values["edit_mode"] != "not_applicable":
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Create from reference does not accept an Edit mode.")
            expected_preservation = (
                "identity" if values["reference_purpose"] == "identity" else "subject"
            )
            if values["preservation"] != expected_preservation:
                raise CompatibilityError(
                    "COMPOSER_INPUT_INVALID",
                    "Reference purpose does not match the governed preservation route.",
                )
            if values["source_policy"] != "current_attachment":
                raise CompatibilityError(
                    "COMPOSER_INPUT_INVALID",
                    "Create from reference requires a materialized current-turn reference.",
                )
            if values["reference_source"] not in {
                "current_upload", "latest_thread_artifact"
            }:
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Reference source is invalid.")
        else:
            if values["reference_source"] != "not_applicable" or reference_artifact_sha256 != "none":
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Edit does not accept reference-creation controls.")
            if values["edit_mode"] not in {"preserve", "restyle"}:
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Edit requires a supported Edit mode.")
            if values["edit_mode"] == "preserve" and (
                values["visual_family"] != "auto" or subfamily != "auto"
            ):
                raise CompatibilityError(
                    "COMPOSER_INPUT_INVALID",
                    "Preserve-current-appearance Edit cannot include style controls.",
                )
        if values["source_policy"] not in {"current_attachment", "previous_artifact"} or values["preservation"] not in {"subject", "identity"}:
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Edit requires an approved source and supported preservation.")
        if scale != "none" or count != 1:
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Edit supports one output and no upscale factor.")
        if values["source_policy"] == "previous_artifact":
            if clean_attachments or source_index != "none" or values["preservation"] == "identity":
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Previous-artifact edit supports subject preservation without current uploads.")
        else:
            if not clean_attachments:
                if reference_creation:
                    raise CompatibilityError("REFERENCE_IMAGE_MISSING", "Create from reference requires a source image.")
                raise CompatibilityError("COMPOSER_SOURCE_REQUIRED", "Edit requires at least one current source image.")
            if source_index == "none":
                source_index = 1 if len(clean_attachments) == 1 else source_index
            if source_index == "none" or source_index > len(clean_attachments):
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Select one available current source image.")
            if values["preservation"] == "identity" and len(clean_attachments) != 1:
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Person identity preservation requires exactly one current source image.")
        if reference_creation:
            if values["reference_purpose"] == "identity" and len(clean_attachments) != 1:
                raise CompatibilityError("REFERENCE_IMAGE_INVALID", "Person identity requires exactly one reference image.")
            if values["reference_source"] == "latest_thread_artifact":
                if len(clean_attachments) != 1 or source_index != 1 or reference_artifact_sha256 == "none":
                    raise CompatibilityError("REFERENCE_IMAGE_INVALID", "Latest-thread reference binding is incomplete.")
                try:
                    encoded = clean_attachments[0]["contentString"].split(",", 1)[1]
                    reference_bytes = base64.b64decode(encoded, validate=True)
                except (IndexError, ValueError, binascii.Error) as error:
                    raise CompatibilityError("REFERENCE_IMAGE_INVALID", "Latest-thread reference bytes are invalid.") from error
                if hashlib.sha256(reference_bytes).hexdigest() != reference_artifact_sha256:
                    raise CompatibilityError("REFERENCE_IMAGE_INVALID", "Latest-thread reference failed its integrity check.")
            elif reference_artifact_sha256 != "none":
                raise CompatibilityError("REFERENCE_IMAGE_INVALID", "Uploaded reference cannot claim a thread-artifact hash.")
    else:
        if values["edit_mode"] != "not_applicable":
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale does not accept an Edit mode.")
        if (
            reference_creation
            or values["reference_source"] != "not_applicable"
            or reference_artifact_sha256 != "none"
        ):
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale does not accept reference-creation controls.")
        if values["source_policy"] not in {"current_attachment", "previous_artifact"} or values["preservation"] != "none":
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale requires an approved source and preservation none.")
        if scale not in {"auto", 2, 3, 4} or count != 1 or seed != "auto":
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale requires one output and a supported factor.")
        if values["source_policy"] == "previous_artifact":
            if clean_attachments or source_index != "none":
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Previous-artifact upscale cannot include current uploads.")
        else:
            if not clean_attachments:
                raise CompatibilityError("COMPOSER_SOURCE_REQUIRED", "Upscale requires at least one current source image.")
            if source_index == "none":
                source_index = 1 if len(clean_attachments) == 1 else source_index
            if source_index == "none" or source_index > len(clean_attachments):
                raise CompatibilityError("COMPOSER_INPUT_INVALID", "Select one available current source image.")

    if count == 1 and values["batch_relationship"] != "auto":
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Batch relationship applies only to Batch / series.")
    if values["operation"] == "upscale" and any(
        values[field] != "auto" for field in ("output_purpose", "background", "visible_text")
    ):
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale does not accept creative appearance controls.")
    if values["operation"] == "upscale" and (
        values["visual_family"] != "auto"
        or subfamily != "auto"
        or values["aspect_ratio"] != "auto"
        or values["quality"] != "auto"
        or values["batch_relationship"] != "auto"
        or bool(source_instruction.strip())
    ):
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale contains settings that do not apply.")
    if values["operation"] == "upscale" and values["final_output_quality"] != "standard":
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Upscale cannot add a second final-output enhancement.")
    if values["background"] == "preserve_source" and values["operation"] != "transform":
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Preserve-source background applies only to Edit / transform.")

    atlas_mode = values["atlas_selection_mode"]
    has_exact_atlas_style = values["visual_family"] != "auto" and subfamily != "auto"
    if atlas_mode != "auto" and not has_exact_atlas_style:
        raise CompatibilityError("COMPOSER_INPUT_INVALID", "Manual Visual Atlas mode requires an exact family and subfamily.")
    if atlas_mode == "auto" and has_exact_atlas_style:
        # Backward-compatible advanced clients already use exact taxonomy
        # selection as a deliberate manual choice.
        atlas_mode = "manual_taxonomy"
    try:
        atlas_plan = get_visual_atlas().select(
            free_text,
            mode=atlas_mode,
            family_id=values["visual_family"] if has_exact_atlas_style else None,
            subfamily_id=subfamily if has_exact_atlas_style else None,
            operation=values["operation"],
            preservation=values["preservation"],
        )
    except AtlasError as error:
        raise CompatibilityError("COMPOSER_ATLAS_INVALID", str(error)) from error

    semantics = _composer_semantics(
        values,
        count=count,
        subfamily=subfamily,
        source_index=source_index,
        scale=scale,
        seed=seed,
        source_instruction=source_instruction,
    )
    structured = {
        "operation": values["operation"],
        "edit_mode": values["edit_mode"],
        "reference_purpose": values["reference_purpose"],
        "reference_source": values["reference_source"],
        "reference_artifact_sha256": reference_artifact_sha256,
        "aspect_ratio": values["aspect_ratio"],
        "count": count,
        "quality": values["quality"],
        "final_output_quality": values["final_output_quality"],
        "source_policy": values["source_policy"],
        "source_index": source_index,
        "preservation": values["preservation"],
        "scale": scale,
        "seed": seed,
        "creative_direction": {
            "visual_family": values["visual_family"],
            "visual_subfamily": subfamily,
            "output_purpose": values["output_purpose"],
            "background": values["background"],
            "visible_text": values["visible_text"],
            "batch_relationship": values["batch_relationship"],
            "source_instruction": source_instruction.strip() or "none",
        },
        "semantics": semantics,
        "delivery": {"final_output_quality": values["final_output_quality"]},
        "user_request_sha256": hashlib.sha256(free_text.encode("utf-8")).hexdigest(),
    }
    if atlas_plan["used"]:
        structured["knowledge_modules"] = {"visual_atlas": atlas_plan}
    atlas_instruction = (
        " When knowledge_modules.visual_atlas is present, use its small selected style descriptors only as "
        "presentation guidance; never copy the Atlas benchmark subject, and never let style guidance override "
        "requested content, identity, or anatomy."
        if atlas_plan["used"]
        else ""
    )
    message = (
        "Use the normal AAG Image workflow. The exact USER_CREATIVE_DIRECTION is the user's natural-language request. "
        "Only semantics.explicit_constraints are authoritative user-selected constraints. Respect them exactly unless "
        "the governed image contract reports that they are unsupported. Fields in semantics.model_discretion_fields "
        "are not literal 'auto' properties: choose appropriate supported values using the request, the explicit "
        "constraints, available image-system capabilities, and professional image-generation judgment. Controls absent "
        "from both semantic collections are not applicable and must not become creative requirements. You retain full "
        "professional authority over prompt wording, composition, lighting, camera, artistic details, and every "
        "unconstrained choice. When semantics.reference_creation is present, it is authoritative: create a substantially "
        "new scene/composition/action from the reference and use image-tool operation=transform. Do not preserve the "
        "source composition by default. For identity purpose, preservation=identity and the same recognizable person are "
        "mandatory while pose, action, environment, camera, lighting, and framing may change to fulfill the exact user "
        "request; never downgrade to subject preservation. General visual reference makes no person-identity claim. "
        "For Edit and Upscale, semantics.source_preservation is authoritative: preserve every "
        "source property not explicitly changed by the exact user request or an explicit constraint. Preserve mode keeps "
        "the source subject, content, identity, composition, and visual style unless a requested edit makes a specific "
        "change necessary. Restyle mode authorizes changing visual style only; preserve the source content and all other "
        "unrequested properties where possible. Upscale never authorizes creative redesign. Author the complete "
        "professional creative FLUX prompt; do not replace it with a caption, "
        "terse summary, deterministic concatenation, or template fragment. The remaining top-level values are signed "
        "internal validation state, not additional user requirements."
        + atlas_instruction
        + "\n"
        + COMPOSER_INTENT_PREFIX
        + composer_canonical_json(structured)
        + "\n"
        + "AUTHORITATIVE_CONTENT_PRESERVATION=Translate the user's request faithfully when needed. Preserve every "
        "requested subject, object, action, relationship, quantity, and named attribute. Composer presentation or "
        "style constraints may change how the requested content is rendered, but never authorize replacing or "
        "omitting that content. Before calling an image tool, compare the professional prompt with the exact user "
        "request and correct every semantic substitution or omission.\n"
        + COMPOSER_USER_REQUEST_PREFIX
        + free_text
    )
    return message, clean_attachments


def composer_preview(data: dict[str, Any]) -> dict[str, Any]:
    """Return a friendly preview without signatures, canonical JSON, or paths."""
    message, attachments = compose_request(data)
    if data.get("mode", "auto") == "auto":
        return {
            "mode": "Auto",
            "lines": [
                "Mode: Auto — free text only",
                f"Description: {data['free_text'].strip()}",
                f"Current uploads: {len(attachments)}",
            ],
        }
    intent = composer_intent_from_message(message)
    creative = intent["creative_direction"]
    family = creative["visual_family"]
    subfamily = creative["visual_subfamily"]
    reference_creation = "reference_creation" in intent["semantics"]
    operation_labels = {
        "generate": "Create" if intent["count"] == 1 else "Batch / series",
        "transform": "Create from reference" if reference_creation else "Edit / transform",
        "upscale": "Upscale / enhance",
    }
    ratio_labels = {"auto": "Auto", "1:1": "1:1 Square", "4:3": "4:3 Landscape classic", "3:2": "3:2 Photography landscape", "16:9": "16:9 Widescreen", "9:16": "9:16 Vertical / phone", "landscape": "Automatic landscape", "portrait": "Automatic portrait"}
    quality_labels = {"auto": "Auto", "fast": "Fast", "balanced": "Balanced", "quality": "Maximum technical quality"}
    source_labels = {"auto": "No source", "current_attachment": "Current upload", "previous_artifact": "Most recent Composer image"}
    lines = [
        "Mode: Advanced",
        f"Operation: {operation_labels[intent['operation']]}",
    ]
    if intent["operation"] == "generate":
        lines.extend([
            f"Style: {COMPOSER_TAXONOMY['families'][family]} / {COMPOSER_SUBFAMILIES[family][subfamily]}",
            f"Size: {ratio_labels[intent['aspect_ratio']]}",
            f"Quantity: {intent['count']}",
            f"Technical quality: {quality_labels[intent['quality']]}",
        ])
    elif reference_creation:
        lines.extend([
            "Reference purpose: "
            + (
                "Preserve person identity"
                if intent["reference_purpose"] == "identity"
                else "Preserve general visual reference"
            ),
            f"Style: {COMPOSER_TAXONOMY['families'][family]} / {COMPOSER_SUBFAMILIES[family][subfamily]}",
            f"Size: {ratio_labels[intent['aspect_ratio']]}",
            f"Technical quality: {quality_labels[intent['quality']]}",
        ])
    elif intent["operation"] == "transform":
        lines.append(
            "Edit mode: "
            + ("Restyle image" if intent["edit_mode"] == "restyle" else "Preserve current appearance")
        )
        if intent["edit_mode"] == "restyle":
            lines.append(
                f"Style: {COMPOSER_TAXONOMY['families'][family]} / {COMPOSER_SUBFAMILIES[family][subfamily]}"
            )
    lines.append(
        "Source: "
        + (
            "Last generated image in this thread"
            if reference_creation and intent["reference_source"] == "latest_thread_artifact"
            else source_labels[intent["source_policy"]]
        )
    )
    if intent["source_index"] != "none":
        lines.append(f"Selected current upload: #{intent['source_index']}")
    if intent["operation"] == "transform" and not reference_creation:
        lines.append(f"Preservation: {'Recognizable person' if intent['preservation'] == 'identity' else 'Subject / content'}")
    if intent["operation"] == "upscale":
        lines.append(f"Upscale: {'Auto (backend default)' if intent['scale'] == 'auto' else str(intent['scale']) + '×'}")
    if intent["seed"] != "auto":
        lines.append(f"Seed: {intent['seed']}")
    hint_labels = {
        "output_purpose": "Intended use",
        "background": "Background direction",
        "visible_text": "Visible text direction",
        "batch_relationship": "Series relationship",
    }
    for field, label in hint_labels.items():
        value = creative[field]
        if value != "auto":
            lines.append(f"{label} (model guidance): {value.replace('_', ' ').title()}")
    lines.extend([f"Description: {data['free_text'].strip()}", f"Current uploads: {len(attachments)}"])
    return {"mode": "Advanced", "lines": lines}


def composer_intent_from_message(message: str) -> dict[str, Any]:
    matches = [line[len(COMPOSER_INTENT_PREFIX) :] for line in message.splitlines() if line.startswith(COMPOSER_INTENT_PREFIX)]
    if len(matches) != 1:
        raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer intent envelope is missing or ambiguous.")
    raw = matches[0]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer intent is not valid JSON.") from error
    if not isinstance(value, dict) or composer_canonical_json(value) != raw:
        raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer intent is not canonical.")
    return value


def composer_user_request_from_message(message: str) -> str:
    """Extract the exact native-chat text from one Composer envelope."""
    if not isinstance(message, str) or message.count(COMPOSER_USER_REQUEST_PREFIX) != 1:
        raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer user request is missing or ambiguous.")
    user_request = message.split(COMPOSER_USER_REQUEST_PREFIX, 1)[1]
    signature_marker = "\n" + COMPOSER_SIGNATURE_PREFIX
    if signature_marker in user_request:
        if user_request.count(signature_marker) != 1:
            raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer signature boundary is ambiguous.")
        user_request, signature = user_request.rsplit(signature_marker, 1)
        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer signature boundary is invalid.")
    return user_request


def validate_composer_intent_call(intent: dict[str, Any], call: CanonicalCall) -> None:
    """Fail closed when a signed Advanced selection differs from a tool call."""
    operation = intent.get("operation")
    count = intent.get("count")
    aspect = intent.get("aspect_ratio")
    quality = intent.get("quality")
    final_output_quality = intent.get("final_output_quality", "standard")
    source_policy = intent.get("source_policy")
    source_index = intent.get("source_index", "none")
    preservation = intent.get("preservation")
    scale = intent.get("scale")
    seed = intent.get("seed", "auto")

    def mismatch(field: str) -> None:
        raise CompatibilityError("COMPOSER_INTENT_MISMATCH", f"Canonical tool call does not preserve Composer field {field}.")

    if operation == "generate" and isinstance(count, int) and count >= 2 and call.tool_name == "aag-image-batch":
        args = call.arguments
        if args.get("operation") != "multi_generate":
            mismatch("operation")
        if args.get("count") != count:
            mismatch("count")
        if quality != "auto" and args.get("quality") != quality:
            mismatch("quality")
        if args.get("final_output_quality", "standard") != final_output_quality:
            mismatch("final_output_quality")
        items = args.get("items")
        if not isinstance(items, list) or len(items) != count:
            mismatch("count")
        if aspect != "auto" and any(
            not isinstance(item, dict) or item.get("aspect_ratio", "auto") != aspect
            for item in items
        ):
            mismatch("aspect_ratio")
        return

    if call.tool_name != "aag-image-task":
        mismatch("operation")
    args = call.arguments
    if args.get("operation") != operation:
        mismatch("operation")
    if args.get("count", 1) != count:
        mismatch("count")
    if aspect != "auto" and args.get("aspect_ratio", "auto") != aspect:
        mismatch("aspect_ratio")
    if quality != "auto" and args.get("quality", "auto") != quality:
        mismatch("quality")
    if args.get("final_output_quality", "standard") != final_output_quality:
        mismatch("final_output_quality")
    if args.get("source_policy") != source_policy:
        mismatch("source_policy")
    if source_index == "none":
        if "source_index" in args:
            mismatch("source_index")
    elif args.get("source_index") != source_index:
        mismatch("source_index")
    if args.get("preservation") != preservation:
        mismatch("preservation")
    if operation == "upscale":
        if scale != "auto" and args.get("scale") != scale:
            mismatch("scale")
    elif "scale" in args:
        mismatch("scale")
    if seed != "auto" and args.get("seed") != seed:
        mismatch("seed")
