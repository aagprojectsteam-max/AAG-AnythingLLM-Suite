# Verification Contract

Version: `aag-bounded-mate-v1`

## Authority boundary

An AAG puzzle is accepted only by the deterministic verifier in
`aag_chess.verifier`. The LLM never decides chess correctness. Stockfish may
find or order candidates and may cross-check a result, but an engine score,
principal variation, or displayed mate value is never an acceptance proof.

Legal move generation and terminal recognition are supplied by the pinned
`chess==1.11.2` rules library. The project-owned verifier performs the bounded
proof search, minimax quantification, exact-distance check, uniqueness policy,
dual classification, resource accounting, and certificate construction.

## Supported proposition

V1 supports `N ∈ {1, 2, 3}`. For a structurally valid FEN:

- the attacker is the side to move at the root;
- target distance is `D = 2*N - 1` plies;
- on an attacker node, at least one legal continuation must force checkmate;
- on a defender node, every legal reply must retain a forced checkmate;
- attacker choice minimizes mate distance;
- defender choice maximizes mate distance;
- a terminal success exists only when the side to move is checkmated and is
  the defender;
- stalemate, insufficient material, an exhausted depth, or a defender escape
  is failure for that branch.

The root is exact mate in N precisely when its minimax distance is D. A root
with a forced mate at a shorter distance is rejected as
`mate_distance_not_exact`.

## Key and dual policy

For the default profile, the root must have exactly one move attaining the
proven result. Zero keys is no mate; more than one is `key_not_unique`.

A dual is more than one equally optimal attacker continuation at an attacker
node after the key. The proof records the ply, preceding UCI line, and all dual
moves. V1 exposes three policies:

- `forbid` — conservative production default; reject any detected dual;
- `warning` — accept but return `accepted_with_dual_warning` and metadata;
- `allow` — accept and retain the metadata.

This is a mechanical dual definition, not a claim of artistic quality.

## Position validity and provenance

Parsing success is not sufficient. `Board.is_valid()` must pass, which rejects
structural defects such as missing or excessive kings, adjacent kings,
impossible check configurations, pawns on back ranks, and invalid rights.

Structural validity does not prove historical reachability. Provenance is
stored separately. The V1 built-in generator uses arbitrary KQK composition
templates expanded by symmetry and color mirror. Public output labels them
`arbitrary_composition_template` with `retro_legality_proven=false`. Arbitrary
supplied FENs are classified as `external_fen`; no retro-legality claim is
made.

The optional Stockfish backend creates density-aware candidates with
bounded material profiles and edge-biased mating geometry. Accepted public
constructed positions are labeled `stockfish_assisted_material_constructed`
with `retro_legality_proven=false`; forward-playout positions use
`stockfish_assisted_game_like` with legal provenance. Stockfish scores do
not change the supported proposition or acceptance rules.

## Normalization and deduplication

The normalized position contains board placement, side to move, castling
rights, and only a legally effective en-passant square. Halfmove and fullmove
counters are omitted because they do not change legal move generation inside
the five-ply V1 proof horizon. The stable problem hash also includes requested
mate depth and a version tag.

## Bounds and termination

Every invocation has explicit `max_nodes` and `max_seconds` bounds. Each proof
node consumes budget, including transposition hits. Search keys include the
normalized position and remaining depth. A transposition cache prevents
re-solving the same bounded state. Exhaustion returns a structured rejection;
it never becomes an inconclusive acceptance.

Production defaults are 500,000 nodes and 10 seconds per candidate, capped by
the service. Maximum supported proof depth is five plies, so recursion is
strictly bounded.

## Certificate

An accepted result contains a machine-readable proof tree:

- normalized FEN and minimax distance at each node;
- node role (`attacker` or `defender`);
- UCI and SAN for every included branch;
- all legal defender branches;
- optimal attacker continuation branches;
- explicit checkmate terminals.

The certificate identifier is SHA-256 over canonical UTF-8 JSON with sorted
keys and compact separators. Ordering is stable because legal moves are sorted
by UCI. The certificate proves the bounded contract for the pinned verifier
and rule-library semantics; it is not a general retro-legality proof.

## Difficulty boundary

`aag-difficulty-v1` runs only after an accepted exact proof. Mate depth is the
dominant score feature, with bounded contributions from root legal-move count,
defensive proof branching, detected duals, and whether the key checks. Scores
0–34 are `easy`, 35–69 `medium`, and 70–100 `hard`. This is a reproducible
mechanical classification, not an artistic or human-performance claim.

The scorer cannot accept a position and never weakens, substitutes for, or
changes a verifier result. A difficulty mismatch only prevents an already
verified candidate from satisfying the requested product category.

## Public release contract

The complete verifier result is private solution-bearing data. Public
manifests and images are constructed through explicit allowlists and never
contain key moves, UCI/SAN continuations, proof trees, dual lines, certificate
content/hashes, solution comments, or solution-bearing filenames/metadata.
V1 has no public solution-export interface.

An artifact integrity check validates public schema and hashes without proving
chess truth. The explicit `verify-output --deep` mode additionally re-runs this
contract for each stored position.

## Stockfish-assisted discovery boundary

Stockfish runs privately through UCI with `Threads=1`, `Hash=16`, strength
limiting disabled, a clear hash before each candidate, fixed nodes, and bounded
startup/analysis timeouts. Candidate attempts and engine nodes are capped.
Normal and exceptional paths close the process.

An engine result is never converted to `accepted=True`. Only candidates whose
private engine mate score matches the requested discovery target are submitted
to the existing `MateVerifier`, which independently proves exact distance,
unique key, dual policy, and all defensive replies. Public manifests may state
the backend/version and aggregate counts, but exclude evaluations, mate scores,
PV, best move, key, continuation, proof, and certificate.

## Density boundary

`aag-density-v1` classifies only total public piece count: sparse 3–9 (the
Stockfish constructor uses 5–9 and the legacy builtin may emit three), normal
10–16, and rich 17–26. Automatic selection and batch balancing derive only
from the request seed. Density does not contribute to difficulty, proof, or
acceptance. Stockfish filters and `MateVerifier` proves the complete position,
including every density piece; no pre-existing mate is grandfathered after
material is added.
