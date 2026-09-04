# AAG Chess Puzzle Agent

AAG Chess Puzzle Agent 1.6.0 is a local application that creates
deterministically verified, unsolved chess puzzle cards. It supports exact
Mate-in-1, Mate-in-2, and Mate-in-3 for White or Black, emits SVG and/or PNG,
and writes a public JSON manifest with SHA-256 metadata. It can be used through
the CLI or directly from the installed local **AAG Chess Puzzle** AnythingLLM
Skill.

The verifier—not the generator, an LLM, or a chess-engine score—is the final
authority. Every released position passes bounded legal-move proof search.

## Scope and limitations

V1.6 has two candidate-discovery backends. `stockfish` mixes four bounded,
seeded sources: forward legal playout, tactical legal playout, diverse material
construction, and composed fallback. It uses a local Stockfish process to
filter complete positions before AAG verification. `builtin` preserves the small offline
KQK template library and remains available without Stockfish. The builtin
verified candidate universes are:

- Mate-in-1: 8 candidates per side;
- Mate-in-2: 16 candidates per side;
- Mate-in-3: 8 candidates per side.

Stockfish materially expands variety but is not an unlimited creative
composition engine. A deterministic selection layer favors varied material,
pawn structures, king regions, piece interactions, source families, and broad
proof-derived motifs. It penalizes repeated pawn scaffolds, symmetry, and
near-clone structures. Requests never repeat a puzzle to reach a count. An
unavailable combination or excess count fails clearly and writes no partial
output. Positions are structurally valid arbitrary compositions; V1 does not
claim originality or artistic quality. Arbitrary compositions are explicitly
marked as not retro-proven; forward legal playout candidates are labeled
separately with legal-generation provenance.

## Prerequisites and setup

- Python 3.12 or newer (acceptance tested with Python 3.14.4);
- local DejaVu Sans font at
  `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` for PNG rendering;
- the core CLI needs no network, GPU, LLM, or image-generation service;
- AnythingLLM use requires the existing local AnythingLLM service and its
  configured agent model, but adds no chess-side network dependency;
- optional local Stockfish (recommended for expanded variety; tested with
  Stockfish 17.1 at `/usr/games/stockfish`).

From the repository root:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
```

The existing project environment can be used directly:

```bash
.venv/bin/aag-chess --help
```

## Generate puzzles

The output directory must not already exist. SVG and PNG are produced by
default. `auto` is the CLI default: it prefers Stockfish and falls back to the
builtin backend when Stockfish is unavailable.

```bash
.venv/bin/aag-chess generate \
  --engine auto \
  --mate 2 \
  --side white \
  --difficulty medium \
  --count 10 \
  --seed 42 \
  --density auto \
  --output ./output-mate2
```

Select one format or both with `--formats`:

```bash
.venv/bin/aag-chess generate \
  --engine stockfish --mate 1 --side black --difficulty easy --count 1 \
  --formats svg png --output ./output-black
```

The stable output shape is:

```text
output-mate2/
  manifest.json
  puzzles/
    puzzle-0001.svg
    puzzle-0001.png
    ...
```

`manifest.json` contains only public puzzle data: versions, normalized FEN,
public identity, measured difficulty, density profile/piece count, public-safe
source/motif/selection metadata, request/accounting fields, filenames,
dimensions, media types, sizes, and SHA-256 hashes. It contains no key move,
continuation, proof tree, certificate, or solution. V1 intentionally provides
no public solution-export command.

Select the offline fallback explicitly with `--engine builtin`. Check the
detected binary and version without starting a generation request:

```bash
.venv/bin/aag-chess engine-info
```

Stockfish runs with one thread, a 16 MB hash, a cleared transposition table per
candidate, and a fixed node limit. Adjust bounded discovery with
`--max-attempts` (1–10,000) and `--stockfish-nodes` (100–1,000,000). The
application starts and stops the UCI process itself.

### Automatic board density

`--density auto` is the default. Its deterministic target mix is approximately
15% sparse, 50% normal, and 35% rich, so sparse positions are a minority.
Stockfish construction uses 5–9 pieces for sparse, 10–16 for normal, and
17–26 for rich. The legacy builtin fallback remains sparse and may contain as
few as three pieces. Automatic batches are balanced deterministically, include
normal/rich profiles, and never schedule more than two sparse puzzles in a row.

Advanced callers may select `--density sparse`, `normal`, or `rich`. Rich and
normal require the Stockfish backend; `builtin` remains a sparse-only fallback.
Density guides candidate construction, after which Stockfish filters and the
complete position must still pass `MateVerifier`. Density is independent of
difficulty and never substitutes for verification.

## AnythingLLM natural-language use

The installed AnythingLLM Skill accepts ordinary Hebrew or English chess
intent. Missing technical choices never require a follow-up: the defaults are
one puzzle, White to move, medium requested difficulty, `auto` discovery,
automatic density, and PNG. For example, typing only this is sufficient:

```text
תעשה לי חידה מט ב-3 לשחור
```

AnythingLLM calls the existing local application, performs deep integrity
verification, displays the unsolved PNG inline, and keeps its download card.
The visible reply is intentionally short and Hebrew-friendly; private context
tokens, hashes, component versions, engine diagnostics, and manifest internals
are never printed in normal chat. Puzzle cards likewise show only the Hebrew
title, mate length, Hebrew difficulty (`קל`, `בינוני`, or `קשה`), side to move,
and board. Technical versions remain in public metadata for integrity checks,
not in the visible card.

Natural follow-ups such as `תן לי רמז`, `מה הפתרון?`, and
`מה הפתרון לחידה 2?` retrieve the selected puzzle's proof from a private,
conversation-scoped context. The bridge deep-verifies the batch and reruns the
same bounded `MateVerifier`; the chat model never solves the position.
Solutions use authoritative SAN in vertical move order, show defensive
branches separately, preserve `+`/`#`, and use Unicode direction isolation so
notation remains left-to-right inside Hebrew text. Hints remain progressive
and omit the full line until an explicit solution request.
Explicit requests can select a count, difficulty, SVG, both formats, Stockfish,
or builtin discovery. Natural phrases such as `לוח מלא יותר` / `הרבה כלים`
select rich density, while `מעט כלים` selects sparse. The Skill accepts no output path, executable, or shell
argument. See [`docs/ANYTHINGLLM-SKILL.md`](docs/ANYTHINGLLM-SKILL.md) for the
installed paths, defaults, operations, and troubleshooting.

## Determinism

For the CLI and for an explicit AnythingLLM seed, `--seed` is the sole source
of candidate construction, source ordering, density selection, and batch
ordering. The same
request, seed, backend, Stockfish version/configuration, AAG versions, pinned
dependencies, and font reproduce the ordered result; demonstrated same-version
replays produce identical manifests and images. Reproducibility is not claimed
across Stockfish or Python versions. Runtime timings and timestamps are omitted
from the manifest.

Ordinary AnythingLLM requests omit a seed. The bridge then derives a new seed
from a conversation-scoped counter and compares the result with recent
public-safe structural fingerprints. It tries strict, moderate, then fallback
similarity limits, so history cannot make generation unbounded. Supplying a
seed explicitly bypasses conversational selection and preserves reproducible
CLI-equivalent behavior. History contains no FEN, move, proof, certificate, or
solution.

## Difficulty meaning

Difficulty is measured after chess verification by `aag-difficulty-v1`. Mate
depth is dominant, with bounded adjustments from root legal-move count,
defensive proof branching, detected post-key duals, and whether the key gives
check. Scores 0–34 are `easy`, 35–69 are `medium`, and 70–100 are `hard`.

With the builtin library, supported generated categories are:

- Mate-in-1 → `easy`;
- Mate-in-2 → `medium`;
- Mate-in-3 → `hard`.

This is an explainable mechanical V1 estimate, not a claim about subjective
human difficulty or artistic merit. Difficulty can filter candidates; it can
never accept or reject chess truth in place of the verifier.

Stockfish scores, mate values, best moves, and principal variations remain
private filters. A candidate becomes public only when the unchanged bounded
`MateVerifier` returns `accepted=True` for the requested exact distance.

## Verify output

Check the manifest, exact file set, metadata, sizes, and SHA-256 hashes without
re-solving the chess positions:

```bash
.venv/bin/aag-chess verify-output ./output-mate2
```

Explicitly re-run bounded mate proofs and difficulty scoring too:

```bash
.venv/bin/aag-chess verify-output ./output-mate2 --deep
```

## Tests

```bash
.venv/bin/pytest -q
git diff --check
```

Architecture, operations, verification details, and the release record are in
[`docs/`](docs/) and
[`evidence/anythingllm-skill-v1.md`](evidence/anythingllm-skill-v1.md).
