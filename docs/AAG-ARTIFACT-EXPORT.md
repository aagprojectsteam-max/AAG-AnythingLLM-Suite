# AAG Artifact Export contract

`aag.trusted-artifact-export.v1` is a creative-domain-independent boundary for
on-demand export from AnythingLLM. The current Image producer uses it; AAG
Chess and future trusted artifact producers can adopt the same descriptor and
UI without another PDF implementation.

## Producer adapter

A trusted producer persists an AnythingLLM output with an opaque
`storageFilename` and, for collections, this `artifactExport` descriptor:

```json
{
  "schema": "aag.trusted-artifact-export.v1",
  "producer": "producer-id",
  "trustClass": "anythingllm-generated-image-v1",
  "collectionId": "stable-owner-scoped-id",
  "logicalIndex": 1,
  "requestedCount": 3,
  "collectionComplete": true,
  "sourceSha256": "64-lowercase-hex"
}
```

The producer must persist every item in one authorized chat response. Logical
indices are one-based, unique, and contiguous. `collectionComplete=true` is
valid only when every intended trusted artifact exists. A producer does not
provide PDF bytes, host paths, or completion-time ordering.

## Consumer/service boundary

The browser sends only `format=pdf`, `mode=single|collection`, and 1–10 opaque
storage IDs after an explicit click. The server resolves an authorized
persisted chat, ignores client ordering for a collection, derives logical order
from persisted descriptors, verifies the exact set/hash/dimensions, and then
invokes `aag.artifact-export.pdf.v1`.

The assembler is local and deterministic. It creates exactly one
orientation-preserving page per source, performs only lossless PNG decoding and
Flate recompression for PDF embedding, adds no cover/index/blank/watermark, and
verifies the resulting page count. Cache identity is the ordered source hashes
plus the fixed export contract/layout. Source artifacts are never modified or
deleted.

## Browser UX

The client invokes native `showSaveFilePicker()` from the initiating gesture
before requesting server work, suggesting the UTC, zero-padded, editable name
`ANYTHING-YYYYMMDD-HHMMSS.pdf`. Unsupported picker environments use a normal
browser download. Cancelling the picker creates no PDF request. Full collection
export remains disabled for partial collections; completed items retain their
individual Download and PDF actions.

## Chess integration boundary

A future Chess puzzle-image producer should persist the same descriptor beside
each puzzle image and reuse `AagImageCollection`, the per-image actions, and the
single export endpoint. It must not add a Chess-specific PDF assembler. Chess
domain data remains in its producer payload; the export service sees only the
trusted ordered artifacts and safe layout metadata.
