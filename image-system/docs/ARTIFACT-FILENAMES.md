# Persisted generated-image filenames

New AAG presentation artifacts are stored physically as
`AAG-Image-WORKSPACE-THREAD-YYYYMMDD-HHMMSS.ext`, then `-2.png`, `-3.png`, etc. on collision.
The filename contains no prompt, title, provider, or internal UUID. The extension follows the validated format: PNG → `.png`, JPEG → `.jpg`, WebP → `.webp`. Storage and downloads retain the original bytes. Non-PNG images are decoded to PNG only inside on-demand PDF assembly.

Workspace numbers are persistent user-facing AAG serials in
`aag_workspace_serials`, separate from native database workspace IDs. The first
allocation uses an authenticated sidebar snapshot. A new artifact's thread number
is the current one-based ordinal in the authenticated, ordered visible thread
list, using additive display positions in `aag_thread_display_order`. Deletion
and reorder immediately change current display numbers; native IDs remain stable
for persistence, ownership, sockets and recovery. Each filename records the
ordinal at creation permanently. Show Details distinguishes the current ordinal
from the immutable creation ordinal. Earlier serial tables remain for historical
compatibility; they do not define the current visible thread number.
The native unthreaded conversation retains reserved thread serial **0**.
`aag_artifact_serials` preserves the artifact's workspace binding. The earlier
`aag_thread_serials` and filename ledger continue to resolve historical names.
No historical artifact is renamed and no native identity is rewritten.

The authoritative time is the persisted artifact's `verified_at` event, created
by `src/adapters.js` after the engine output passes decoding and hash validation.
Canonical metadata remains **UTC**. For a new filename, the private host lifecycle
socket's read-only `/artifacts/timezone` endpoint resolves the Ubuntu host's
`/etc/localtime` IANA zone (this installation: **Asia/Jerusalem**). A copied TZif
file uses `/etc/timezone` only after byte equality with that zone's installed
TZif file. The server applies `Intl.DateTimeFormat` Gregorian/ASCII/h23 formatting
using the rules at `verified_at`, including DST and local date rollover. There
is no fixed offset, browser timezone, container clock, or UTC fallback. Missing
host timezone fails allocation safely; recovery can retry once the host returns.
The timezone endpoint accepts no client zone or path arguments.

`aag_artifact_filenames` persists the exact
assigned filename, original timestamp, timezone, workspace/thread serials, hash,
job ID and artifact ID once. Recovery and repeated presentation reuse that row.
The card's `filename` and `storageFilename` are identical. Download uses that
persisted name; Details and reveal measure the same physical file through the
existing authoritative resolver. Internal publisher filenames and identities
remain internal and unchanged.

The additive mapping tables are created idempotently in the existing AnythingLLM SQLite
database through its existing Prisma client. There are no changes to Prisma's
native models, workspace/thread/chat rows, or unrelated persistence. Rollback to
an older application can leave these additive tables in place safely. Do not
delete this ledger while retaining numeric artifact filenames: its tombstones
preserve historical serial and filename reservations.

Allocation holds a SQLite write reservation before reading the namespace. An
exclusive `O_CREAT|O_EXCL|O_NOFOLLOW` file write, file fsync and directory fsync
precede committing the immutable identity binding. Existing files (including
partial files left by an interrupted allocation) are skipped, never overwritten.
Ledger reservations remain occupied even if a file is later removed. A crash
before commit can leave an unreferenced file and consume a collision suffix; a
retry safely allocates the next suffix. A missing or changed committed file fails
closed instead of silently replacing it. Symlink roots/files are rejected.

Historical chat outputs retain their existing filenames and bytes. If an old
persisted output is replayed, its job/artifact identity resolves its saved legacy
name before any new allocation. All filename allowlists accept both schemes;
filenames never supply workspace authorization or replace artifact identity.
The native files and serving overlays adjust only the allowlist and MIME handling for the validated extensions. The authoritative host resolver reads non-PNG metadata through the already-installed Pillow decoder from the same safe file descriptor. No user-supplied path or destination is introduced.

Tests exercise the ledger through real SQLite, and isolated integration also
uses the installed Prisma 5.3.1 client. Validation includes concurrent allocation,
identical-pixel siblings, restart, rename, reordered queries, deletion, historical
replay, resolver security, actual image generation and desktop file selection.

Already-persisted filenames are returned before resolving or converting any timezone. Historical UUID names and the earlier numeric candidate names remain readable and are never renamed.
