# Release Readiness

Target tag: `v1.0.0-rc1`. Code, transactional lifecycle, compatibility gates, Atlas external-pack verifier, clean-install acceptance, sanitization and rollback are release-ready for a private repository.

Known limitations: only one AnythingLLM commit is supported; Ubuntu x86_64 is the tested target; image/local-LLM hardware runs need external dependencies; Atlas pixels require an authorized pack; no top-level AAG public license grant or Atlas redistribution grant was found. Therefore RC1 must remain private and is not ready for public release.

Stockfish is optional and is never bundled or silently downloaded. Users may install it from their distribution or the official Stockfish release channel, subject to its GPLv3 terms. Docker is detected and reported; it is only required for services configured to use Docker-backed runtimes.
