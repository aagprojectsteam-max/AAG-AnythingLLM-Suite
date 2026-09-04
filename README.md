# AAG AnythingLLM Suite

Private-first, sanitized source package for the AAG extensions built around AnythingLLM. The captured production release is AAG Image `0.9.0-preview.13`, built against AnythingLLM revision `07bd65f80b3d9ba3031ed7afb8786627326bd134`.

## What AAG adds

- Provider-neutral image generation, transformation, identity-preserving portrait/scene workflows, batching, quality policy, queue/status/cancel, ownership and idempotency.
- Native AnythingLLM Composer controls, progress UI, Visual Atlas selection, artifact cards, multi-image export and PDF assembly.
- ComfyUI and Image Hub bridges, optional upscale service, controlled lifecycle and XPU-aware operation.
- Model-neutral local-LLM compatibility, runtime attestation, STARTTIME/PID/UID/executable checks, llama.cpp SYCL/MTP launch policy and model switching.
- Deterministically verified chess-puzzle generation and AnythingLLM skill integration.
- Optional Ubuntu diagnostics, governed orchestration, maintenance intelligence and context memory without bundled private state.
- Transactional install/update/rollback tooling and publication gates.

Model weights, private Visual Atlas pixels, third-party binaries, conversations, databases, uploads, runtime state, logs, credentials and generated user artifacts are intentionally absent.

## Quick start

1. Review `docs/INSTALL.md`, `docs/MODELS.md`, and `docs/UPSTREAM-COMPATIBILITY.md`.
2. Copy `.env.example` to `.env` and set local roots.
3. Run `./doctor.sh --preflight`.
4. Run `./install.sh --profile core --dry-run`, then repeat without `--dry-run` when satisfied.

This package does not silently download models or modify a live installation without a backup and compatibility check.

