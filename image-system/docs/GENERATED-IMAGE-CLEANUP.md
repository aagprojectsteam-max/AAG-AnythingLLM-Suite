# Generated-image ownership and cleanup

The existing AnythingLLM BackgroundService starts `cleanup-generated-images`
six minutes after startup and then every nine hours. The native policy retains
files younger than one hour. Both intervals and the retention grace are unchanged.

## Persisted ownership

Native history rendering, generated-image attachments, artifact authorization,
Show Details, Download and PDF resolve a typed `imageGenerationCard` in an active
(`include=true`) `workspace_chats.response.outputs` array. The card's
`payload.storageFilename` binds it to the configured `generated-images` directory.
Completed `scheduled_job_runs.result.outputs` also retain images under the native
cleanup policy. All such rows are scanned without filtering their JSON by a
filename prefix. Text-only responses have no image ownership.

`aag_artifact_filenames` maps artifact ID and job ID to the assigned native copy's
filename. A valid typed card carrying those IDs can resolve that exact binding
even if its filename field needs reconstruction. A conflicting identity or an
unresolvable card makes ownership unknown, never orphaned.

The naming ledger alone is not ownership: reservations remain after conversations
are deleted, to prevent name reuse. An unreferenced reserved file can be cleaned.
If that reservation's existing conversation has an open native agent invocation,
presentation may still be pending after a crash. That filename is UNKNOWN and
retained until the invocation closes or a durable image card owns it. This is a
conservative use of existing persisted state, not a new ownership registry.

Persisted AAG job artifacts identify the original verified engine/publisher
artifact, in separate storage. The native `generated-images` copy is assigned
and bound by `aagArtifactNaming.persist` and `buildPresentationOutputs`. The
cleanup does not enumerate AAG job JSONs or infer names from workspace/thread
serials. Application paths and physical paths shown in Details are derived from
the saved card; arbitrary path strings or display labels are not ownership.

## Failure that prompted this change

The previous worker enumerated files using the updated filename pattern but
queried chats and scheduled runs with `contains: "img-"`. Its reference set
therefore omitted valid `AAG-Image-...` cards. It also used model helpers that
catch database failures and return `[]`, plus a JSON parser that turns malformed
state into an empty output set. Both can misclassify referenced files as orphans.

## Safe decision and deletion

`aagGeneratedImageCleanup.discoverReferences` uses direct Prisma SQL calls whose
errors propagate. Keyset pagination and count checks run in one SQLite
transaction. For destructive execution, a no-row UPDATE acquires SQLite's write
reservation before discovery; reference writers cannot change the ownership
snapshot between discovery and deletion. The no-row statement changes no data.

Discovery must finish successfully before any unlink. Malformed JSON, malformed
outputs, unresolved/conflicting identities, missing required sources or an
incomplete scan skip deletion and log a diagnostic. Decisions are REFERENCED,
ORPHAN or UNKNOWN. Only an eligible ORPHAN older than the existing retention
grace can be deleted. A cleanup failure never disables future scheduled runs.

The root must be canonical and physical. Deletion is anchored to an open root
directory descriptor using `/proc/self/fd`; only names accepted by the existing
generated-image filename pattern are candidates. Symlinks, hardlinks and
nonregular entries are skipped. Root identity and candidate inode, size, times
and link count are checked again before unlink. No caller-supplied path is used
as an unlink target. ArtifactFiles and its independent authorization/security
checks are unchanged.

The worker supports `--dry-run` for decision-only inspection. Its configuration
comes from the existing native files module; no arbitrary root/path CLI is
accepted. The reusable core is independently testable against isolated SQLite
and filesystem fixtures. No AAG progress/recovery timers or indexes are changed.
