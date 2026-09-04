# AnythingLLM Patches

`PATCH-MANIFEST.yaml` records all 21 backend/frontend overlay targets with upstream commit, upstream SHA-256 where a stock file exists, repository patch, patch SHA-256 for single files, purpose, test, and reconstruction status. `config/compatibility.json` gates all eleven replaced stock files before installation.

The frontend source overlay is complete and deterministic. `image-system/tools/build-anythingllm-frontend.js` transforms the pinned PromptInput and ChatContainer using unique anchors; the generated results are committed under `patches/anythingllm/frontend/`. The remaining UI components are explicit replacement or additive source files. The installer stages and backs up every target. Compiled upstream assets are intentionally not redistributed; users build the patched source through AnythingLLM's normal frontend process.

Clean reconstruction is verified from AnythingLLM commit `07bd65f80b3d9ba3031ed7afb8786627326bd134`, without reading live production files.

`ANYTHINGLLM_OVERLAY_PROVENANCE=PASS`

`ANYTHINGLLM_CLEAN_RECONSTRUCTION=PASS`
