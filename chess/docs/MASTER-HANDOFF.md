# AAG Chess Puzzle Agent V1 — Master Handoff

## Release identity

- Application: `1.6.0`
- Verifier: `aag-bounded-mate-v1`
- Renderer: `aag-card-v2`
- Generator: `aag-deterministic-generator-v1.2`
- Stockfish discovery: `aag-stockfish-discovery-v3`
- Density: `aag-density-v1`
- Scorer: `aag-difficulty-v1`
- Public manifest: `aag-public-batch-v4`

## Delivered product

The repository contains a locally runnable CLI that validates one bounded
request, generates a finite deterministic candidate stream, independently
proves exact mate, prevents duplicate identities, measures difficulty, renders
public unsolved SVG/PNG cards, writes a strict manifest, publishes atomically,
and verifies its output. Mate-in-1/2/3 and both sides are supported.
V1.1 adds optional controlled Stockfish-assisted discovery while retaining the
builtin template backend. `MateVerifier` remains the sole acceptance authority.
V1.2 adds the installed AnythingLLM **AAG Chess Puzzle** Skill, its narrow local
Unix-socket bridge, native PNG/SVG download cards, and natural-language
defaults. It does not alter or duplicate the chess engine.
V1.4 adds deterministic sparse/normal/rich construction, weighted automatic
selection, batch balancing, safe public density metadata, and natural-language
density preferences while preserving the verifier boundary.
V1.5 cleans the AnythingLLM presentation: private conversation state is no
longer embedded in chat, cards use Hebrew-only user labels, and verified SAN
solution branches are formatted vertically.
V1.6 adds weighted multi-source Stockfish discovery, explainable structural
fingerprints and quality selection, anti-pawn-wall/near-clone pressure, broad
proof-derived motif metadata, and bounded conversational recent-history
diversity for omitted-seed AnythingLLM requests. V1.5 solution formatting and
RTL/LTR isolation remain unchanged.

The obvious entrypoint is:

```bash
.venv/bin/aag-chess generate \
  --engine auto --mate 2 --side white --difficulty medium --count 10 \
  --output ./output
```

## Operational decisions

- Existing Phase 1/2 correctness and rendering boundaries remain in place.
- `auto` prefers detected Stockfish; `builtin` preserves the offline fallback.
- Stockfish uses deterministic seeded density-aware candidates, fixed nodes,
  `Threads=1`, symmetry-class deduplication, and controlled UCI shutdown.
- The Mate-in-2 library has two independently verifier-gated KQK sources,
  yielding 16 symmetry variants per side. Mate-in-1 and Mate-in-3 each yield 8.
- Difficulty is a proof-derived deterministic filter, not request decoration.
- Public data is created by allowlist rather than by removing fields from a
  private verifier serialization.
- Partial success is reported in errors but never published as a successful
  directory.
- Output overwrite is refused; no `--force` option exists.
- Timestamps and runtime counters are excluded from deterministic artifacts.
- Integrity verification is separate from optional deep chess re-verification.
- The integration installs one new imported Skill directory and one host user
  service. It does not alter existing Skills or database schema/configuration;
  the release acceptance call creates only normal isolated chat/output records.

## Known limitations

The builtin finite KQK library is useful but small. Stockfish-assisted discovery
materially expands positions and material signatures but still does not produce
unlimited variety, prove retro-legality, rate artistic quality, prove
originality, or perfectly predict human difficulty. Current builtin library
categories are Mate-in-1/easy, Mate-in-2/medium, and Mate-in-3/hard. Mate-in-3
accepts mechanically classified post-key duals under the documented warning
policy.

Stockfish-assisted output includes explicitly labeled arbitrary compositions
and forward-legal-playout positions. Same-version deterministic replay is
supported, but output identity is not promised across Stockfish/Python versions.

No public solution artifact is provided. Public output is unsolved by default;
only the private, conversation-scoped AnythingLLM follow-up interface can show
a freshly reverified hint or solution.

## Release verification

The authoritative release commands, hashes, timings, leakage checks, test
result, pre-commit Git state, and acceptance matrix are recorded in
`evidence/stockfish-integration-v1.md`. The final commit hash is printed in the
final handoff response because a commit cannot embed its own hash.
