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

## 2026-11-06: AnythingLLM 1.19.0 port candidate

The record above belongs to RC1 on 07bd65f8. This one covers branch `port/anythingllm-1.19.0` on commit 9b1fcab.

Run in a throwaway container of the pinned 1.19.0 image with `--network none`, against a clean checkout, with isolated data, state, config, storage and systemd roots. The Atlas was mounted read-only.

- altered stock file stops the installer before any write: PASS
- dry run: PASS
- package doctor: PASS
- full install: PASS
- golden hashes after install, update and reinstall: PASS (107/107)
- rendered units: PASS (4)
- Atlas verification: PASS
- installed doctor: PASS
- update: PASS
- uninstall and reinstall: PASS
- upstream tree clean after each uninstall: PASS
- conversations, models, Atlas and config preserved: PASS
- shell and Python syntax: PASS

The first run failed 8 of 19 steps. Its causes and fixes are in `ports/anythingllm-1.19.0/PORT-NOTES.md`.

One cosmetic leftover remains: uninstall leaves an empty `ChatHistory/AagImageCollection` directory.

This is a package result. It does not cover real providers, bridges, image generation or a live deployment.
