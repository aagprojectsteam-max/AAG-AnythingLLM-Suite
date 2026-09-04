# Security model — 0.9.0-preview.13

The multi-image public schema is provider-neutral and closed at both the
top-level and per-item objects. The canonical runtime independently enforces
operation, required fields, 2–10 count bounds, exact items/count equality,
quality/aspect enums, prompt bounds, owner/turn scope, and generate-only scope.
Provider grammar remains optional prevention, never the safety boundary.

Batch parents persist the immutable plan hash and stable children before heavy
execution. A single existing FIFO/XPU lease serializes children. Completion
requires exactly one verified artifact for every intended child; duplicate
invocations and duplicate artifacts fail closed. Partial failure or
cancellation preserves completed children, and resume is explicit new-turn
work that reuses stable child IDs and skips every verified child.

Artifact export accepts only bounded opaque generated-image storage IDs. It
re-resolves an authorized persisted chat output, rejects mixed/incomplete
collections, opens source images without following symlinks, and verifies
hashes/dimensions before deterministic local PDF assembly. The browser cannot
provide an output path. The cache key binds the ordered source hashes and fixed
layout; cached output is derived, private, bounded, and never created before an
explicit export request.

The provider sees only bounded semantic scalar fields. Unknown infrastructure, workflow, model, executable, URL, command, and path fields are rejected. Human Identity accepts historical fixture hashes as regression classifications and also accepts a new reference only when the canonical runtime resolved it from the trusted current-attachment invocation context. It permits no caller-supplied path, generic-subject fallback, or alternate-model fallback and runs the exact Contract B worker in a network-isolated cold process.

Normalized current-attachment bytes are staged privately under a request-UUID-derived name with a separate provenance record binding workspace, thread, user, invocation, source index, hashes, dimensions, and format. The bridge request schema contains no reference path. The worker independently derives the staging path, revalidates the caller and file properties, and removes the staging pair after processing. The two historical fixture SHA-256 values remain tests and negative-control evidence; they are not a production allow-list.

Current attachments are limited to JPG/PNG/WEBP, 50 MiB compressed bytes, one frame, 40 million decoded pixels, and 16,384 pixels per side. A real decoder performs EXIF transpose, alpha flattening, sRGB normalization, and metadata-stripped PNG output. Current attachment selection is enforced by forwarding only the normalized selected image. Arbitrary URLs, paths, traversal, deceptive extensions, malformed base64, cross-thread artifacts, and symlinked state files are rejected.

Jobs, context, idempotency, waiters, and leases live under `0700` directories with `0600` atomic files and no-follow reads. The queue is FIFO, depth-bounded to eight by default, owner-scoped, timeout-bounded, and stale waiters are pruned. A lease is reclaimed only after a stale heartbeat and confirmation that ComfyUI and Upscale are idle. The ComfyUI bridge and Upscale service participate in the same filesystem lease for both agent-delegated and external work.

ComfyUI graphs, nodes, models, dimensions, seeds, and output correlation are fixed by trusted code. The Upscale subprocess uses a fixed executable and argument array with allowlisted models and scales. Running cancellation is deliberately reported as `CANCEL_NOT_SUPPORTED`; the runtime never claims an engine was stopped when targeted cancellation is unavailable.

The Image Hub accepts only decoded ComfyUI output names, requires a fresh agent lease token for import, writes through private exclusive staging and no-follow directory descriptors, never overwrites a different existing output, and serves only safe decoded regular images. Final artifacts are fetched back, decoded, dimension-checked, hashed, and publisher-verified before completion.

Residual limitations: full-machine reboot is structurally covered but not physically exercised by this pass; existing API contexts may provide `user_id=unknown`, leaving workspace+thread as the effective owner scope exactly as before; arbitrary-reference quality is evaluated per artifact and is not universally guaranteed; running targeted engine cancellation remains unsupported; provider neutrality is proven for the core contract, while each additional provider still requires its own schema/tool-selection E2E.

## Visual Atlas boundary

The Atlas mount is read-only. Catalog responses expose only bounded display and
selection metadata plus same-origin asset URLs; raw paths, generation prompts,
engine job IDs and benchmark subjects are not exposed. Asset endpoints require
the existing authenticated Image Generator workspace boundary, accept only safe
canonical slugs, resolve beneath the mounted root and serve a manifest-mapped
file. The browser uses a normal authenticated same-origin fetch with explicit
Image Generator workspace context, validates status and exact image media type,
and exposes only an in-memory blob URL to the image element. A missing Referer
does not bypass the workspace check. Requests without valid page provenance or
explicit workspace context are rejected. Browser thumbnails are viewport-lazy
and bounded; full PNG previews are fetched only on explicit larger-preview
actions. Immutable asset responses are private, same-origin and `nosniff`.

Manual selection travels only in a compatibility-server-authored signed tool
request marker and is revalidated against the canonical 493-entry manifest.
AUTO retrieval fails open with no context; invalid explicit selection fails
closed. Atlas images are not identity or source attachments and are not passed
to the engine in Preview 13. The Atlas module cannot weaken anatomy, identity,
ownership, queue, cancellation or artifact-verification contracts.
