# AAG Image Agent — 0.9.0-preview.13

Preview release exposing exactly three bounded AnythingLLM tools:
`aag-image-task`, `aag-image-batch`, and `aag-image-job`.

The provider boundary accepts only semantic task fields. The provider-neutral core owns source authorization and normalization, fixed workflow selection, FIFO job/child lifecycle, a shared operating-system-level XPU lease, execution, publisher policy, artifact decoding and provenance. It does not expose engine paths, models, workflows, URLs, commands or recovery controls, and it does not load inactive legacy image skills.

The model-facing `aag-image-task` contract is closed at registration and again
at execution. Undeclared properties fail before handler/job creation, creative
style is prompt text rather than a structured field, and task results are
direct-output so success or failure cannot trigger an autonomous same-turn
generation retry. Managed local models traverse a behavioral, model-neutral
compatibility boundary that separately validates basic model/text sanity and
ordinary multi-role chat before selecting `NATIVE`, `GENERIC_ADAPTER`, or
`INCOMPATIBLE` tool behavior. A lower-runtime text failure never reaches the
adapter. The boundary does not strip raw special tokens, route by model
identity, or execute tools. The canonical
pre-execution validator remains the authoritative security and correctness
boundary for every provider. See
`docs/PROVIDER-NEUTRAL-BOUNDARY.md` for the LM Studio, Ollama, cloud, and future
provider compatibility boundary.

The Image Composer offers AUTO free text and ADVANCED structured controls plus
free text. Its Visual Atlas surface is backed by the canonical completed
493-entry manifest: users can choose family/subfamily with an immediate real
thumbnail, visually browse/search/filter lazy thumbnails, open one larger
preview, select, change, or clear a style. Exact manual selection is
authoritative over AUTO matching. The workspace model continues to author the
complete professional creative prompt, and Composer requests enter the existing
Image workspace path; they do not bypass Workspace Capability Profiles V1.1 or
call the runtime directly.

`aag-image-batch` is the generate-only general multi-image boundary. The
workspace model supplies one collection brief and exactly 2–10 ordered,
self-contained creative prompts. One durable parent owns stable child records,
one shared XPU lease serializes heavy work, exact-count completion is enforced,
verified siblings survive partial failure/cancellation, and explicit new-turn
resume runs only unfinished children. It does not expand transform, upscale, or
identity scope.

AnythingLLM presents each batch as one ordered collection. Every persisted
image has Download and lazy one-page PDF actions; a complete collection has a
lazy Save all as PDF action. The provider-neutral AAG Artifact Export service
authorizes persisted chat outputs, verifies source provenance, assembles a
deterministic local one-image-per-page PDF, and offers native Save As with a
browser-download fallback. See `docs/AAG-ARTIFACT-EXPORT.md`.

Ordinary generation defaults to the sealed FLUX.2 Klein 4B balanced-aspect
recipe: Euler + Flux2Scheduler, four steps, CFG 1.0, a provider-neutral canonical
prompt-quality gate with trusted-request semantic fidelity and structural QA.
The selected workspace LLM remains the prompt author. The selective-knowledge
layer leaves unstyled requests byte-preserved and, only for a reliable AUTO
match or signed manual Atlas selection, appends one bounded canonical style
descriptor plus subject/anatomy protection. It never loads the full Atlas or
adds another inference call. Ordinary generation retains zeroed negative
conditioning and deterministic 640x640 square, 512x768
portrait, or 768x512 landscape framing. Explicit fast and quality profiles
remain available. The ComfyUI process is launched with local/offline and
telemetry-disable guards.

Human Identity Production v1 remains active only for local-personal, offline
portrait generation with one of the two frozen authorized adult-man or baby
identity fixtures. It uses exact Contract B framing, PuLID v1.1, Juggernaut XL
V9, the locked identity floor, and the unchanged detector/composition/blur
gates. Unsupported identities and requests fail closed without ordinary or
alternate-model fallback.

`npm run build` writes only to `releases/staged/0.9.0-preview.13`. The separate
`node tools/build-anythingllm-frontend.js` command builds the exact deployed
AnythingLLM revision with the reviewed export UI overlay. Neither command
activate Production.
