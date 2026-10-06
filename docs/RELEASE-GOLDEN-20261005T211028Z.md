# Production release 20261005T211028Z

Completed images now appear in the active conversation without refresh, including
responses that carry structured image outputs with empty text. Stream updates,
retransmission and reload preserve a single artifact card. Per-job publication
serialization keeps live persistence and recovery from publishing concurrently.

User-facing Thread numbers follow the current ordered thread list. Deleting or
reordering threads changes their visible ordinals immediately. Native IDs remain
the keys for persistence, ownership, routing, recovery and concurrency. A saved
filename retains its creation ordinal; Details shows the current ordinal
separately. Historical images and filenames are preserved.

The approved manifest SHA-256 is
`ebb4b5bdfc687a96122b0f833b8884a380b7cd4eb15896685c08cc295e47205b`.
Source mappings, installer overlays and the original frontend build provenance
are recorded in the release manifests. The production rollback source is
`20261005T133425Z`.

Before deployment, 745 automated checks and the isolated browser matrix passed.
The user completed real-engine installed-app validation: live presentation,
exactly-once rendering, filename display, exact file selection, reload/reopen,
thread switching and deletion renumbering all passed. A one-shot quota rejection
was isolated to the validation harness and is not a product regression.

After deployment, 45 non-generative production smoke checks passed. Runtime files
and served frontend assets matched the validated candidate. No additional
production image was generated for these checks. Publication preserves the
candidate and production unchanged, and performs no rebuild or restart.

Permanent tests cover the pinned upstream stream reducer, structured image
hydration, delayed image decoding, publication/recovery serialization, failure
release, fresh workspaces, deletion/reorder, SQLite reopen, immutable filenames
and stable native IDs. Raw logs, user prompts, databases, browser profiles,
generated images, compiled builds and environment-specific harnesses remain
outside the public repository.

Publication reran 126 portable regressions successfully, including the new live
artifact and thread numbering cases. Package validation and secret, model-weight,
large-file and generated-image publication scans passed.
