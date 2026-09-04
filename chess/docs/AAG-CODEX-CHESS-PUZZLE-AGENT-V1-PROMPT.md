# AAG Chess Puzzle Agent V1 — Codex Implementation Mission

Date: 2026-08-29

Project root:

`/mnt/data/AI/Agents/AAG-Chess-Puzzle-Agent`

## 1. Mission

Build a small but production-quality local system named:

**AAG Chess Puzzle Agent**

Its primary user interface must ultimately be AnythingLLM.

The user should be able to write naturally in Hebrew:

> צור לי 3 חידות מט בשני מסעים, הלבן מתחיל.  
> אל תגלה את הפתרונות. תן לי תמונה של כל חידה.

The system must:

1. interpret the bounded chess request;
2. generate candidate positions;
3. prove that accepted candidates satisfy the requested mate contract;
4. reject failures;
5. store accepted puzzles and their private solutions;
6. render exact SVG and PNG diagrams;
7. return usable images and puzzle identifiers through AnythingLLM;
8. later resolve requests such as:
   `תן לי את הפתרון של חידה 2`.

This is a real local product, not a demonstration script.

Do not stop after architecture planning. Inspect first, record the facts, then
proceed through implementation and tests.

## 2. Known starting fact

Stockfish was installed successfully from Ubuntu packages and manually
started as Stockfish 17.1.

Do not assume its absolute path. Discover and record it using the live host.

Do not redownload Stockfish unless inspection proves that the existing
installation is unusable.

No NVIDIA GPU exists or is required. The chess core is CPU-based.

## 3. Project isolation

This project must remain separate from:

`/mnt/data/AI/Agents/AAG-Ubuntu-Agent`

That existing agent may be inspected read-only as a reference for established
AnythingLLM bridge or skill patterns, but it must not be modified.

Do not place chess domain logic inside the Ubuntu agent.

## 4. Mandatory inspection phase

Before implementation, inspect and record at least:

- operating system and Python version;
- Codex-visible project permissions;
- exact Stockfish executable and version;
- basic UCI startup and shutdown;
- available CPU count and memory;
- current Docker state;
- the live AnythingLLM deployment;
- AnythingLLM version;
- active AnythingLLM storage location;
- installed custom skills/tools;
- the existing AAG AnythingLLM integration pattern;
- how the browser currently reaches AnythingLLM;
- how a skill can return text, Markdown, URLs or files;
- whether images referenced by a tool response are actually rendered;
- occupied local ports;
- available SVG-to-PNG rendering options;
- installed fonts suitable for Hebrew captions;
- existing backup conventions.

Write:

`evidence/phase0-live-inspection.md`

Separate every finding into:

- verified fact;
- reasonable inference;
- unresolved item.

Do not mutate AnythingLLM during this inspection.

## 5. Architectural baseline

Use a modular architecture with independently testable components. A sensible
baseline is:

- request/schema layer;
- legal-position generator;
- deterministic mate proof verifier;
- Stockfish adapter;
- quality and difficulty scorer;
- deduplication component;
- SQLite repository;
- SVG/PNG renderer;
- narrow local service/API;
- AnythingLLM skill/tool adapter;
- artifact delivery mechanism;
- command-line maintenance interface;
- automated tests.

You may adjust this after live inspection, but document the reason.

Avoid unnecessary infrastructure. This is a single-user local system.

Use a project-local virtual environment, preferably:

`.venv`

Pin direct dependencies and record their versions.

## 6. Exact mate contract

Implement a written formal contract in:

`docs/VERIFICATION-CONTRACT.md`

For V1, support proof verification for N = 1, 2 and 3.

Interpret “mate in N” as follows:

- the attacker is the side to move;
- the attacker has a forced checkmate;
- under optimal defending play, the distance to checkmate is exactly
  `2*N - 1` plies;
- therefore there is no forced mate in fewer attacker moves;
- the configured key-move policy is satisfied.

Examples:

- Mate in 1: mate after the attacker's first move.
- Mate in 2: attacker key, defender reply, attacker mate.
- Mate in 3: five plies under the longest optimal defense.

The proof verifier must:

- enumerate legal moves at every proof node;
- correctly recognize checkmate;
- reject stalemate;
- reject structurally invalid boards;
- evaluate all legal defender replies;
- prove that the attacker has a continuation for every defense;
- compute or prove the bounded distance to mate;
- use memoization or a transposition cache;
- produce a machine-readable proof tree or compact proof certificate;
- terminate under an explicit depth and resource bound.

Stockfish may:

- propose candidate moves;
- order branches;
- locate likely mating positions;
- independently cross-check a stored solution.

Stockfish must not be the only reason a puzzle is declared valid.

For the default puzzle profile require exactly one valid first move: a unique
key.

Track second-move duals separately. Do not silently call a dual-heavy problem
a clean composition. For V1 they may be configurable as:

- forbidden;
- allowed but marked;
- quality warning.

Use the conservative default justified by tests.

## 7. Position legality and provenance

A structural board-validity check is not a proof that an arbitrary position
is reachable from the initial chess position.

Store separate metadata such as:

- `structurally_valid`;
- `provenance_method`;
- `provenance_verified`;
- `retro_legality_claim`.

For V1, prefer generation methods that preserve legal provenance, such as
positions obtained through recorded legal forward move sequences.

If reverse construction is used, do not claim full historical reachability
unless it is actually proven.

## 8. Generation strategy

Do not use an LLM as the primary chess-position generator.

Implement a reproducible candidate-generation pipeline. It may combine:

- seeded legal forward play;
- lightweight engine-guided self-play;
- scanning generated legal games for bounded mates;
- controlled legal mutation of proven positions;
- reverse construction from mating nets, provided provenance is honestly
  classified;
- deterministic random seeds;
- Stockfish candidate discovery.

Keep fixed known positions only as test fixtures. Do not pass the end-to-end
generation acceptance merely by returning bundled fixtures.

Every generated puzzle record must include:

- generation method;
- RNG seed where applicable;
- source/provenance chain;
- candidate count;
- rejection reasons;
- verification result;
- engine settings;
- elapsed time.

Generation must have explicit budgets:

- maximum candidates;
- maximum wall time;
- maximum engine processes;
- maximum requested batch size.

If the budget is exhausted, return an honest partial result or structured
failure. Never fabricate a puzzle.

V1 end-to-end generation must reliably produce Mate-in-2 puzzles.

The proof verifier should support Mate in 3, but do not declare Mate-in-3
generation production-ready unless a measured benchmark and acceptance gate
pass.

## 9. Deduplication

Implement exact normalized-position deduplication in V1.

At minimum, normalize and hash the fields that define the chess problem,
while excluding irrelevant counters only when mathematically safe.

Include:

- stable puzzle hash;
- database uniqueness constraint;
- duplicate rejection reason.

Symmetry-aware deduplication may be added only after its transformations are
fully tested, particularly around pawn direction, castling and en-passant.
Do not introduce incorrect equivalence merely to claim advanced
deduplication.

## 10. Storage

Use SQLite unless live inspection provides a compelling local reason not to.

Store at least:

- puzzle ID;
- public short ID;
- batch ID;
- creation timestamp;
- normalized FEN;
- original FEN;
- attacker side;
- requested mate depth;
- exact proven mate distance;
- unique-key result;
- dual information;
- solution line and defense tree;
- proof certificate;
- structural validity;
- provenance metadata;
- generation method and seed;
- Stockfish executable and version;
- engine configuration;
- verifier version;
- difficulty metadata;
- artifact paths and URLs;
- hashes;
- status;
- rejection/failure details.

Solutions must remain retrievable without regenerating the puzzle.

Use transactions and schema migrations.

## 11. Deterministic diagrams

Generate the board from the stored position, never from free-form text and
never through image-generation AI.

Produce:

- SVG as the canonical vector artifact;
- PNG as a high-resolution sharing artifact.

Create a clean puzzle card containing, where practical:

- puzzle identifier;
- Hebrew title;
- “מט ב־N מסעים”;
- side to move;
- difficulty label;
- exact board;
- coordinates;
- no solution markings in the unsolved image.

Inspect available Hebrew fonts and package no proprietary font files.

The renderer must be deterministic for the same normalized input and renderer
version.

Test at least:

- piece count;
- piece-to-square mapping;
- board orientation;
- side-to-move orientation policy;
- coordinates;
- SVG validity;
- PNG dimensions;
- non-empty output;
- artifact hash;
- absence of solution leakage.

Do not rely only on visual inspection.

## 12. Difficulty

Implement a transparent preliminary V1 heuristic rather than an unsupported
claim about human difficulty.

Potential signals include:

- number of legal candidate keys;
- forcing versus quiet key;
- number of defenses;
- branching factor;
- checks and captures;
- solution uniqueness;
- dual count;
- piece count;
- thematic features that can be detected reliably.

Store the raw feature values.

Label the V1 score explicitly as heuristic and version it.

Support broad `easy`, `medium` and `hard` filtering only after tests show the
filter behaves consistently. Do not claim an Elo-equivalent rating.

## 13. Local API boundary

Expose a narrow, validated domain API. Suggested operations include:

- health;
- create puzzle batch;
- retrieve puzzle;
- retrieve hint;
- retrieve solution;
- retrieve artifact;
- verify supplied FEN for maintenance/testing;
- list recent batches.

Do not expose:

- shell command execution;
- arbitrary file reads;
- arbitrary executable paths;
- unrestricted SQL;
- arbitrary URLs.

Use strict schemas and bounds, including:

- allowed mate depths;
- allowed batch counts;
- legal side values;
- maximum request length;
- time and candidate budgets.

Bind to loopback or the minimum proven local interface.

If a user-level systemd service is appropriate, create it only after the
standalone service passes tests. Do not enable boot startup until the
end-to-end acceptance succeeds.

## 14. AnythingLLM integration

AnythingLLM is the primary user interface and is not optional.

Inspect the actual installed version and use its real supported custom
skill/tool mechanism. Do not design against an assumed version.

Implement narrowly scoped tools equivalent to:

- `create_chess_puzzles`;
- `get_chess_puzzle`;
- `get_chess_puzzle_hint`;
- `get_chess_puzzle_solution`;
- `chess_puzzle_health`.

The create response must contain:

- batch ID;
- stable puzzle IDs;
- Hebrew user-facing description;
- actual image references;
- no private solution when `show_solutions=false`.

Do not return `file://` paths or container-only paths to the browser.

Prove the complete artifact path:

`backend artifact → tool response → AnythingLLM message → browser-visible image`

AnythingLLM may run in Docker while the browser runs on the host. Account for
both network perspectives. Do not assume that a URL reachable inside the
container is reachable by the browser, or vice versa.

Choose and prove one reliable method, such as:

- a loopback artifact HTTP service reachable by the browser;
- a verified reverse-proxy route;
- an existing AnythingLLM-supported attachment mechanism.

Use actual rendered output, not merely a successful HTTP status, as the
acceptance criterion.

## 15. Conversation and batch references

The user must be able to ask:

> תן לי את הפתרון של חידה 2.

Implement stable IDs and a stored batch relationship.

If AnythingLLM supplies a thread/session identifier to tools, use it safely.

If it does not, return explicit batch and puzzle IDs and maintain the most
recent batch mapping available to the skill.

The system must not guess the solution from chat text. It must retrieve the
stored verified solution.

Hints should be generated from stored proof metadata and must not
accidentally reveal the entire solution unless requested.

## 16. Hebrew behavior

Test real Hebrew requests including:

- singular puzzle;
- multiple puzzles;
- Mate in 1;
- Mate in 2;
- “בלי פתרונות”;
- “תן רמז”;
- “תן פתרון לחידה 2”;
- invalid or excessive batch counts;
- ambiguous but safely resolvable phrasing.

The domain API itself should receive normalized structured parameters rather
than rely on uncontrolled natural-language parsing.

## 17. Tests

Create automated:

### Unit tests

- FEN parsing and normalization;
- structural invalidity;
- checkmate and stalemate distinction;
- known Mate-in-1 proof;
- known Mate-in-2 proof;
- known Mate-in-3 proof;
- no-mate position;
- false engine-mate claim fixture;
- multiple-key rejection;
- unique-key acceptance;
- dual classification;
- exact depth versus shorter mate;
- proof certificate stability;
- deduplication;
- database transactions;
- renderer mapping;
- API validation.

### Integration tests

- Stockfish process lifecycle;
- engine timeout and crash handling;
- generation → verification → storage;
- storage → rendering;
- service → artifacts;
- artifact URL retrieval;
- restart persistence;
- concurrent bounded requests;
- no orphan Stockfish processes.

### AnythingLLM integration tests

- skill schema;
- Hebrew parameter extraction;
- tool request to local backend;
- Markdown/image response;
- solution withholding;
- later solution retrieval;
- actual artifact accessibility from the browser path.

### Negative tests

- invalid FEN;
- impossible mate request;
- count above limit;
- unsupported mate depth;
- path traversal;
- SQL-like input;
- arbitrary command input;
- missing artifact;
- stale batch;
- engine unavailable;
- exhausted generation budget.

## 18. Phased gates

### Phase 0 — Live inspection

PASS only with a written fact report and no unrecorded mutations.

### Phase 1 — Core proof verifier

PASS only when Mate-in-1/2/3 positive and negative fixtures pass and exact
depth plus unique key are proven.

### Phase 2 — Deterministic renderer

PASS only when SVG and PNG mapping tests pass.

### Phase 3 — Generator

PASS only when reproducible legal-provenance generation produces verified
Mate-in-2 candidates without returning bundled fixtures.

### Phase 4 — Storage and local service

PASS only when persistence, validation, bounded execution and artifact
delivery tests pass.

### Phase 5 — AnythingLLM integration

PASS only when the real installed AnythingLLM invokes the actual local tool
and displays a real puzzle image.

### Phase 6 — End-to-end acceptance

From AnythingLLM, execute the equivalent Hebrew request:

> צור לי 3 חידות מט בשני מסעים, הלבן מתחיל.  
> אל תגלה לי את הפתרונות. תן לי תמונה של כל חידה.

PASS only if:

1. AnythingLLM receives and routes the request.
2. Three non-fixture candidate positions are produced.
3. Each has legal-generation provenance or an honestly documented
   provenance classification.
4. Each passes exact Mate-in-2 proof.
5. Each passes the configured unique-key contract.
6. Each has a stored proof and solution.
7. Each has accurate SVG and PNG artifacts.
8. All three images are usable through the AnythingLLM browser workflow.
9. No solution is exposed.
10. A later request for the solution of puzzle 2 returns the stored verified
    solution for that exact puzzle.
11. Restarts do not lose the batch or puzzle records.
12. Evidence is saved.

Do not declare V1 complete if this gate has not passed.

## 19. Resource policy

Benchmark before tuning.

Use conservative defaults for:

- Stockfish Threads;
- Stockfish Hash;
- engine process count;
- node/time limits;
- generation candidate limits;
- batch size.

Do not consume all 64 GB of RAM or all CPU threads by default.

Record benchmark inputs and outputs.

Ensure every engine process is shut down in success, timeout and exception
paths.

## 20. Git, backups and changes

Initialize and use Git in the project.

Create checkpoints after:

- inspection;
- verifier;
- renderer;
- generator;
- backend;
- AnythingLLM integration;
- final acceptance.

Before changing AnythingLLM files, create timestamped backups and hashes.

Do not alter its configured LLM provider or unrelated workspaces.

Do not restart unrelated services.

## 21. Required deliverables

At minimum create:

- `README.md`
- `pyproject.toml` or an equally reproducible dependency definition
- `.gitignore`
- source package
- migrations/schema
- tests
- CLI
- local backend/service
- deterministic renderer
- generator
- verifier
- AnythingLLM integration files
- user-service files if actually adopted
- `docs/ARCHITECTURE.md`
- `docs/VERIFICATION-CONTRACT.md`
- `docs/GENERATION-STRATEGY.md`
- `docs/ANYTHINGLLM-INTEGRATION.md`
- `docs/SECURITY-BOUNDARIES.md`
- `docs/OPERATIONS.md`
- `docs/MASTER-HANDOFF.md`
- phase evidence under `evidence/`

Document exact commands for:

- start;
- stop;
- status;
- health;
- tests;
- manual puzzle generation;
- database backup;
- rollback;
- AnythingLLM use.

## 22. Final report

Return a concise but evidence-based final report containing:

- final status;
- implemented scope;
- project path;
- Stockfish path/version;
- architecture;
- mate contract;
- test counts;
- generated acceptance puzzle IDs;
- artifact URLs or paths;
- AnythingLLM skill/tool name and version;
- service state;
- known limitations;
- deferred roadmap;
- Git commit;
- rollback information.

Use exactly one of these final classifications:

- `AAG_CHESS_PUZZLE_AGENT_V1_LIVE_VERIFIED`
- `AAG_CHESS_PUZZLE_AGENT_V1_CORE_PASS_INTEGRATION_BLOCKED`
- `AAG_CHESS_PUZZLE_AGENT_V1_VERIFICATION_FAILURE`
- `AAG_CHESS_PUZZLE_AGENT_V1_ENVIRONMENT_BLOCKED`

Do not use the LIVE_VERIFIED classification without the real AnythingLLM
end-to-end acceptance.

Begin now with Phase 0 inspection, save the report, then continue through the
implementation gates autonomously.
