# Operations

## Install and inspect

```bash
cd /mnt/data/AI/Agents/AAG-Chess-Puzzle-Agent
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/aag-chess --version
.venv/bin/aag-chess --help
.venv/bin/aag-chess engine-info
```

Dependencies are pinned in `pyproject.toml`. Generation is local and performs
no network access. The optional Stockfish backend launches only the detected
local UCI binary; the builtin backend launches no subprocess. PNG requires the
recorded DejaVu Sans font path; SVG is self-contained text/XML.

## Generate

```bash
.venv/bin/aag-chess generate \
  --engine auto --mate 2 --side white --difficulty medium --count 10 \
  --seed 42 --formats svg png --output ./output-m2
```

The output parent must already exist and must not be reached through a
symlink. The final output directory must be new. Create a different output name
for a rerun; overwrite is deliberately unsupported.

`auto` prefers Stockfish and falls back to builtin only when discovery finds no
binary. Explicit `stockfish` fails if unavailable. Explicit `builtin` preserves
the offline V1 generator.

`--max-attempts` is bounded to 1–10,000; the CLI default is 2,000.
`--stockfish-nodes` is bounded to 100–1,000,000 per candidate; the default is
50,000. `--count` is bounded to 1–100. No budget silently expands, and a failed
exact-count request returns no partial directory.

## Verify and diagnose

```bash
.venv/bin/aag-chess verify-output ./output-m2
.venv/bin/aag-chess verify-output ./output-m2 --deep
```

The normal command checks public integrity only. Deep mode also re-solves each
position under default verifier bounds, re-runs difficulty scoring, and
recomputes proof-derived public diversity metadata.

Exit statuses:

- `0`: requested operation passed;
- `1`: integrity/deep verification failed;
- `2`: invalid request or filesystem/application error;
- `3`: bounded generation could not supply the exact requested count;
- `4`: output collision or unsafe output path;
- `5`: explicit Stockfish startup/configuration/analysis failure;
- argparse syntax/choice errors also use `2`.

## Reproducibility

Retain the request, seed, component versions, dependency versions, and font
hash. Do not compare elapsed time or output directory path; neither is stored
in deterministic content. Compare `manifest.json` and artifact bytes directly,
or use SHA-256.

## Maintenance rules

- Never bypass `MateVerifier` when adding candidate sources.
- Keep generator streams finite and deterministic.
- Keep Stockfish at one thread with fixed node limits; never publish its PV,
  score, mate value, or best move.
- Increment a component version when its deterministic contract changes.
- Keep recent conversational diversity state public-safe, bounded, and outside
  downloadable output and private-solution roots.
- Never serialize `VerificationResult` at the public boundary.
- Run the full test suite, acceptance CLI commands, integrity checks,
  `git diff --check`, and staged-content audit before release.
- Keep caches, local outputs, databases, and diagnostic logs below ignored
  `runtime/` or outside the repository; keep demonstrated evidence in
  `evidence/`.

## AnythingLLM Skill

The live Skill/service paths, update commands, health checks, attachment
delivery, and natural-language defaults are in
[`ANYTHINGLLM-SKILL.md`](ANYTHINGLLM-SKILL.md). Normal users should state only
the chess request; the Skill owns output paths and bounded technical defaults.
