# Upstream Compatibility

The sole supported baseline is `Mintplex-Labs/anything-llm` commit `07bd65f80b3d9ba3031ed7afb8786627326bd134`. `tools/verify-upstream.py` validates eleven stock source hashes before any write. Additive targets must not already contain unreviewed data; every installed target is staged, backed up, and rollback-recorded.

Backend and frontend provenance is machine-readable in `PATCH-MANIFEST.yaml`. Source reconstruction, not a copied compiled tree, is the supported distribution mechanism. Any other AnythingLLM commit is rejected until its anchors, hashes, build, and focused tests are requalified.

- Project: `https://github.com/Mintplex-Labs/anything-llm.git`
- Verified source revision from AAG build provenance: `07bd65f80b3d9ba3031ed7afb8786627326bd134`
- Observed Compose tag: `mintplexlabs/anythingllm:latest`
- Immutable container digest: unresolved because Docker inspection was unavailable during packaging.

Do not apply replacement overlays to another revision. A moving `latest` tag is not an acceptable compatibility proof. The installer fails closed unless the user explicitly supplies a matching source root/revision and required target files.
