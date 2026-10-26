# Changelog

## Production 20261026T123451Z — 2026-10-26

- Structural scaffold: the workspace LLM authors a strict-subset SVG line drawing for requests with exact counts, symmetry or layout; the agent validates, re-emits and rasterizes it and the engine restyles it through the reference path (exact structure, e.g. a nine-candle hanukkiah). See `image-system/docs/STRUCTURAL-SCAFFOLD.md`.
- New `colorize` operation: black-and-white photo restoration with DDColor on the local ComfyUI/XPU engine, preserved dimensions, no engine prompt, fast/quality tiers, Composer entry. See `image-system/docs/COLORIZE.md`.
- Compatibility proxy: unified 32 MiB request limit on `/v1/*` (closes the historical HTTP 413 on vision payloads), configurable upstream timeout, size-based audit-log rotation, markup-aware argument sanity for scaffolds.
- Engine endpoints centralized in `src/endpoints.js` (validated, ENV-overridable private URLs).
- Composer UI: single-line header, info glyph instead of repeated guidance badges, shorter helper texts, visible engine state chip, primary Download action, correct RTL ellipsis on thread titles; removed the duplicated inline Atlas prompt-disclosure script.
- Includes the 2026-10-20 production releases that were not yet published (chat-body-fix, tool-keepalive, shared-plan-authoring, token-usage).

## Production 20261006T134320Z — 2026-10-06

- Dock and undock Image Composer between Popup and a left or right resizable side panel; preserve draft and Composer state.
- Persist panel width and side, reserve a safe chat gutter, and keep historical images and action rows responsive.
- Remove the Display Mode dropdown and correct the controls disclosure direction.
- Keep the dynamic active-model header visible while conversation history scrolls.
- Preserve sidebar ordinals, internal identities, historical filenames and live artifact presentation; include portable layout regressions.

## Production 20261006T050300Z — 2026-10-06

- Label workspaces and threads `N - NAME` using their current visible order; preserve stable IDs/slugs and historical filenames.
- Resize the sidebar from 260–520px, with a 292px default and a locally persisted preference.
- Keep ordinal prefixes visible, ellipsize titles only, and expose complete titles on hover/focus with Hebrew/English direction support.
- Preserve chat reflow and navigation alignment; add portable width, numbering and safe browser-checkpoint regressions.

## Production 20261005T211028Z — 2026-10-06

- Present completed image artifacts live without refreshing, including structured responses with empty text.
- Preserve one artifact through stream updates, retransmission, recovery and reload; serialize publication per job.
- Resolve visible Thread numbers from the current ordered list while retaining stable backend IDs and historical filenames.
- Add pinned-upstream reducer, SQLite publication and ordinal-numbering regressions.

## Unreleased — current Visual Atlas V2

- Publish sanitized bindings for the 493 existing V2 images; pixels remain external.
- Include exact Show/Hide/Copy Prompt, accessible icon actions, viewport-bounded scrolling and persistent XLarge display size.
- Preserve the existing Atlas integration and normal generation behavior.

## 1.0.0 - 2026-09-04

- Adds the owner-approved MIT license for AAG-owned code.
- Completes deterministic backend/frontend overlay provenance for 11 pinned stock files and 21 overlay targets.
- Makes the 493-style Visual Atlas fully usable in metadata-only mode while keeping all 986 rights-unresolved pixel assets outside the public distribution.
- Excludes the historical nonportable Ubuntu Agent capture from all install profiles.
- Revalidates clean reconstruction, full lifecycle, Composer, Atlas, Chess, PDF/artifacts, progress/cancel, and model-neutral optional local-LLM behavior.

## 1.0.0-rc1 - 2026-09-04

- Converts the package into a profile-based, transactional end-user installer for Ubuntu x86_64.
- Adds exact AnythingLLM commit and stock-file hash gates, portable configuration, hardware/model detection, doctor, update, uninstall and rollback flows.
- Adds a verified external Atlas asset-pack workflow; pixels remain excluded pending an explicit redistribution grant.
- Records isolated full-profile install/update/uninstall/reinstall acceptance and preserves the live deployment.

## 0.1.0-private-package - 2026-09-04

- Initial sanitized packaging of the AAG AnythingLLM customization ecosystem.
- Captures AAG Image 0.9.0-preview.13 and Visual Atlas 1.0.0 metadata.
- Adds reproducible inventories, transactional tooling, CI and audit evidence.
