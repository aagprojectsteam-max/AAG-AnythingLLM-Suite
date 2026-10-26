# Colorize operation (black-and-white restoration)

Added 2026-10-26. Fourth closed `aag-image-task` operation next to `generate`,
`transform` and `upscale`.

## Semantics
- `operation=colorize`, `preservation=none` (or `auto`), `count=1`, no `scale`,
  no `width`/`height`/`aspect_ratio` (output keeps the source dimensions).
- Source comes from `current_attachment` or `previous_artifact` like `upscale`.
- `quality` selects the model tier: `fast`/`auto`/`balanced` → `ddcolor_paper_tiny.pth`
  at input size 512; `quality` → `ddcolor_modelscope.pth` at input size 1024.
- `final_output_quality=enhanced_2x` is allowed and runs the existing upscale
  stage after colorization (same chain as generate/transform).
- No prompt text reaches the engine. The task `prompt` is still required by the
  public contract; it is recorded for provenance only. `prompt-quality` skips
  the operation exactly as it skips `upscale`.

## Engine
- ComfyUI graph: `LoadImage` → `DDColor_Colorize` → `SaveImage`
  (`src/colorize.js`, workflow id `colorize.restore.auto.v1`, revision
  `colorize.ddcolor.v1`, route `comfy-colorize-v1`).
- Custom node: `custom_nodes/ComfyUI-DDColor` (kijai, archive `main`, owner-downloaded).
  Checkpoints are symlinks to `/mnt/data/AI/Models/Checkpoints/DDColor/`.
- `verifyRuntime` fails closed (`CAPABILITY_UNAVAILABLE`) when the node or the
  profile checkpoint is not registered in `/object_info`.
- Sources above 4 MP are downscaled (bounded engine input); otherwise the
  publisher verification requires exact dimension preservation (`adapters.verifyArtifact`).
- The trusted publisher (`image-hub.py`) accepts `GEN`/`REF` kinds only; colorized
  outputs are imported as `REF`.
- Stall profile `colorize` in `comfy.js`; `DDColor_Colorize` is classified as a
  `sample` phase for progress heuristics.

## Composer
- Operation select: "Colorize black-and-white photo" / "צביעת תמונת שחור-לבן".
- `compatibility.py`: `COMPOSER_ENUMS.operation` includes `colorize`; validation
  branch mirrors upscale (approved source, preservation none, one output, no
  creative controls) but keeps `quality` as the model tier.
- The deterministic Composer intent pins `operation/quality/source_policy/preservation`.

## Governance
- The frozen `registry/workflows.json` is untouched. The workflow is documented in
  `registry/workflows-extensions.json`; merging requires an explicit frozen-policy
  migration approved by the owner.
- Acceptance evidence (isolated XPU run, 2026-10-26): tiny@512 4.2 s / 1.1 GB,
  modelscope@512 4.7 s / 1.8 GB, modelscope@1024 2.2 s warm / 4.2 GB.

## Tests
- `tests/colorize.test.js` (contract, graph, execute with mocked engine, fail-closed).
- `tests/public-tool-schema.test.js` (enum published to the model).
- `integrations/model-neutral-compatibility/test_composer_colorize.py` (Composer intent).
