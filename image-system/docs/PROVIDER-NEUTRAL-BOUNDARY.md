# Provider-neutral public-contract boundary

The authoritative Image System path is:

```text
ANY PROVIDER / ANY MODEL
→ provider adapter
→ canonical AAG public tool contract
→ strict canonical pre-execution validation
→ governed execution
```

The public contract and the pre-execution validator are provider-neutral. The
validator checks the exact canonical schema after a provider adapter has
returned structured arguments and before an AAG handler or parent job can run.
Unknown properties, missing required fields, invalid enums, invalid types, and
out-of-bounds values fail closed with `PUBLIC_SCHEMA_VIOLATION` and zero jobs.
This remains true without provider-side constrained decoding or a grammar.

The portable public object structurally requires `operation`, `prompt`,
`source_policy`, and `preservation`. Generate and transform use a rich prompt
authored by the workspace model. Upscale uses a concise instruction to enlarge
the selected source without creative alteration; the governed upscale routing
and recipe remain unchanged. `preservation` has no schema default because its
valid value is operation-dependent.

Provider adapters are responsible only for translating their provider's tool
representation into the canonical function name, JSON Schema, and structured
arguments without weakening or rewriting that contract. They do not authorize
fields, choose AAG workflows, validate sources, or bypass canonical validation.

- The managed local-model path uses one model-neutral compatibility boundary.
  Each server session must first pass a basic model/text sanity probe. Only a
  sane decoder may proceed to the separate ordinary multi-role chat probe.
  Only sane compatible chat may proceed to tool capability discovery. Tool
  behavior is classified as `NATIVE`, `GENERIC_ADAPTER`, or `INCOMPATIBLE`.
  Classification never branches on a model name, family, filename, or exact
  path. A lower-runtime text failure is never repaired by the tool adapter.
- The generic adapter accepts only one canonical
  `{ "tool_name": "...", "arguments": { ... } }` object, validates it against
  the request's actual tool schemas, and emits a normal OpenAI tool call. It
  never executes a tool. Workspace Capability Profiles V1.1 and the canonical
  AAG pre-execution validator remain the authorization boundaries.
- Raw special tokens, leaked tool-control syntax, malformed template output,
  empty unusable output, and pathological repetition fail closed. Tokens are
  not cosmetically stripped or hidden.

The four capability stages are deliberately separate:

1. **Basic model / text sanity** — the GGUF tokenizer, embedded template,
   llama.cpp rendering, and decoded output must produce bounded human-readable
   text without raw special/control tokens.
2. **Ordinary chat compatibility** — a normal multi-role chat request must
   remain sane through the OpenAI-compatible chat boundary.
3. **Tool capability** — only after stages 1 and 2 pass, a side-effect-free
   synthetic schema probe may test native and then generic-adapter behavior.
4. **Mode** — valid native behavior is `NATIVE`; a valid canonical adapter is
   `GENERIC_ADAPTER`; every other outcome is `INCOMPATIBLE` and fails closed.

The local runtime compatibility layer is optional defense in depth, not a security or correctness dependency
for the canonical AAG execution boundary.
Every accepted call still passes Workspace Capability Profiles V1.1 and the
unchanged canonical pre-execution validator.
- LM Studio and Ollama are compatible at the OpenAI-style tool-adapter boundary.
  Native constrained generation may be used when available, but is not
  required; their returned arguments still pass through canonical validation.
- Gemini and OpenAI-compatible cloud providers receive the same canonical JSON
  Schema through their adapters. Native tool/schema enforcement is helpful but
  cannot replace or bypass AAG validation.
- Future compatible providers require only an adapter that preserves the
  canonical function/schema/argument boundary. No Image System rewrite or
  provider/model allow-list is required.

The provider-neutral quality field is a technical preference, separate from
creative prompt language:

- `fast`: explicit preference for speed, quick output, low latency, or the
  fastest supported generation.
- `quality`: explicit request for maximum, best, or highest generation quality,
  or an explicit preference for quality over speed.
- `balanced`: explicit request for a compromise between speed and quality.
- `auto` or omission: no expressed technical speed-versus-quality preference;
  use the governed default.

Creative direction—including 3D, cinematic, detailed, polished, professional,
realistic, storybook, watercolor, beautiful, or highly detailed—stays in the
creative prompt and never alone implies `quality`.

The loopback-only AAG Image Composer is a non-core local surface. AUTO passes
free text unchanged. ADVANCED adds authoritative operation, visual family,
subfamily, aspect, count, technical quality, source, preservation, and upscale
requirements while explicitly retaining the workspace model as author of the
full professional creative FLUX prompt. Both modes submit to the existing Image
workspace API and therefore traverse the same compatibility and capability
profile boundaries as normal AnythingLLM use.

## Signed Composer canonicalization

ADVANCED Composer envelopes use the RFC 8785 JSON Canonicalization Scheme at
the Python/JavaScript boundary. Python generation, HMAC signing, Python
verification, JavaScript visible-prompt recognition, and intent audit hashing
all consume the same canonical intent bytes. In particular, integral binary64
values are encoded without a decimal fraction (`1.0` is `1`), object keys use
UTF-16 code-unit order, invalid Unicode and non-finite values are rejected, and
strings retain standard JSON escaping. This contract is scoped to signed
Composer intents; unrelated `stable_json` hashes are unchanged.

The AnythingLLM persistent and ephemeral agent bridges call the strict
`composerInvocationPrompt` boundary. A valid signed envelope contributes only
the exact `USER_CREATIVE_DIRECTION` text to `AAG_INVOCATION_PROMPT`. An ordinary
message remains unchanged. A message containing reserved Composer markers that
cannot pass canonical structure and request-hash checks raises
`AAG_COMPOSER_ENVELOPE_INVALID`; hidden envelope text is never promoted to the
authoritative request. HMAC verification remains mandatory in the Python
compatibility boundary, so mutations to signed text, Atlas selection,
confidence, signed fields, or signature still fail before tool execution.
