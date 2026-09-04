# Tests and CI

- Package doctor: PASS.
- Secret scans: PASS/PASS.
- Chess suite: PASS when run outside restricted socket sandbox (output reached 100%).
- Live AAG release doctor: PASS, including 125 Python tests.
- Node package tests: core/security/artifact/batch/policy/runtime/scheduler/progress/governed-update paths pass; Atlas pixel/frontend-source-dependent tests are gated by excluded assets.
- CI performs syntax, JSON and sanitization gates without models or private secrets.

