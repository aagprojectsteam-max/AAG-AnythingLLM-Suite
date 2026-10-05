# Approved release integrity and image lifecycle

## Two separate authorities

Development source is validated when a candidate is created. Normal startup reads `image-agent/releases/approved/CURRENT.json`, verifies its manifest SHA256, and checks the exact approved runtime, deployed files, frontend tree, read-only Docker bridge mounts/inodes, frozen contracts and Atlas assets. It does not compare active development source with historical release hashes.

The manifest is a local administrator trust record, not a digital signature or protection against a privileged attacker who can replace both the verifier and approval pointer. The approval pointer and deployment are owner-managed. Every release payload file and deployed target is enumerated; unexpected frontend files, missing assets, malformed schemas and an invalid 28-family/493-style bijection fail closed.

The compatibility/Expert Python process runs from the approved runtime snapshot. The image CLI uses the separately deployed lifecycle/verifier. Native AnythingLLM JS and frontend use deployed copies. Development edits therefore cannot silently alter the approved runtime. Frozen reference contracts, the engine budget and executable lifecycle helpers remain protected runtime inputs.

## Creating and deploying a release

From `image-agent`:

1. `node tools/build-anythingllm-frontend.js`
2. `python3 lifecycle/release_candidate.py create`
3. Inspect the resulting `APPROVED-MANIFEST.json` and validation logs.
4. `python3 lifecycle/release_deploy.py deploy /absolute/path/to/approved/<release-id>`

Candidate creation runs Node, compatibility, Expert and lifecycle/integrity tests, Python syntax validation, the stage build and existing contract/security/Atlas doctor. It checks that source did not change during validation and that frontend source/build-tool provenance matches the actual build. The candidate includes only required runtime code/data, static frontend and test evidence. No model weights or private runtime state are copied.

The existing historical narrow `governed-update.js` refuses updates after approved-runtime activation. It cannot overwrite an approved release with a partial overlay.

A frozen-policy change is rejected by normal resealing. Such a change requires an explicitly reviewed contract migration. Atlas changes still require the existing governed product-asset reseal before candidate creation; normal candidate creation does not reseal images or prompts.

## Deployment and rollback

Deployment serializes with other deployments and lifecycle operations, refuses active/queued image work, verifies the seal, and makes a timestamped rollback backup before changing production. The rollback index includes previous file modes/hashes and the prior approval pointer. AnythingLLM is stopped during file replacement and restarted to remount file binds. The public directory inode is preserved. HTTP HTML/JS/CSS MIME checks and Docker health supplement tree parity.

A deployment failure automatically restores the backup. Manual rollback:

`python3 lifecycle/release_deploy.py rollback /absolute/path/to/deployment-<timestamp>`

Both Atlas versions, images, prompt provenance and review evidence are untouched.

## Ownership and state

Canonical commands: `bin/aag-image-start`, `bin/aag-image-stop`, `bin/aag-image-status`. Arguments are forbidden. START/STOP share an exclusive process lock and fixed argv; no shell execution or process-name killing is used.

Owned components: ComfyUI transient unit, Comfy bridge and image proxy, Image Hub/bridge, Upscale/bridge. Reference nodes are health-checked as part of Comfy readiness. Active reference workers, pending inbox files, scheduler lease/waiters, Comfy queue and Upscale busy state prevent shutdown. Missing idle telemetry fails closed. New governed tasks check READY before queuing and after obtaining the scheduler lease, closing the STOP/submission race.

Retained infrastructure: AnythingLLM, Docker, compatibility service, Composer relay, chat models, reference inbox watchers, Qdrant/vector workers and desktop/power services. The retained compatibility service and Composer socket are required for READY. OFF describes stopped owned engines; retained services stay available so the UI can START again.

Status derives from current health probes and unit states. Saved operation metadata records only bounded in-progress operations and explicit failures, not a saved READY/OFF truth. Interrupted operations are detected with PID/start-time/deadline checks and report ERROR. Retry clears an error only after observed success. Duplicate or crossover commands return the current operation without creating another engine instance. Active-work STOP returns a safe retryable error while preserving the work.

## API and UI

`GET /api/aag-composer/image-generator/lifecycle/status`

`POST /api/aag-composer/image-generator/lifecycle/start`

`POST /api/aag-composer/image-generator/lifecycle/stop`

Routes retain native session authorization, image-workspace membership, role and same-origin guards. POST also validates the existing Composer session/CSRF token through the private relay. Only an empty JSON object is accepted. A separate owner-only Unix socket invokes fixed canonical CLI paths. The compatibility server performs validation only; it cannot execute lifecycle commands.

The Composer control is independent of Expert Mode. It shows accessible Hebrew/English status text, keyboard buttons and safe retry errors. It polls actual status on mount, focus and refresh, with faster polling during transitions and cleanup on unmount. No lifecycle state is saved in browser storage. It may optimistically show STARTING/STOPPING, but only backend observations can show READY/OFF.

No image or model-provider calls are made by lifecycle status/start/stop. Expert OFF/ON, current-active-model routing, selective 28/493 guidance and the existing 512-token budget are unchanged.
