# Final Security and History Audit

All five reachable commits and all tags were audited. `git rev-list --objects --all`, blob-size inspection, filename/extension searches, targeted credential patterns, MIME inspection, and Gitleaks history mode were used.

- Largest reachable blob: 1,678,426 bytes; no blob exceeds 10 MiB.
- No GGUF, safetensors, checkpoints, projector/MTP weights, embeddings, rerankers, databases, uploads, conversations, browser profiles, environment secrets, cookies, sessions, or credential-named objects were found.
- Targeted patterns found no GitHub/OpenAI/Google/Slack token, private key, password assignment, proxy token, or session token.
- Gitleaks scanned five commits and produced 19 reviewed false positives: one compatibility SHA-256 and repeated Atlas taxonomy fields named `key`. None is a secret.
- Three unreachable local annotated-tag objects are earlier annotations of the same historical commits; they contain no file tree and are not remote refs.

`SECRET_SCAN=PASS`

`GIT_HISTORY_SCAN=PASS`

`MODEL_WEIGHT_SCAN=PASS`
