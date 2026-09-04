# AAG AnythingLLM Suite 1.0.0-rc1

Private release candidate for an end-user Ubuntu x86_64 distribution of the AAG AnythingLLM extensions.

## Highlights

- Profiles: `core`, `pdf`, `chess`, `image`, `local-llm`, `full`.
- Transactional install with exact AnythingLLM revision/file-hash gates, backup and automatic rollback.
- Portable XDG-based configuration, generated systemd user units, hardware/model detection and one-command doctor.
- Safe update, rollback and uninstall that preserve conversations, models, Atlas assets and user configuration by default.
- Verified owner-supplied Visual Atlas flow covering 493 previews plus 493 thumbnails.

## Install

```bash
git clone <private-repository-url> aag-anythingllm-suite
cd aag-anythingllm-suite
./install.sh --anythingllm-root /path/to/anythingllm --storage /path/to/anythingllm/storage
./doctor.sh
```

Only AnythingLLM commit `07bd65f80b3d9ba3031ed7afb8786627326bd134` is accepted. Model weights, ComfyUI, llama.cpp, Stockfish binaries and Atlas pixels are not bundled. Supply only assets whose licenses permit your use; the installer does not silently download them.

## Acceptance

Full-profile isolated install/doctor/update/uninstall/reinstall passed. Compatibility passed 125/125, focused Composer passed 34/34, deterministic Chess passed 87/87, and the external Atlas pack passed 986/986. Final production verification was read-only and healthy.

## Limitations

Ubuntu x86_64 is the tested platform. GPU image generation and accelerated local inference depend on user-provided runtimes and hardware. Public release remains blocked on complete top-level licensing/provenance and an explicit Atlas pixel redistribution grant. Keep this RC private.
