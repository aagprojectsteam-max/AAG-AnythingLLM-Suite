# Final Atlas Distribution Decision

The external canonical Atlas contains 493 PNG references and 493 derived WebP thumbnails: 986/986 files. The manifest records per-file size/SHA-256 and aggregate reference/thumbnail hashes. Read-only records show local AAG workflow generation using a FLUX model and AAG-authored prompts.

Classification:

- Atlas taxonomy, manifests, hashes, and AAG runtime metadata: `AAG_GENERATED_REDISTRIBUTABLE` only after an owner license grant; currently repository metadata only.
- 493 thumbnails: `UNKNOWN` because they derive from the reference pixels and no owner/model-output redistribution evidence is recorded.
- 493 reference PNGs: `UNKNOWN` for the same reason.
- Model weights and external engine assets: `DO_NOT_REDISTRIBUTE` from this project.

No pixel archive or checksum is published. The supported alternative is the existing owner-supplied local pack verified and installed by `tools/atlas-assets.py`. Missing pixels are reported clearly by doctor; metadata and code remain available, but Atlas-dependent browsing/Composer tests require the pack.

`ATLAS_DISTRIBUTION_STATUS=EXTERNAL_ONLY_RIGHTS_UNRESOLVED`

`ATLAS_RELEASE_ASSET=NONE`

`ATLAS_SHA256=NOT_APPLICABLE`
