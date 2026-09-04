# Release Readiness

Target tag: `v1.0.0`. Code, transactional lifecycle, compatibility gates, deterministic overlay, metadata-only Atlas, sanitization, and rollback are ready for public release.

Known limitations: only one AnythingLLM commit is supported; Ubuntu x86_64 is the tested target; image/local-LLM hardware runs need external dependencies; Atlas pixels are optional and require an authorized/user-generated pack; and Ubuntu Agent is not installed. The owner-approved MIT grant closes the final publication gate.

Stockfish is optional and is never bundled or silently downloaded. Users may install it from their distribution or the official Stockfish release channel, subject to its GPLv3 terms. Docker is detected and reported; it is only required for services configured to use Docker-backed runtimes.
