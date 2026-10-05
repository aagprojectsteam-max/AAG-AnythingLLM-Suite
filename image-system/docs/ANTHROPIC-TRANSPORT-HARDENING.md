# Anthropic agent transport and completion

The agent uses AnythingLLM's selected Anthropic model, SDK authentication, network path and provider routing. There is no filter bypass or automatic rewriting of blocked content. OpenAI transport is unchanged.

## Filter errors

An HTTP 418 is classified as `PROVIDER_TRANSPORT_BLOCKED` only when the error body contains positive NetFree block-page and HTML evidence. Logs retain provider, status, filter and an allow-listed classification. The UI receives a short message, never the HTML. Unknown 418 responses are not attributed to NetFree. A block-page response is not an Anthropic model rejection; origin receipt cannot be proven from the page alone.

Failure diagnostics separate user text, system text, history, tool descriptions, tool schemas and, when a Composer envelope is present, Expert and selected style guidance. Only lengths and SHA256 fingerprints are logged. VisualPlan is recorded if supplied as an explicit request field. A model-authored VisualPlan normally arrives later in tool arguments; it is not invented as an input component. The filter response does not identify the causal component. Fingerprints support investigation without claiming keyword-based attribution or resubmitting blocked content.

## Stream safety

The installed Anthropic SDK 0.39.0 parses an SSE event-only frame with `JSON.parse('')`, producing `Unexpected end of JSON input` and logging the raw frame. The observed historical failing frame was `event: content_block_delta` with no data. Historical raw HTTPS bytes were not recorded, so the original loss layer cannot be reconstructed.

The adapter now obtains the SDK HTTP response with `asResponse()` and keeps its `_iterSSEMessages` framing. A guarded JSON conversion reports `PROVIDER_SSE_DATA_MISSING`, `PROVIDER_SSE_INVALID_JSON`, or an event mismatch without dumping content. Missing event data is never replaced or ignored. This is diagnostic hardening, not recovery of missing upstream data.

A tool can return to the neutral boundary only after message start, complete matching tool input, content-block stop, `stop_reason=tool_use`, message stop and clean stream exhaustion. Partial JSON, missing markers, provider errors, duplicate completion, conflicting input and multiple calls to a serial agent fail closed. Existing AIbitat call-ID claiming prevents duplicate dispatch. Distinct calls in successive completed messages remain supported.

No Anthropic wire logic is added downstream of the provider adapter. Transport bounds are 8 MiB per event and 32 MiB per stream, in addition to the existing serial argument bound.

## Context metadata

`AnthropicLLM.fetchModelMaxTokens()` already retrieves the selected model through the authenticated Models API. It now also supplies verified `max_input_tokens` to the existing context lookup in process memory, requiring a matching model ID and a positive safe integer. It does not fetch a second model map, guess capacities or change provider selection. Until metadata arrives or if retrieval fails, the existing 100,000 input-window and 4,096 output-token fallbacks remain. A missing-model warning is deduplicated per process/model.

References: [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming), [model metadata](https://platform.claude.com/docs/en/api/http/models/retrieve).

## Tests and deployment

`node --test tests/anthropic-hardening.test.js tests/provider-normalization.test.js` covers normal text, initial and fragmented tool arguments, empty deltas, missing data, incomplete/malformed JSON, block/message completion, EOF, errors, filter classification, duplicates, serial calls, exactly-once dispatch, privacy and context fallback. The captured historical event-only frame is in `tests/fixtures/anthropic/missing-data.sse`.

Use the normal candidate/seal/deploy pipeline. The transport helper and ordinary Anthropic metadata overlay are explicit read-only deployment mappings; no installed SDK files are patched.
