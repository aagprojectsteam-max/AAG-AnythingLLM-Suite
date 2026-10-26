# Changelog

# 0.9.0-preview.13 (2026-10-26 maintenance + colorize)

- Added the structural scaffold: the workspace LLM authors a strict-subset
  SVG line drawing for requests with exact counts/symmetry/layout; the engine
  restyles it through the reference path, so structure is exact. See
  `docs/STRUCTURAL-SCAFFOLD.md`.
- Added the `colorize` operation (DDColor on the local ComfyUI/XPU engine):
  black-and-white restoration with preserved dimensions, no engine prompt,
  fast/quality model tiers, Composer operation entry, closed-contract tests.
  See `docs/COLORIZE.md`.
- Engine endpoints moved to `src/endpoints.js` (ENV-overridable, validated
  private http URLs); no hard-coded Docker gateway addresses in runtime code.
- Compatibility proxy: unified 32 MiB request limit on `/v1/*` (closes the
  historical HTTP 413 on vision payloads), configurable upstream timeout,
  size-based rotation of `audit.ndjson`.
- Composer UI: single-line header, info glyph instead of repeated
  "MODEL GUIDANCE", shorter helper texts, visible engine state chip, primary
  Download action, correct RTL ellipsis on thread titles.
- Removed the duplicated inline Atlas prompt-disclosure script from the
  panel bundle (it was loaded twice in production with the standalone file).
- `aag-llama-control`: per-model tuning moved to `aag-llama-model-overrides.json`
  so the launcher logic stays model-neutral (release gate requirement).

# 0.9.0-preview.13

- Fixed Production Visual Atlas thumbnails and large previews by replacing
  unauthenticated native image loads with guarded same-origin fetches and
  validated blob display, including immutable content-addressed URL versions.
- Added production-origin endpoint and Brave browser regressions for exact
  Cinematic / Film Still → Feature-film look selection, gallery, search,
  changed selection, large preview, HTTP failures and console errors.
- Made Visual Atlas 1.0.0 a mandatory sealed product asset: 28 families, 493
  byte-preserved PNG references and 493 hash-bound thumbnails. Release builds
  now fail on any missing, corrupt or incomplete Atlas asset and record the
  mandatory asset contract.
- Added deterministic Atlas export/fresh-install restore tooling and operator
  backup/restore documentation without adding a second 226+ MB live copy.

## 0.9.0-preview.12

- Integrated the completed 493-entry Visual Atlas as a deterministic,
  model-neutral selective-knowledge module with reviewed English/Hebrew
  aliases, top-k 2, confidence/fallback rules, and bounded prompt context.
- Added the user-facing Composer Visual Atlas browser, family filter, local
  search, 48-card progressive rendering, lazy thumbnails, exact selection,
  immediate preview, larger preview, clear/change actions, and thread-scoped
  manual-selection state. Manual taxonomy and visual selections override AUTO.
- Added same-origin, workspace-guarded thumbnail and preview routes sourced
  directly from the canonical Atlas manifest. Thumbnails are immutable-cached;
  full 512px previews load only on an explicit larger-preview gesture.
- Kept Atlas style references distinct from Human Identity and edit/source
  references. The current engine receives compact textual style guidance only;
  Atlas images are never attached to a generation graph lacking role-separated
  reference support.
- Added durable per-job Atlas decision evidence, exact family/subfamily,
  confidence/reason, visual-reference use, and approximate dynamic context
  contribution without adding diagnostics to normal user responses.
- Repaired one discovered zero-byte derivative thumbnail from its intact,
  hash-verified completed PNG and expanded integrity tests to validate every
  preview and thumbnail.
- Added a composable selective-knowledge hook for future independently sourced
  modules. No Haredi cultural facts or nonexistent pack files were invented.

## 0.9.0-preview.11

- Added the provider-neutral `aag-image-batch` generate-only public contract:
  exactly 2–10 workspace-model-authored ordered prompts under one collection
  brief, with closed nested schema validation and no structured style field.
- Added one durable batch parent, stable logical/execution child IDs, exact-count
  completion, same-turn parent/child deduplication, ordered provenance,
  partial-sibling preservation, explicit new-turn resume, and safe running
  cancellation between sequential children under the existing XPU lease.
- Added coherent native AnythingLLM live/history collection presentation with
  verified progress, per-image Download/PDF actions, and complete-only Save all
  as PDF.
- Added the reusable AAG Artifact Export boundary for Image, future Chess, and
  future trusted producers. PDF generation is local, deterministic, lossless,
  one image per page, provenance-keyed, cached only after an explicit gesture,
  and exact-page-count verified.
- Added Save-As-first browser UX with editable
  `ANYTHING-YYYYMMDD-HHMMSS.pdf` UTC suggestions and a browser-download
  fallback. Browser payloads cannot supply host output paths.
- Extended the trusted websocket turn-context refresh to the batch skill, so
  the batch contract receives the same server-derived invocation and per-turn
  scope as the existing task and job skills and still fails closed if absent.
- Made omitted per-item seeds allocate bounded persisted internal seeds before
  execution, keeping child retries stable and preventing null sampler inputs.
- Preserved the Preview 10 single-image schema and quality meanings, Prompt
  V5.2, source selection, no-same-turn-retry, provider-neutral authoritative
  validation, Connection:close, FLUX routes, XPU governance, native cards,
  Contract B, and Contract C.

## 0.9.0-preview.10

- Clarified the provider-neutral `quality` selection contract: explicit
  speed/latency intent selects `fast`; explicit maximum-quality or
  quality-over-speed intent selects `quality`; an explicit compromise selects
  `balanced`; otherwise `auto` or omission uses the governed default.
- Made creative art-direction wording—including cinematic, detailed, 3D,
  polished, professional, realistic, storybook, watercolor, and equivalent
  Hebrew intent—remain prompt semantics rather than technical quality routing.
- Made `prompt` structurally required in the portable public schema so every
  provider receives the same complete-call boundary. Generate/transform use the
  workspace-LLM-authored creative prompt; upscale uses a concise no-creative-
  alteration instruction while retaining its existing governed route.
- Removed the misleading `preservation=auto` schema default. The enum remains
  available for explicit fail-closed ambiguity, while generate/upscale select
  `none` and transform explicitly selects `subject` or `identity`.
- Added provider-independent fail-closed regression coverage proving unknown
  fields, missing required fields, and invalid enums are rejected by canonical
  pre-execution validation even when no provider grammar is present.
- Documented canonical validation as the authoritative provider-neutral
  boundary. The llama.cpp Gemma template is an optional prevention layer; LM
  Studio, Ollama, Gemini, OpenAI-compatible cloud, and future adapters require
  no Image System rewrite when they preserve the canonical tool boundary.
- Preserved Prompt V5.2, source selection, no-same-turn-retry, the FLUX
  recipes/routes, XPU governance, native presentation, Connection:close,
  Contract B, and Contract C unchanged.

## 0.9.0-preview.9

- Enforced the closed public task schema at the AnythingLLM execution boundary
  before any image handler or parent job can run. Undeclared fields, missing
  required fields, invalid enums, types, and bounds now fail deterministically.
- Removed the private legacy `style` compatibility input. Creative style remains
  workspace-LLM-authored prompt text and is never a structured task argument.
- Made `aag-image-task` results direct-output for the current turn, so
  AnythingLLM cannot offer a post-result image-task retry after success or
  failure. Existing runtime idempotency remains an independent duplicate guard.
- Kept source selection, Prompt V5.2, provider neutrality, Connection:close,
  XPU governance, native presentation, Contract B, and Contract C unchanged.

## 0.9.0-preview.8

- Added the provider-neutral canonical prompt-quality contract. The trusted
  browser invocation is authoritative and the selected agent LLM remains the
  sole semantic/creative prompt author. Complete proposals are preserved
  unchanged; under-specification is recorded without inducing a retry loop.
  AAG contains no prompt templates, prose appends, second LLM or retry loop.
- Added explicit ordinary, detailed-prompt, identity-scene, coloring-page, and
  photorealistic regression fixtures without changing model, sampler,
  scheduler, steps, CFG, shared-XPU governance, or native presentation.
- Added a provider-neutral AnythingLLM artifact-presentation adapter for the
  canonical `aag-image-task` result. Completed artifacts are fetched only from
  the trusted local Hub, then MIME-, SHA-256-, and dimension-verified before
  registration as native `imageGenerationCard` outputs.
- Removed the accidental requirement that a model emit Markdown to display an
  image. Websocket and one-off Agent paths now use the same deterministic card
  registration, preserve canonical artifact metadata, and retain AnythingLLM's
  authenticated workspace-scoped image-serving boundary.
- Kept Contract B, Contract C, PuLID, Juggernaut, generation, transform,
  upscale, ownership, scheduler, and idempotency behavior unchanged.

## 0.9.0-preview.7

- Restored the four overlapping legacy image skills to their governed inactive
  state so every model sees one canonical model-facing image task and job.
- Separated the current Image Agent release from frozen Scene Contract C's
  validated Preview 5 protocol release; Contract C bytes and hash are unchanged.
- Kept provider compatibility open by protocol capability with no vendor/model
  policy branch.

## 0.9.0-preview.6

- Declared `AAG_IMAGE_PROVIDER_POLICY=OPEN_BY_CAPABILITY`: no provider/model
  allow-list and no vendor-name policy branch exists in the canonical core.
  Any integration producing a valid supported tool call receives identical
  normalization, security, routing and result semantics.
- Corrected the provider-independent real-browser integration boundary: when
  any supported provider omits the optional `request` summary but supplies a
  valid tool prompt, the trusted core uses the server-injected current
  invocation prompt.
- Kept missing semantic intent fail-closed when neither a provider request nor
  trusted invocation prompt exists; attachment ownership, source validation,
  Contract B, Contract C, models, recipes and identity-to-subject prohibition
  are unchanged.
- Preserved sealed Preview 5 as historical evidence and added durable Generic
  OpenAI/local-model and Gemini browser acceptance requirements.

## 0.9.0-preview.5

- Preserved frozen Human Identity portrait Contract B byte-for-byte and added
  separately frozen Scene Identity Contract C for bounded identity-preserving
  scene/action transforms.
- Canonicalized provider framing hints internally: omitted/896×1152 portrait
  intent routes to Contract B; bounded portrait/landscape scene intent routes
  to Contract C without exposing workflow details or falling back to subject.
- Added request-bound private Scene C staging, an on-demand local bridge,
  locked 0.55 identity floor, mandatory visual veto, scene-fidelity evidence,
  persistent activation/doctor coverage, and exact rollback boundaries.
- Kept PuLID v1.1, Juggernaut XL V9, ordinary generation, subject transform,
  upscale, runtime-context bridge, owner validation and idempotency V2 assets
  and behavior unchanged.

## 0.9.0-preview.4

- Activated the sealed ordinary-generation balanced-aspect winner without
  changing the FLUX.2 Klein 4B model, Euler + Flux2Scheduler, four steps, CFG
  1.0, verbatim prompt policy or zeroed negative conditioning.
- Added deterministic 640x640 square, 512x768 portrait and 768x512 landscape
  defaults, bounded recipe/model provenance metadata, and required offline /
  telemetry-disable environment at ComfyUI process start.
- Kept Human Identity Contract B, PuLID/Juggernaut route, prompts, thresholds,
  evaluator, bridge and asset paths byte-unchanged.

## 0.9.0-preview.3

- Activated the Phase 7R3-confirmed local Human Identity Production-v1 route for the frozen authorized adult-man and baby fixtures.
- Added exact Contract B routing, cold on-demand PuLID v1.1 + Juggernaut XL V9 host execution, locked quality gates, delegated global XPU lease, running cancellation, and local-only bridge lifecycle.
- Kept ordinary generation, subject transform, upscale, and context routes unchanged.

## 0.9.0-preview.2 — Hardening Pass 2 candidate

- Split the trusted runtime into bounded source, store, scheduler, engine, artifact, and error modules.
- Added FIFO shared-XPU arbitration with external ComfyUI/Upscale participation and stale-owner reconciliation.
- Added real child records, sequential `count=2`, partial-failure provenance, queue cancellation, timeout classification, and restart-stale recovery.
- Added decoder-backed source normalization, no-follow/exclusive publishing, resource bounds, stable error redaction, and direct allowlisted engine adapters.
- Disabled Human Identity with stable `CAPABILITY_UNAVAILABLE` because the persisted visual evidence does not prove reliable identity fidelity.
- Added deterministic staging build, consistency doctor, and expanded Node/Python security and integration suites.
