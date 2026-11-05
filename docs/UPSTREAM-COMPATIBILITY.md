# Upstream Compatibility

The sole supported baseline is `Mintplex-Labs/anything-llm` commit `9b1fcab141000eaba9f78ed131151de04904454b` (v1.19.0). `tools/verify-upstream.py` validates 37 stock source hashes before any write. Additive targets must not already contain unreviewed data; every installed target is staged, backed up, and rollback-recorded.

Backend and frontend provenance is machine-readable in `PATCH-MANIFEST.yaml`. Source reconstruction, not a copied compiled tree, is the supported distribution mechanism. Any other AnythingLLM commit is rejected until its anchors, hashes, build, and focused tests are requalified.

- Project: `https://github.com/Mintplex-Labs/anything-llm.git`
- Verified source revision from AAG build provenance: `9b1fcab141000eaba9f78ed131151de04904454b`
- Required image: `mintplexlabs/anythingllm` pinned by digest, never by tag
- Immutable container digest: `sha256:daf5a6ae9d363ed578e7d41e2d0668c7eaa604bd90b0ff27435f893b0906833e`

Do not apply replacement overlays to another revision. A moving `latest` tag is not an acceptable compatibility proof. The installer fails closed unless the user explicitly supplies a matching source root/revision and required target files.
