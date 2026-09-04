# AAG AnythingLLM Suite

AAG AnythingLLM Suite is a private-first distribution that adds governed image generation, native Composer controls, Visual Atlas style selection, safe artifacts/PDFs, verified chess puzzles, and optional local-LLM and Ubuntu operations to a compatible AnythingLLM source installation.

It is an installer-driven distribution—not a backup of one workstation. Original-machine paths, usernames, models, conversations and secrets are not required or included.

## Supported platform

- Ubuntu Linux on `x86_64` is the tested release target.
- AnythingLLM source commit `07bd65f80b3d9ba3031ed7afb8786627326bd134` is the only supported patch baseline for RC1.
- Docker may host AnythingLLM, but an exact source checkout is required to build/apply the overlay safely.
- Core/PDF/Chess work without a GPU. Image features need a compatible ComfyUI installation and external models. The accepted accelerated local-LLM path is Intel GPU + Level Zero/SYCL; CPU llama.cpp is user-supplied. NVIDIA is detected but not claimed as an accepted AAG local-LLM backend.

## Quick start

```bash
git clone <private-repository-url> aag-anythingllm-suite
cd aag-anythingllm-suite
./install.sh --anythingllm-root /path/to/anything-llm \
  --storage /path/to/anythingllm/storage
./doctor.sh
```

When AnythingLLM is in `~/anything-llm` or `/opt/anything-llm`, `./install.sh` can detect it automatically. Always run `--dry-run` first on an existing installation.

## Profiles

| Profile | Installs | External requirements |
|---|---|---|
| `core` | common artifact/PDF endpoints, doctor, update and rollback | exact AnythingLLM source |
| `pdf` | core artifact export and PDF assembly | exact AnythingLLM source |
| `chess` | chess code, skill and rendered user service | Python packages; Stockfish optional/recommended |
| `image` | image skills, Composer sources, Atlas integration, progress/cancel, identity and bridges | ComfyUI, models, authorized Atlas pack |
| `local-llm` | model-neutral compatibility and llama.cpp controller | user-built llama.cpp and GGUF; SYCL optional |
| `full` | all supported code except external weights/assets | union of the above |

## Models and assets

Weights are never downloaded silently. Configure `AAG_MODEL_ROOT`; `./doctor.sh` reports each dependency as `FOUND`, `MISSING`, or `OPTIONAL`. See `config/models.yaml` and `MODEL-ASSET-SETUP.md`.

The 493 Atlas references and 493 thumbnails are not in Git because no explicit redistribution grant was found. An authorized owner can install a byte-exact pack:

```bash
./tools/atlas-assets.py verify --source /path/to/visual-atlas
./tools/atlas-assets.py install --source /path/to/visual-atlas \
  --target "$HOME/.local/share/aag-anythingllm-suite/visual-atlas"
```

`atlas-assets-manifest.json` contains every expected path, byte size and SHA-256. This resolves the four asset-dependent tests without weakening them.

## ComfyUI and local LLM

ComfyUI, custom nodes and model weights are external. Configure `COMFYUI_ROOT`; the installer deploys AAG-owned orchestration only. For local inference, install [llama.cpp](https://github.com/ggml-org/llama.cpp). Intel users can follow its [official SYCL guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/SYCL.md). MTP remains approved-pair-only; never attach a Gemma sidecar to another model.

## Chess, PDF and artifacts

Chess correctness comes from deterministic legal-move proof; Stockfish is a candidate generator/cross-check, not the authority. Install Stockfish from Ubuntu's package manager or the [official Stockfish project](https://stockfishchess.org/download/). Artifact and PDF endpoints validate ownership and opaque storage references and do not expose arbitrary filesystem access.

## Configuration and lifecycle

Defaults live in `config/defaults.env`; user overrides live in `~/.config/aag-anythingllm-suite/config.env`. Paths, ports, models, Atlas, ComfyUI, llama.cpp, outputs and systemd context are configurable.

```bash
./doctor.sh             # actionable health report
./update.sh             # transactional update with rollback
./uninstall.sh          # preserve config, models, Atlas and user data
./uninstall.sh --purge-config
./rollback.sh BACKUP_DIRECTORY
```

## Security and limitations

The installer checks the exact upstream Git commit and stock-file hashes before patching, stages every write, backs up replaced files and rolls back on failure. It never modifies conversations or model directories. Keep the repository private: a top-level AAG public license grant and Atlas redistribution grant are still unresolved, and the production frontend requires building the captured source overlays against the pinned AnythingLLM revision.

See `FRESH-INSTALL.md`, `DOCTOR.md`, `ATLAS-ASSET-DISTRIBUTION.md`, `RELEASE-READINESS.md`, `SECURITY.md`, and `THIRD_PARTY_NOTICES.md`.
