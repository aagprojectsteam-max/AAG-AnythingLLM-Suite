# Installation

Requirements: Linux with systemd user services, Bash, Node.js, Python 3, a clean AnythingLLM checkout at the recorded revision, and optional ComfyUI/llama.cpp/Stockfish dependencies per profile.

Run `./doctor.sh --preflight` and then `./install.sh --profile PROFILE --dry-run`. Profiles are `core`, `image`, `chess`, `pdf`, `local-llm`, and `full`. A real install requires `ANYTHINGLLM_ROOT` and `ANYTHINGLLM_STORAGE`, creates a timestamped backup, stages writes, validates JavaScript/JSON/Python, records hashes, and rolls back on failure.

The frontend Composer build and Visual Atlas pixel bundle must be supplied separately; their absence is reported explicitly.

