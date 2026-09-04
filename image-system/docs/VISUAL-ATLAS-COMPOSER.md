# Visual Atlas → Composer — 0.9.0-preview.13

Status: canonical production architecture  
Atlas: `1.0.0`, 28 families, 493 completed styles  
Taxonomy SHA-256: `014fc056dde8b1da9e38efbd7042f6b06d60017d17517fe154c0c2e183d5c16d`
Compatibility boundary: `aag-model-neutral-compatibility-v1.2`

## Production contract

The Visual Atlas is both a user-facing style browser and a bounded selective
knowledge module. The completed Atlas is immutable input to this feature: the
integration does not restart its runner, regenerate previews, replace reference
PNGs, or change its thermal policy.

The single authoritative mapping is:

- taxonomy: `integrations/model-neutral-compatibility/composer/visual-taxonomy.json`
- entry and asset authority: `../visual-atlas/manifest/atlas-manifest.json`
- reviewed retrieval vocabulary: `../visual-atlas/manifest/retrieval-aliases.json`
- reference PNGs: `../visual-atlas/images/<family>/<subfamily>/preview.png`
- browser thumbnails: `../visual-atlas/thumbs/<family>/<subfamily>.webp`

The catalog endpoint joins those sources at runtime. It never returns raw file
paths, benchmark prompts, job IDs, or engine metadata. There is no UI-only copy
of the taxonomy.

## Request flow

```text
user request + optional explicit UI selection
  -> same-origin AnythingLLM Composer proxy
  -> model-neutral Composer compatibility layer
  -> signed normal image-tool request
  -> prompt-quality and anatomy contract
  -> selective-knowledge modules
       -> Visual Atlas deterministic retrieval (0..2 entries)
       -> future independent cultural module hook (no cultural data present)
  -> existing task/batch scheduler and adapters
  -> existing FLUX/ComfyUI or other operation-specific engine
```

The selector is implemented twice at the appropriate trust boundaries:
`visual_atlas.py` supplies catalog/search metadata and the signed Composer plan;
`src/visual-atlas.js` revalidates the exact selection and enriches the final
image prompt immediately before the existing engine adapter. Both consume the
same manifest, taxonomy and alias metadata. Neither contains an image-model or
LLM vendor route.

Each deployed provider carries a build-time snapshot of the canonical taxonomy
and verifies its SHA-256 against the mounted manifest before use. This is a
hash-identical deployment artifact, not a second maintained taxonomy; builds
always copy it from the canonical Composer source.

## Selection modes and precedence

- **AUTO:** a cheap local cue gate followed by normalized exact alias and
  canonical-label matching. No style cue or no reliable match means no Atlas
  intervention. Equal low-confidence matches fail open as ambiguous. There is
  no extra LLM call.
- **Manual taxonomy:** choosing a family and exact subfamily in Composer shows
  the matching completed thumbnail immediately. The exact pair is authoritative.
- **Manual visual browse:** the user searches/filters the gallery, selects an
  actual Atlas card, returns to Composer, and sees the selected thumbnail,
  display path and description. This exact pair is authoritative.

Manual selection overrides AUTO. Free text is still authoritative for subject,
activity, colors and other requested details. The Atlas contributes only style,
medium, composition, lighting and rendering vocabulary. A manual choice is
transported in the optional signed request field as
`AAG_ATLAS_SELECTION_V1`; the authoritative natural-language request remains
unchanged. An absent or forged/incomplete manual pair is rejected.

AUTO considers reviewed English and Hebrew cues and aliases, then canonical
English labels. Top-k is hard-limited to two and the minimum confidence is
`0.72`. Explicit matches are normally `0.90` or greater. Selection order is
deterministic: score, then stable manifest index. Unsupported/no-style input
falls back to the pre-existing prompt byte-for-byte.

## Browser and preview behavior

AnythingLLM's inline Composer is the production surface. It provides family and
subfamily selectors, an actual thumbnail for an exact selection, a larger
preview, Browse Visual Atlas, local search, family filters, a visible selected
state, Change and Clear. Search covers IDs, English and localized labels,
descriptions, and aliases.

The gallery renders at most 48 cards initially and adds 48 only on explicit
request. A viewport observer fetches only nearby thumbnails; it does not load
493 full-resolution files. Full 512px PNGs are requested only when a user opens
a larger preview. Images are served through authenticated, workspace-scoped,
same-origin proxy routes with private immutable browser caching:

```text
/api/aag-composer/image-generator/atlas-thumbnail/:family/:subfamily
/api/aag-composer/image-generator/atlas-preview/:family/:subfamily
```

These protected endpoints require normal AnythingLLM authentication and either
same-origin page provenance or explicit validated Image Generator workspace
headers. React fetches each image with those headers, verifies HTTP success and
the exact PNG/WebP Content-Type, and only then gives `<img>` a browser-local
`blob:https://anythingllm.localhost/...` URL. This avoids relying on native
image-request Referer behavior while preserving the route guard. A query key
derived from the immutable reference hash and derivative version prevents a
cached error from shadowing repaired delivery.

The mounted Atlas directory is read-only. IDs are restricted to canonical safe
slugs and resolved paths must remain beneath the Atlas root.

Manual selection survives ordinary component updates. It clears when the user
chooses Clear, moves to an operation that must not carry a creative style, or
changes from one established thread to a genuinely different thread. The
initial creation of a thread from an unthreaded Composer does not erase the
selection used to create it.

## Reference roles

In this release an Atlas image is a **preview**, not an engine input. The tested
production FLUX graph does not expose an independent style-reference input that
can coexist safely with identity/source images. Therefore
`visual_reference_used=false` and the generation engine receives bounded text
style guidance only.

These roles remain separate:

- Visual Atlas: style/presentation knowledge and UI preview.
- Human Identity: trusted current attachment, provenance-bound Contract B/C
  identity processing.
- Source/edit: current attachment or owner-scoped prior artifact for transform.

Identity and upscale routes exclude Atlas selection. Restyle can use Atlas
knowledge without reclassifying its source. A future engine-specific style-
reference adapter may consume Atlas pixels only if it preserves these roles and
proves that the benchmark subject cannot leak into output.

## Prompt, anatomy and model neutrality

Atlas enrichment occurs after the established prompt-quality/anatomy contract.
The appended text explicitly says the user subject is authoritative, prohibits
copying the Atlas benchmark subject, and preserves anatomy and identity rules.
It cannot remove the strict person-count, head, arm, hand, attachment or
adult/child proportion constraints already produced by prompt quality.

The semantic plan contains only portable fields: family/subfamily IDs and
labels, Atlas index, descriptor, score and matched aliases. FLUX, SDXL and future
engine translation remains inside the normal adapter boundary.

## Context and caching

The full taxonomy, 493 descriptions and 493 images are never injected into a
model context. Catalog browsing is local and deterministic. Runtime context is
limited to 720 characters and two entries. The stable V5.2 workspace prompt and
public schemas are unchanged, preserving their cache-friendly prefixes.

Exact `llama-tokenize` measurements with the deployed Qwen3.5-4B Q4 tokenizer:

| Case | Before | After | Added |
|---|---:|---:|---:|
| Advanced Composer, manual Watercolor | 709 | 1,024 | 315 tokens |
| Advanced Composer, no style | 691 | 691 | 0 tokens |
| Final runtime prompt, one style | 37 | 121 | 84 tokens |
| Final runtime prompt, maximum top-2 | 37 | 141 | 104 tokens |

The established V5.2 baseline remains 15,998 total model-input tokens: 11,397
workspace tokens and 4,364 schema tokens. Measurement evidence is under
`evaluation/visual-atlas-composer-integration-20260903T150000Z/`.

## Observability

Each task record has an `atlas` plan recording `used`, mode, reason, confidence,
selected IDs/indices, taxonomy and manifest hashes, whether a visual reference
was used, and approximate context characters/tokens. Batch children carry their
own plans and the parent stores a bounded summary. Composer prepare audit also
records its selection decision. Normal chat responses do not display these
internals.

Useful checks:

```bash
node image-agent/tools/doctor.js --deployed
node --test image-agent/tests/visual-atlas.test.js
python3 -m unittest image-agent.integrations.model-neutral-compatibility.test_visual_atlas
```

Inspect a generated job JSON under the private `aag-image-agent-state` store for
its `atlas` member. Do not copy private owner or attachment state into reports.

## Failure and fallback

- AUTO Atlas unavailable/invalid: log a bounded diagnostic and continue without
  Atlas enrichment.
- Manual Atlas unavailable/invalid: fail closed rather than silently generating
  in the wrong style.
- No cue, unreliable match or ambiguous low-confidence match: no intervention.
- Identity or upscale: no intervention by contract.
- Unknown asset/style/path traversal: HTTP 404 or bounded validation error.

## Activation and rollback

Activation deploys all three same-version providers, the Composer proxy and the
exact-revision AnythingLLM frontend, declares the Atlas read-only mount, and
recreates only AnythingLLM. It does not restart generation services or mutate
the Atlas corpus.

The exact pre-change rollback is
`backups/visual-atlas-composer-integration-20260903T113547Z/`. Run its guarded
`ROLLBACK.sh --check` before `ROLLBACK.sh --apply`. It restores Preview 11
source/deployed integration files, public frontend and compose definition while
preserving jobs, outputs, identities, models, all 493 reference PNGs, and the
repaired derived thumbnail.

## Production acceptance (2026-09-03)

Preview 13 repairs the operator-reported native-image 403 and is active on the
same pinned AnythingLLM revision and `https://anythingllm.localhost` origin. The
exact Feature-film look thumbnail route returned HTTP 200 `image/webp` with SHA
`9865439b26b7af26efc2f1be32c4443a389e4c4cfcb80c752a9f67df6e727983`;
its large route returned HTTP 200 `image/png` with the unchanged manifest SHA
`9fffc2125f26748faba0a7dd72cf58a28536c2e7e7b06d7848a3b41603072d89`.
The production Brave 152 acceptance passed 23/23 checks with zero broken image
or console errors. The endpoint regression passed 15/15, including requests
without Referer and negative workspace-boundary checks. Evidence is in
`evaluation/visual-atlas-preview-production-fix-20260903T130000Z/`.

The Atlas 1.0.0 product gate reports 493/493 references and 493/493 thumbnails.
Before/after reference, thumbnail, taxonomy and manifest set hashes are
identical. Release builds now fail before staging when the mandatory product
asset is absent, incomplete or corrupt.

The historical Preview 12 activation used AnythingLLM revision
`07bd65f80b3d9ba3031ed7afb8786627326bd134`. Final verification passed
306/306 durable tests, 565/565 deployed doctor gates, 19/19 live browser
checks, 15/15 controlled generation/idempotency checks, and 13/13 independent
Atlas integrity checks. The accepted Watercolor request completed through the
existing FLUX/ComfyUI fast path as job
`aag-3d6549e9-23e4-4b5d-a390-9384447a1bab`; its decoded 512x512 artifact is
SHA-256 `82378d70695d1eedfd0bf706a3d9802a460a537529e3df3c509e029cb3458e5a`.

All 493 manifest-bound reference PNG hashes and dimensions passed, all 493
browser thumbnails are non-empty WebP files, AnythingLLM and the compatibility,
Hub and Upscale health endpoints passed, the Atlas mount is read-only, and the
XPU queue was empty. The local port-8080 language-model server is intentionally
on demand and was stopped at the final snapshot, so compatibility `/ready`
correctly returned `503 upstream-unavailable` while `/health` remained healthy;
this does not affect deterministic browsing or the already-proven image runtime.

Machine evidence, including the disclosed host-only decoder harness failure
that preceded the successful in-container run, is in
`evaluation/visual-atlas-composer-integration-20260903T150000Z/`. The sealed
release is `releases/0.9.0-preview.12`; its `FILE-SHA256SUMS` verifies the
release without copying or mutating the Atlas corpus.

That Preview 12 browser claim is historical and was superseded by operator UAT:
the live Brave
profile cached an HTTP 403 for the native `<img>` request because that request
carried neither a usable Referer nor the Composer's custom workspace headers.
Preview 13 repairs and retests the actual `https://anythingllm.localhost`
delivery path. Product packaging and recovery are authoritative in
`VISUAL-ATLAS-PACKAGING.md`.

## Deliberate limitations and extension hook

- Automatic selection uses a reviewed deterministic vocabulary, not embeddings;
  unfamiliar phrasing safely receives no Atlas context.
- AUTO selection is observable internally but is not currently surfaced as a
  persistent manual UI chip.
- Atlas pixels are not submitted as style-conditioning input in this engine
  generation.
- The loopback standalone Composer remains a diagnostic fallback; the native
  inline AnythingLLM Composer is the production browser.
- No Haredi cultural data is in this repository and none is invented here.
  `src/selective-knowledge.js` provides the composition point for a future
  independent, bounded cultural-intent module whose plan can coexist with Atlas
  style and anatomy plans.
