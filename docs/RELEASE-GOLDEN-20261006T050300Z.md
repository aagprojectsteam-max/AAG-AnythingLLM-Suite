# Production release 20261006T050300Z

Workspace and thread sidebar rows now display `N - NAME`. Each number follows
the current visible order; thread ordinals start independently within each
workspace. Confirmed workspace deletion removes its presentation row promptly.
The generated `Thread N ·` prefix is gone. Stored titles remain verbatim,
including titles that happen to contain the word “Thread”. Stable IDs/slugs,
database relationships, API/socket routing and historical filenames are unchanged.

Drag the sidebar's right edge to select a width from 260–520px. New profiles
default to 292px; the preference survives reload and browser restart. A small
viewport temporarily clamps the layout without discarding the wider preference.
The separator is keyboard accessible: arrow keys resize, Shift increases the
step, Home/End select bounds, and Enter or double-click restores the default.

Ordinal prefixes do not truncate. Titles use the remaining width and ellipsize
when needed. Hover or keyboard focus reveals the complete stored title; Escape
dismisses the tooltip. Isolated text direction supports Hebrew, English and mixed
titles. Search, workspace rows, thread trees, New Thread and footer controls stay
aligned while the main chat area reflows.

The exact approved manifest SHA-256 is
`29300261f385559d3e8fbe2e31383d35451cf70c837f080d44ba4aba555a9c74`.
The [public source manifest](../manifests/GOLDEN-20261006T050300Z.json),
[installation map](../manifests/GOLDEN-INSTALL-MAP.json) and original frontend
build provenance bind this publication to the validated candidate. The verified
production rollback version is `20261005T211028Z`.

Candidate validation passed 754 release regressions and 84 native UI browser
checks, including deletion/reorder, independent ordinals, stable identities,
historical filenames, width persistence, tooltips, bidi and reload/restart.
Workspace reordering used native drag/drop. Upstream has no thread drag/drop UI;
thread reorder rendering used a scoped ordered-response fixture, with unchanged
database IDs and request routes. Native deletion tests used isolated test data.

After deployment, 57 non-generative production checks passed. These covered
workspace reorder in a disposable browser profile, actual thread/workspace
switching, 260/292/520px layouts, accessible tooltips, reload/restart persistence
and existing image cards. All production database records and historical image
bytes remained unchanged. Production smoke did not delete user workspaces or
threads. Live file and HTTP asset hashes matched the sealed candidate exactly.

Publication includes exact source, permanent regressions and deterministic
source-only installation overlays. Compiled assets, screenshots, private browser
harnesses, profiles, databases, logs, generated images, model weights and host
configuration remain outside Git. Publication performs no build, deployment,
production restart or real image generation.

Portable checks are defined in `.github/workflows/ci.yml`; package and publication
checks run with `./doctor.sh --package` and `./tools/sanitize.sh`.

Publication reran all 132 portable Node regressions and the portable checkpoint
ownership test successfully. Package doctor and publication scans passed. An
initial sandbox run blocked the cross-process job-index test with `EPERM`; the
unchanged suite passed with subprocess access in the isolated publication tree.
