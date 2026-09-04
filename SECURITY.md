# Security

Report vulnerabilities privately to the repository owner. Do not attach production logs, databases, prompts, uploads, tokens, certificates or user artifacts to issues.

The suite treats AnythingLLM storage and all generated state as sensitive. Services bind to loopback or a narrowly scoped Docker bridge. Artifact access is owner-scoped, identity inputs are request-bound, and installer writes are backup-first. Run `tools/sanitize.sh` before every commit and push.

