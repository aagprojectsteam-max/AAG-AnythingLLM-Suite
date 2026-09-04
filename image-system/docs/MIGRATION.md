# Migration — 0.9.0-preview.13

Preview 13 is a production preview-delivery correction and permanent Atlas
packaging gate on top of Preview 12. Before building or migrating, run
`node tools/visual-atlas-product.js`; 493 references, 493 thumbnails and all
metadata must pass. A normal migration copies the complete AAG Image System
root, including the top-level `visual-atlas` directory. A deliberately split
large-asset migration must use the verified `--export` and `--install-from`
workflow in `VISUAL-ATLAS-PACKAGING.md`; a code-only migration is incomplete.

Build all three canonical provider bundles and the exact-revision AnythingLLM
frontend. Deploy the providers, Composer proxy and source-built public tree
together. Preserve the existing read-only Atlas mount and recreate AnythingLLM
once. Do not alter the static V5.2 workspace prompt, tool schemas, Human
Identity contracts, engine graphs or thermal thresholds.

Existing schema-v2/v3 jobs remain readable; the `atlas` plan is an optional
additive record member. Existing images, history and selections are not migrated.
The completed 493 reference PNGs are immutable. Browser thumbnails are derived
assets. Rollback restores the Preview 11 integration and UI while preserving
jobs, images, chats, models, all reference PNGs and the repaired derived
thumbnail. Preview 13 does not regenerate or migrate any Atlas image. See
`VISUAL-ATLAS-COMPOSER.md` for the full contract and
`VISUAL-ATLAS-PACKAGING.md` for installation/restore.

## Earlier Preview 4 migration

## From 0.9.0-preview.3

This is a coupled ordinary-generation activation. Deploy the canonical source,
both provider directories, staged/promoted release, workflow registry, ordinary
recipe and `integrations/launchers/aag-ai-start` together. Restart only the
AnythingLLM container to reload providers; the governed image launcher applies
the offline environment when ComfyUI starts. Do not alter the Human Identity
active-generation marker, runtime, model paths or Contract B.

The default `auto`/`balanced` profile becomes aspect-aware. Explicit `fast`
retains the prior dimension policy and explicit `quality` retains the 9B route.

## Earlier hardening migration record

1. Verify the scoped pre-change backup at `backups/image-agent-pre-hardening2-20260826T160805Z` with its `SHA256SUMS`; reuse it rather than creating another large backup.
2. Run the Node and Python suites, build the candidate, and run `doctor` before changing canonical or deployed files.
3. Promote the reviewed candidate source to canonical `image-agent/`, preserving immutable `image-agent/releases/0.9.0-rc.1` evidence and the Hardening Pass 2 evidence tree.
4. Deploy both provider bundles and the three integration sources listed in `STAGED-MANIFEST.json`. Keep the displaced live integration files as same-directory rollback anchors so their original ownership and modes can be restored without privilege escalation.
5. Restart only the affected on-demand image services and AnythingLLM after proving there is no active queue, lease, or upscale process. Do not enable any image unit at boot.
6. Verify source, staged, deployed, integration, provider schema, registry, Contract B, and core-patch hashes. Confirm exactly two active image tools and the bounded `active-validated-local-personal-scope` Human Identity capability.
7. Run bounded provider and restart-durability dogfood, then execute a full rollback/reapply drill. Never remove or modify an existing file under `/mnt/data/AI/Outputs`.

Human Identity is not a migration gate for execution because it is deliberately unavailable; it remains a release-maturity gate and must not be re-enabled without new visual acceptance evidence.
