# Architecture

Release: `1.6.0`

## Pipeline

```text
CLI / AnythingLLM request
  -> immutable validated GenerationRequest
  -> backend selection (auto / builtin / stockfish)
  -> deterministic density plan (auto / sparse / normal / rich)
  -> weighted multi-source discovery (game-like / tactical / material / composition)
  -> canonical and symmetry deduplication
  -> optional private fixed-node Stockfish filter
  -> bounded MateVerifier proof (only chess acceptance authority)
  -> explainable quality and structural-similarity selection
  -> independent proof-derived difficulty assessment
  -> public projection and deterministic SVG/PNG rendering
  -> hash-bearing manifest and atomic directory publication
```

`aag_chess.cli` is the supported local interface. `application.py` orchestrates
complete batches and integrity checks; it contains no mate-solving logic.
`generator.py` preserves the finite builtin stream. `stockfish.py` owns local
UCI lifecycle and bounded candidate discovery. `verifier.py` alone can accept
chess correctness.

## Authority boundaries

- `position.py` parses structural validity, canonicalizes FEN, and creates
  stable identities. Structural validity is not historical reachability.
- `generator.py` constructs/orders builtin candidates and calls the verifier;
  a template never self-certifies.
- `stockfish.py` constructs density-aware candidates, optionally from forward
  legal playout, and uses Stockfish only as a private filter.
- `verifier.py` performs the exact bounded minimax proof and is the sole
  acceptance authority.
- `difficulty.py` scores only an accepted proof. It cannot accept a puzzle.
- `density.py` classifies public-safe piece count and plans weighted batches.
  Density is independent of difficulty and has no correctness authority.
- `diversity.py` computes deterministic structural fingerprints, conservative
  proof-derived motif labels, anti-wall/symmetry penalties, and selection
  scores. It ranks verified candidates and has no correctness authority.
- `renderer.py` accepts only verifier-approved data and draws deterministically;
  it never uses generative image AI.
- `application.py` builds an allowlisted public projection and publishes only
  after the exact requested count is complete.

## Density construction

`aag-density-v1` classifies total pieces as sparse 3–9, normal 10–16, or rich
17–26. Stockfish sparse construction emits 5–9; the legacy KQK fallback may
emit three. Automatic single-puzzle selection targets 15/50/35 percent and
automatic batches use deterministic largest-remainder balancing with no run of
more than two sparse profiles.

Normal/rich construction uses asymmetric pawn placement, bounded conventional
piece caps, varied material families, and staggered composed structures rather
than repeated rank scaffolds. Bounded seeded forward-legal game-like and
tactical playout routes also supply reachable candidates. Regardless of construction, Stockfish analyzes the
complete board and `MateVerifier` re-proves that same complete board. Adding
pieces can never preserve acceptance without full revalidation.

Builtin remains a sparse-only offline fallback. Normal/rich explicit requests
fail clearly when builtin is explicitly selected.

## Provenance

Public provenance distinguishes:

- `arbitrary_composition_template`, retro-legality false;
- `stockfish_assisted_material_constructed`, retro-legality false;
- `stockfish_assisted_composition`, retro-legality false;
- `stockfish_assisted_game_like`, legal-generation provenance true;
- `stockfish_assisted_tactical_mutation`, legal-generation provenance true.

This does not claim originality or artistic quality.

## Public/private separation

The in-memory `VerificationResult` is solution-bearing. Public manifests and
artifacts are created from allowlists and exclude keys, SAN/UCI lines, proof
trees, continuations, certificates, engine PV/evaluation, and analysis timing.
Public-safe data includes FEN, exact mate depth, side, identity, difficulty,
density profile/piece count, conservative provenance, component versions, and
artifact hashes.

AnythingLLM solution/hint follow-ups use a separate conversation-scoped opaque
capability. A private scope-index selects the latest capability without placing
a marker or token in visible chat. Retrieval deep-verifies the public batch and
recreates the selected proof with `MateVerifier`; private records are outside
public artifact roots. The Skill projects only clean user text and delivery
URLs to the model, while SAN formatting is deterministic and derived from the
proof rather than reconstructed by the LLM.

## Determinism and bounds

An explicit request seed is the sole source for candidate randomness, source
mixing, density choice,
and batch density ordering. Stockfish uses `Threads=1`, a cleared 16 MB hash,
and a fixed node limit. Candidate attempts, verifier nodes/time, engine nodes,
batch count, and artifact sizes are bounded. Manifests omit timestamps and
runtime paths. Same request, seed, versions, dependencies, and font reproduce
ordered artifacts; cross-version byte identity is not claimed.

AnythingLLM requests with no explicit seed use a bounded public-safe recent
history. A conversation counter deterministically derives seeds and
strict/moderate/fallback similarity thresholds prevent permanent starvation.
Explicit seeds bypass history-based selection and remain reproducible.

## Filesystem and integrity

Generation requires a new output directory under a real non-symlink parent.
Artifacts are staged beside the target and atomically renamed only after the
complete manifest exists. `verify-output` enforces strict JSON shape, canonical
FEN/identity/density consistency, exact file sets, safe paths, sizes, media
types, dimensions, and SHA-256. `--deep` additionally reruns the mate proof and
difficulty scorer.

Runtime acceptance outputs live below ignored `runtime/`; release evidence is
tracked below `evidence/`.
