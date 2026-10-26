# Structural scaffold (exact counts, symmetry, layout)

Added 2026-10-26. General mechanism, not a subject library: the workspace LLM
authors a minimal line drawing whenever a request has exact structure; the
engine restyles it instead of "counting".

## Why
Flux.2 Klein (4B/9B) cannot count or keep symmetric layouts. Two hanukkiah
coloring pages produced 7 and 5 candles from correct prompts. With a
deterministic 9-candle line drawing as a ReferenceLatent source, the 4B model
produced the exact structure in 87 s (`/home/aag-linux/TEMPORARY/hanukkiah-experiment`).

## Contract
- New optional `scaffold` argument on `aag-image-task`, generate only
  (`schemas/provider-task.schema.json`, `skills/aag-image-task/plugin.json`).
- Strict SVG subset: one `<svg viewBox>`, optional `<g>`, elements
  rect/circle/ellipse/line/polyline/polygon/path, numeric attributes only,
  fill none/white/black, no text/image/use/style/script/href/entities.
- `src/scaffold.js` tokenizes, whitelists, bounds every number (|v| ≤ 4096,
  ≤ 1500 elements, ≤ 24000 chars), and re-emits a canonical SVG; the raw model
  string never reaches the rasterizer. Rasterization: sharp/librsvg in the
  container (`deps.renderScaffold` in tests).
- Invalid scaffold → `SCAFFOLD_INVALID` before any job exists.

## Routing
- `runtime.workflow`: generate + scaffold → `generation.scaffold.fast.v1`.
- `runtime.resolveSource`: renders the scaffold as the normalized source
  (`source.kind = structural_scaffold`, sha256, element counts).
- `comfy.execute`: scaffolded generate takes the reference graph with
  0.6 MP reference, fast profile forced, prompt = LLM prompt +
  `scaffold.ENGINE_SUFFIX`; adapter `comfy-scaffold-v1`, revision
  `comfy.scaffold.v1`; artifacts imported as `REF`.
- Show Details: `structural_scaffold`, `scaffold_elements`, `scaffold_canvas`.

## Governance
- Frozen `registry/workflows.json` untouched; documented in
  `registry/workflows-extensions.json`.
- No change to ownership, idempotency or retry policy.

## Tests
`tests/scaffold.test.js`: parser accept/reject matrix, generate-only contract,
source resolution with injected rasterizer, execute routing (fast profile,
ReferenceLatent, suffix, provenance).
