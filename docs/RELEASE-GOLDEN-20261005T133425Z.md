# Golden 20261005T133425Z

The production-validated release **20261005T133425Z** is the current Golden.
Release closure publishes the approved implementation without changing or
restarting production. The prior verified rollback, **20261005T111343Z**, and
its complete deployment backup remain local and unchanged.

The immutable approval manifest SHA-256 is
`859dce49ed588947a70c682f5c9ee6eb44f8d93d1acb59c5f1fd3f46d51d83e0`.
[The public Golden manifest](../manifests/GOLDEN-20261005T133425Z.json) binds
approved source, tests and source-only provider bundles to their exact hashes.
[The installation map](../manifests/GOLDEN-INSTALL-MAP.json) enumerates server,
storage integration **code**, and frontend source overlays. Production databases,
workspace bindings, active systemd units, job state, images and rollback payloads
are excluded. Static deployment paths in existing approved source are retained;
this closure does not claim a new portable-host architecture.

## Preserved implementation

| Behavior | Authoritative source in this repository |
| --- | --- |
| AUTO/ADVANCED, Expert defaults and manual override, disclosure controls, Atlas | `image-system/integrations/anythingllm/frontend/` |
| Selective taxonomy, Expert validation and semantic review | `image-system/expert-prompt/`, `integrations/anythingllm/model-neutral-compatibility/`, `image-system/src/expert-review.js` |
| Artifact rendering, readable errors and hidden machine protocol | `image-system/integrations/anythingllm/aagArtifactPresentation.js`, `aagArtifactText.js` and frontend overlays |
| Prompt, details, download, PDF, physical/application paths and SHA-256 | `image-system/src/prompt-provenance.js`, `generation-details.js`, artifact server/client modules |
| Forward-only user-facing workspace/thread serials and filename success label | `image-system/integrations/anythingllm/aagArtifactNaming.js`, `aagArtifactText.js`, frontend serial initializer |
| Referenced legacy/new-image preservation and true orphan cleanup | `image-system/integrations/anythingllm/aagGeneratedImageCleanup.js` |
| Idle work index, bounded recovery and restart handling | `image-system/src/job-index.js`, `recovery.js`, `lifecycle/` |
| Browser-neutral visible exact-file reveal, cold/warm Nautilus, Brave and App Mode | `image-system/lifecycle/artifact_files.py` |

New filenames use persistent AAG serials and host-local generation time; canonical
artifact timestamps remain UTC. Historical names and image bytes are unchanged.
The previous deployment's real-user checks passed in normal Brave and the
installed App Mode, including refresh and reopening the app. Closure creates no
new real image and performs no new deployment.

## Validation and external inputs

The exact published implementation passed 454 Node tests, 149 compatibility tests,
61 Expert tests, 60 lifecycle tests, 7 browser-lifecycle tests, and 4 public Atlas
metadata tests. Full suites use the separately retained authorized Atlas product
pack and the existing offline tokenizer dependency in an isolated validation
tree. Atlas V1 sealed runtime inputs and V2 public metadata are checked separately.
The first bare-source run exposed missing external assets and package path
aliases; final runs passed after supplying those inputs and repository aliases.

The approved provider builder and pinned AnythingLLM frontend builder both passed.
The upstream revision is `07bd65f80b3d9ba3031ed7afb8786627326bd134`.
`manifests/AAG-BUILD-PROVENANCE.json` retains the original Golden's compiled-tree
identity. `manifests/CLOSURE-BUILD-PROVENANCE.json` records the fresh rebuild:
its source/overlay/tool hashes match Golden, but its compiled-tree hash differs.
This is source build validation; byte-for-byte frontend rebuild reproducibility
is not claimed. Neither compiled tree is published in this repository, and the
fresh rebuild does not replace the immutable production payload.

Run `./doctor.sh --package` and `./tools/sanitize.sh` on a source checkout.
Package mode checks the distribution; an absent local model/engine checkout is
an external prerequisite, not an installed-system health result. Full image
runtime validation requires the authorized asset pack, tokenizer and host
services. The source hash map deliberately excludes active host unit files.
The approved host lifecycle and fail-closed workspace policy require locally
managed configuration; no production workspace binding is shipped.

## Publication boundary

The audit covers every changed/new publication file. Only approved source, tests,
documentation and intentional source bundles/metadata are included. Three tiny
PNG/JPEG/WebP decoding fixtures are the only binary image files. Secret-like
negative-test strings have exact path/line-hash exceptions with documented reasons;
scanner findings never print credential values. No weights, user artifacts,
production storage, databases, browser profiles, logs, private service settings
or evaluation captures are published.

Four pre-existing local file variants are preserved outside this commit; their
approved Golden counterparts are published. A fifth pre-existing file already
matched Golden exactly. The final commit and remote hash are recorded in the
local closure report rather than a self-referential committed document.
