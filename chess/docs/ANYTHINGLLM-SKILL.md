# AAG Chess Puzzle AnythingLLM Skill

## Installed integration

- Skill name: **AAG Chess Puzzle**
- AnythingLLM version inspected: `1.15.0` in container `anythingllm`
- Host Skill path:
  `/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-chess-puzzle`
- Container Skill path:
  `/app/server/storage/plugins/agent-skills/aag-chess-puzzle`
- Chess executable:
  `/mnt/data/AI/Agents/AAG-Chess-Puzzle-Agent/.venv/bin/aag-chess`
- Isolated source-output root:
  `/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/outputs`
- Private follow-up root (never downloadable):
  `/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/private-solutions`
- Host bridge socket:
  `/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/bridge.sock`
- User service: `aag-chess-anythingllm-bridge.service`

The installed release uses AnythingLLM's imported `skill-1.0.0` format. The
active loader reads installed Skill configuration for each new agent
invocation, so installing or updating the Skill itself does not require a
container restart. The host bridge user service must be enabled and running.

## Architecture and authority

```text
natural-language request
  -> AnythingLLM AAG Chess Puzzle Skill
  -> validated JSON over mounted Unix socket
  -> exact local aag-chess argv (never a shell)
  -> builtin/Stockfish-assisted discovery
  -> MateVerifier acceptance authority
  -> renderer and public manifest
  -> aag-chess verify-output --deep
  -> native inline image + generated-file download cards

explicit hint/solution follow-up
  -> trusted workspace/thread/user scope selects its latest private context
  -> private context selects only a public ID from that batch
  -> verify-output --deep + fresh MateVerifier proof
  -> conservative hint or vertically formatted verified SAN solution text
```

The Skill contains no chess engine, generator, scorer, solver, or rendering
logic. Stockfish remains a private candidate-discovery/filtering aid. Neither
Stockfish nor the LLM can create an accepted result.

## Hints and solutions

Generation remains unsolved. The bridge stores an opaque capability and a
private latest-context pointer keyed by AnythingLLM's trusted
workspace/thread/user scope. No capability, context marker, hash, or internal
ID is placed in assistant text or exposed to the chat model. Supported
follow-ups include:

```text
תן לי רמז
עוד רמז
מה הפתרון?
מה הפתרון לחידה 2?
תן לי את הפתרון המלא לחידה האחרונה
```

The default selection is the last puzzle in the latest generated batch. A
1-based puzzle number can select another puzzle in that batch; the internal
public ID remains available to the validated bridge but need not be displayed.
The first hint says only whether the verified key gives check; a
second hint identifies the verified moving piece and origin; a third can state
the key without the full proof. A solution displays verified SAN vertically,
separates defensive branches, preserves `+` and `#`, and concludes with mate.
UCI remains in private structured data and is not part of normal visible text.
Unicode LTR isolate characters surround move numbers and SAN; this avoids
unsafe HTML while keeping notation readable inside Hebrew RTL paragraphs.

Private records contain no moves or proofs: only an unguessable context token,
conversation ownership, output-directory UUID, ordered public IDs, and hint
levels. A separate mode-0600 pointer maps the validated conversation scope to
its latest token. Every retrieval deep-verifies the public directory and
recreates the proof with `MateVerifier`. Records persist until the operator
removes the dedicated private state and corresponding output. Scope mismatch,
invalid IDs, and path traversal fail closed.

## Natural-language defaults

The user only needs to state chess intent. This complete request needs no
technical follow-up:

```text
תעשה לי חידה מט ב-3 לשחור
```

It resolves to `mate=3`, `side=black`, `count=1`, requested
`difficulty=medium`, `engine=auto`, `density=auto`, and `formats=png`. When seed
is omitted, the bridge automatically varies consecutive results using bounded,
solution-free conversation history. Other omitted technical defaults are `max_attempts=1000` and
`stockfish_nodes=20000`.

`aag-difficulty-v1` mechanically classifies generated Mate-in-1 as easy and
Mate-in-3 as hard. When *medium is omitted and supplied only by the Skill
default*, the adapter uses the feasible measured class for the CLI filter while
preserving `requested_difficulty=medium` and reporting the measured class.
An explicit difficulty is never rewritten; an impossible explicit
mate/difficulty combination fails honestly.

Supported structured values are:

- `mate`: required integer 1, 2, or 3;
- `side`: `white` or `black`, default `white`;
- `difficulty`: `easy`, `medium`, or `hard`, default request `medium`;
- `count`: 1–10, default 1;
- `engine`: `auto`, `stockfish`, or `builtin`, default `auto`;
- `formats`: `png`, `svg`, or `svg+png`, default `png`;
- `density`: `auto`, `sparse`, `normal`, or `rich`, default `auto`;
- `seed`: optional non-negative safe integer; supply it for reproducible mode,
  or omit it for conversational auto-diversity;
- `max_attempts`: 1–2000, default 1000;
- `stockfish_nodes`: 100–200000, default 20000.

Advanced values are optional. Ordinary users should not be asked for an
engine, format, seed, output path, attempt budget, node budget, manifest,
renderer, or verifier. Explicit user choices override defaults.

Automatic density is seed-derived and favors normal/rich positions; it is
separate from difficulty. The Skill maps `הרבה כלים` and `לוח מלא יותר` to
`rich`, and `מעט כלים` to `sparse`. Users never need to mention density.

The auto-diversity history retains at most 30 public-safe entries per
workspace/thread/user scope and compares against the most recent 20. It stores
only public ID, source family, motif, material/pawn signatures, king regions,
and density—never FEN, SAN/UCI, proof, certificate, or solution. Similarity
relaxes deterministically after bounded retries. An explicit seed bypasses
history-based selection.

Examples understood by the Skill include:

```text
תייצר לי מט בשניים
תן לי 3 חידות קשות מט ב-3
תן לי חידת מט ב-2 לשחור
תכין 10 חידות מט בשניים עם PNG ו-SVG
תשתמש ב-Stockfish ותיצור חידת מט בשניים
תשתמש במחולל המובנה ותיצור חידת מט באחד
תעשה לי חידה מט בשניים עם לוח מלא יותר
```

## Files and security

Every request receives an application-controlled UUID directory below the
isolated output root. The Skill cannot accept an output directory or filename.
It validates all enum and integer values, rejects extra fields, caps body,
response, manifest, and artifact sizes, rejects traversal and symlinks, and
uses a 240-second generation timeout with process-group termination. The bridge
uses explicit argv and `shell=False`; it is reachable only through the mounted
Unix socket.

After `verify-output --deep` passes, the handler rechecks paths and SHA-256 and
copies only PNG, SVG, and the public manifest into AnythingLLM's native
`storage/generated-files` facility. AnythingLLM returns authenticated
`fileDownloadCard` attachments and persists `TextFileDownload` output records
for chat history. The browser route is
`/api/agent-skills/generated-files/<storageFilename>`; the authenticated local API
route is `/api/v1/document/generated-files/<storageFilename>`.

For inline display, the handler also uses AnythingLLM 1.15.0's
`saveGeneratedImage`, `imageGenerationCard`, and ownership-checked
`/api/image-generation/generated-images/<img-UUID.png>` route. The active chat
row is narrowly primed with that one public image output before the browser
fetch, eliminating the installed version's save/render race. The LLM receives
the exact browser URL and is forbidden to invent a path or invoke any other
image generator. A native output remains in persisted chat history. No file
server, `file://` URL, base64 chat blob, filesystem path, or public access to
the private solution root is used.

The bridge retains public IDs, hashes, component versions, attachment metadata,
and integrity results internally. The Skill's model-facing projection contains
only a short Hebrew introduction, browser-usable inline-image URLs, and whether
download cards were registered. Normal chat never receives context markers,
storage filenames, hashes, versions, engine diagnostics, or manifest internals.
It also never returns a key move, continuation, proof, certificate, Stockfish
PV, best move, or private verifier data. Only explicit `hint` or `solution`
actions may return verifier-derived human-readable text.

## Install or update

From the repository root:

```bash
install -d -m 0755 \
  /mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-chess-puzzle
install -m 0644 integrations/anythingllm/skill/aag-chess-puzzle/{plugin.json,handler.js,README.md} \
  /mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-chess-puzzle/
install -d -m 0755 /home/aag-linux/.config/systemd/user
install -m 0644 \
  integrations/anythingllm/systemd/aag-chess-anythingllm-bridge.service \
  /home/aag-linux/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aag-chess-anythingllm-bridge.service
```

The initial install is discovered dynamically. After replacing a handler that
has already been loaded, restart only the `anythingllm` container to clear
Node's module cache. Restart the bridge user service when its Python code or
unit arguments change.

## Troubleshooting

```bash
systemctl --user status aag-chess-anythingllm-bridge.service
curl --unix-socket \
  /mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/bridge.sock \
  http://localhost/health
.venv/bin/aag-chess engine-info
docker inspect -f '{{.State.Status}} {{.RestartCount}}' anythingllm
```

If explicit Stockfish discovery fails, verify `/usr/games/stockfish` with
`aag-chess engine-info`; `auto` can fall back to builtin. Bounded-search errors
mean the exact requested set was not found and no partial success is reported.
Raw stack traces are not returned to normal users. Do not delete the shared
AnythingLLM database or generated-files directory while troubleshooting.
