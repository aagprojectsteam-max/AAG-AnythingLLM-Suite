# Release Readiness

Target tag: `v1.0.0-rc1`. Code, transactional lifecycle, compatibility gates, Atlas external-pack verifier, clean-install acceptance, sanitization and rollback are release-ready for a private repository.

Known limitations: only one AnythingLLM commit is supported; Ubuntu x86_64 is the tested target; image/local-LLM hardware runs need external dependencies; Atlas pixels are optional and require an authorized/user-generated pack; and Ubuntu Agent is not installed. Overlay reconstruction, metadata-only Atlas operation, and Ubuntu Agent optionalization are complete. The sole remaining publication gate is an explicit copyright-owner AAG license grant, so the repository remains private pending that approval.

Stockfish is optional and is never bundled or silently downloaded. Users may install it from their distribution or the official Stockfish release channel, subject to its GPLv3 terms. Docker is detected and reported; it is only required for services configured to use Docker-backed runtimes.
