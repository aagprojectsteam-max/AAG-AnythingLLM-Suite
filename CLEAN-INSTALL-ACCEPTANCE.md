# Clean Install Acceptance

Tested on 2026-09-04 in `/tmp/aag-rc1-final.*` against a clean AnythingLLM checkout at the pinned commit, with isolated data/state/config/storage/systemd roots. A prior longer lifecycle run also covered update and reinstall.

- full install: PASS
- exact upstream hash gate: PASS (7/7)
- rendered portable units: PASS (4)
- external Atlas verification/install: PASS (986/986)
- installed doctor: PASS
- Agent Skills, PDF/artifacts, Chess, Composer, progress/cancel and canonical hashes: PASS
- update: PASS
- uninstall with data/models/Atlas/config preservation: PASS
- reinstall: PASS
- second uninstall and clean upstream worktree: PASS
- compatibility: PASS (125/125 with the verified external Atlas root)
- focused Composer/progress/export: PASS (34/34)
- deterministic Chess tests not requiring a live engine: PASS (87/87)
- shell, Python, JavaScript and JSON static validation: PASS

ComfyUI generation and local-LLM inference were reported optional in the isolated root because models/hardware runtimes were intentionally not copied into it.

The broad Node and Chess collections include live-service/engine cases. Those are represented by the focused deterministic suites above and by the separate live read-only doctor; they are not treated as clean-root failures when their external services are absent.
