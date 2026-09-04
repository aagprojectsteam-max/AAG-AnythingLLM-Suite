# AAG AnythingLLM Suite 1.0.0

Public production release of the AAG enhancements for a pinned AnythingLLM source installation on Ubuntu x86_64.

## Included

- `core`, `pdf`, `chess`, `image`, `local-llm`, and `full` installation profiles.
- Transactional install with eleven stock-file hash gates, staging, backup, rollback, update, uninstall, and reinstall.
- Deterministic AnythingLLM backend and frontend overlay covering Composer, image cards/collections, Atlas selection, artifacts/PDF, and progress/status/cancel.
- Four AnythingLLM Agent Skills in the full profile: image task, image batch, image job, and verified chess puzzle.
- Visual Atlas taxonomy, aliases, descriptors, and integrity metadata for 493 styles.
- Model-neutral local-LLM compatibility and optional Intel Level Zero/SYCL path.
- Portable systemd user units generated for the selected profile.

## External components

No model weights, GGUF/mmproj/MTP files, ComfyUI installation, llama.cpp binary, Stockfish binary, custom nodes, or Atlas pixels are bundled. Doctor reports missing models and runtimes without silently downloading them.

Atlas runs in metadata-only mode by default. Users with a lawfully generated or authorized pack can verify/install all 493 PNG references and 493 WebP thumbnails with `tools/atlas-assets.py`. The public release does not attach those pixels.

## Hardware

Core, PDF, and Chess support CPU-only systems. Image generation uses the user's compatible ComfyUI environment. Intel Arc/Level Zero/SYCL is the qualified accelerated local-LLM path; CPU llama.cpp is supported when user supplied. NVIDIA/AMD local-LLM acceleration is not claimed. MTP is approved-pair-only.

## Acceptance

- clean AnythingLLM reconstruction and 11/11 stock hash verification: PASS
- full install, doctor, update, rollback, uninstall, reinstall: PASS
- Composer/Visual Atlas/model-neutral compatibility: 125/125
- focused frontend/image/artifact/progress: 64 passed, 3 optional pixel-pack tests skipped
- Chess: 224 passed, 6 optional real-engine tests deselected
- secret/history/model/large-blob scans: PASS
- live production changed: NO

## Limitations

AnythingLLM commit `07bd65f80b3d9ba3031ed7afb8786627326bd134` is the only supported baseline. Ubuntu Agent is intentionally not installed because the historical capture is host-specific. External components retain their own license terms.
