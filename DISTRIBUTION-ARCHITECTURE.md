# Distribution Architecture

The repository is immutable package input. `tools/config.sh` resolves defaults plus the user's config; `install.sh` verifies the pinned AnythingLLM commit and seven stock hashes, builds a transaction manifest, stages copies and installs profile-selected components. Runtime code is placed below `AAG_INSTALL_ROOT`; mutable state/backups live under `AAG_STATE_ROOT`; user configuration lives under `AAG_CONFIG_ROOT`; AnythingLLM skills live only in its scanner root.

Rendered systemd units use resolved installation/storage paths. External Atlas pixels, models, ComfyUI, llama.cpp and Stockfish remain dependency boundaries. Updater restores the known stock baseline before reapplying a release. Uninstaller replays the transaction manifest and preserves data/assets by default.

