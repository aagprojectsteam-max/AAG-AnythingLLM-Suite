# Production release 20261006T134320Z

Image Composer can now dock into a side panel on either side of the chat.
The Dock/Undock icon switches between Popup and Side Panel; there is no Display
Mode dropdown. While docked, Panel side selects Left or Right. Closing and
reopening the panel, changing sides and docking preserve Composer state and drafts.

Drag the panel's inner edge to resize it. The preferred width is 320–560px with
a 384px default, persisted locally with side and mode. The actual width adapts
to available space while reserving at least 480px for chat and 12px separation.
Narrow viewports show Popup without discarding the saved panel preference.
Keyboard arrows resize the physical edge; Home/End select bounds and Enter
restores the default. Resize work is scheduled once per animation frame and the
preference is saved when dragging ends. Normal-resource production smoke
measured approximately 60 frames per second on both sides.

The controls disclosure points up when expanded (Hide controls), and down when
collapsed (Show controls). Central content keeps a safe gutter; historical images
scale responsively and artifact actions wrap within the chat area. There is no
horizontal overflow. The active-model header stays visible above the scrolling
conversation and follows the actual workspace model.

Workspace and thread sidebar rows remain `N - NAME`, with independent dynamic
thread ordinals. The existing 260–520px navigation sidebar and 292px default are
preserved. Stable internal IDs/slugs, API/socket ownership, persisted artifacts,
historical filenames and live image presentation without refresh are unchanged.
Hebrew/English direction handling and keyboard accessibility remain covered.

The approved manifest SHA-256 is
`f80dad87da21a3ecaa87b4932b273a2f7b69d7ec85064b36b670d4bddc937db3`.
The [public source manifest](../manifests/GOLDEN-20261006T134320Z.json),
[installation map](../manifests/GOLDEN-INSTALL-MAP.json) and original frontend
build provenance bind this publication to the exact deployed candidate.
The verified rollback version is `20261006T050300Z`.

Validation retained 759 passing release tests and 343 passing UI checks,
including 33 focused checks under normal resources. The user approved the
installed-app manual review under normal resources. All 29 final production
smoke checks passed without generating an image. Deployed files and served assets
matched the seal; production database contents and all 18 historical artifact
files were preserved. A blanket POST guard in the deployment harness initially
blocked read-only artifact metadata requests and triggered a verified rollback.
Only the harness was corrected; the same sealed candidate was then deployed
successfully, without product changes.

Publication includes exact source, permanent portable regressions, and
deterministic source-only installation overlays. Compiled assets, private
browser harnesses, profiles, databases, screenshots, logs, diagnostic attempts,
generated images, model weights and active host configuration are excluded.
No build, deployment, production restart, real generation or system performance
change is performed during publication.

Portable publication gates are defined in `.github/workflows/ci.yml`, including
the Composer layout preference, bounds, safe-width and physical keyboard tests.
Run `./doctor.sh --package` and `./tools/sanitize.sh` for package/security gates.
