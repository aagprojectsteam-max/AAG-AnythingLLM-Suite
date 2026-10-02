# Current Visual Atlas V2

The existing Visual Atlas now uses the completed V2 image set: 493 canonical styles across 28 families. The canonical `visual-atlas` directory, taxonomy, ordering, selection logic and integration remain in place. `atlas_version: 1.0.0` identifies the existing catalog contract; `image_set: visual-atlas-v2` identifies the current image set. This is not a new suite release or a new Atlas architecture.

## What is published

- Current canonical image SHA-256 values, exact image-bound prompts and prompt SHA-256 values.
- Public generation provenance: canonical ID, seed, workflow and model identities and hashes. Local job IDs, attempt state, timestamps, machine paths, backup locations and review evidence are excluded.
- The existing protected catalog exposes each entry's `atlas.prompt` and `atlas.prompt_sha256`. Disclosure checks the current preview/thumbnail identity and prompt hash before rendering text or copying it.
- Compact Select and Prompt icons share one row, with labels, hover/focus tooltips and keyboard activation. Selected-card behavior uses the original Select handler.
- The prompt dialog fits the viewport; the prompt text scrolls internally while the image, labels and controls remain accessible.
- Small, Medium, Large and XLarge use the existing responsive grid and `aag.image-composer.v1.2.atlas-thumbnail-size` preference.

Historical prompts are for disclosure only. Normal image generation still uses the existing bounded style descriptors; the Atlas object-only prompt text does not become a global generation policy.

## Pixels stay external

The existing publication policy remains unchanged: no PNG/WebP payload, generated pack, model weights, private state or compiled frontend is committed or attached to a release. The Git checkout works in metadata-only mode. To reproduce previews, supply the exact authorized 493 PNG files and 493 WebP derivatives described by `atlas-assets-manifest.json`.

```bash
python3 tools/atlas-assets.py verify --source /path/to/current/visual-atlas
python3 tools/atlas-assets.py install --source /path/to/current/visual-atlas \
  --target /path/to/installed/visual-atlas
```

The installer verifies every external pixel file, then installs this package's public metadata. It does not import the source machine's manifest, old V1 metadata or private runtime records. An old V1 pack fails the current hashes. No image generation runs during installation.

## Frontend reproduction

The installer and pinned frontend builder already copy `AagImageComposerPanel`. Its `index.jsx` includes the exact live prompt-disclosure module after the `BEGIN LIVE ATLAS PROMPT DISCLOSURE` marker. This keeps the live behavior self-contained in the existing overlay and requires no unrelated Image Composer mode changes or extra build hooks. Its stylesheet and localization file carry the live UI changes. No production bundle or local browser profile is published.

Use the existing install/update procedure and rebuild the pinned AnythingLLM frontend as described in `FRESH-INSTALL.md`. Runtime preview and thumbnail routes are unchanged.

## Validation

```bash
python3 -m unittest discover -s integrations/anythingllm/model-neutral-compatibility -p 'test_visual_atlas*.py'
node --test image-system/tests/visual-atlas*.test.js image-system/tests/inline-composer-integration.test.js
bash tools/sanitize.sh .
bash doctor.sh --package
```

Optional UI acceptance needs Brave/Chromium and Python `websocket-client`. Point it at an installed, authorized AnythingLLM image-generator workspace with the current pixel pack:

```bash
python3 image-system/tools/atlas-ui-acceptance.py \
  --url https://your-anythingllm/workspace/image-generator \
  --output /tmp/atlas-ui-acceptance
```

It checks the four sizes, icon controls and tooltips, keyboard selection, saved XLarge preference, narrow windows, internal long-prompt scrolling, exact clipboard text, and actual browser zoom at 100% and 80%. It does not submit generation jobs. Screenshots and browser results are local test outputs, not repository assets.

The source implementation passed these live UI checks before publication. Publication metadata records use of the current images as-is; it does not claim that every image passed visual review.
