# Atlas Asset Publication Decision

`ATLAS-ASSET-PROVENANCE.json` enumerates all 986 external pixel files with their existing SHA-256 and byte counts. The repository records local generation through an AAG workflow, AAG-authored benchmark prompts, and a FLUX model. It records no downloaded reference-image inputs, but it also does not establish the model/checkpoint output license or a copyright-owner public grant.

Therefore the 493 AI-generated PNG references are `UNKNOWN`, and the 493 WebP thumbnails are `UNKNOWN` derivatives. Model weights are `NOT_REDISTRIBUTABLE` from this project. No Atlas pixel archive may be attached to a public release.

The public-safe strategy is metadata-only operation:

- all 493 taxonomy entries, aliases, style descriptors, and integrity metadata ship in Git;
- deterministic automatic and manual style selection works without pixels;
- the browser reports preview pixels unavailable instead of disabling Composer;
- an owner may generate or install a local 986-file pack with `tools/atlas-assets.py`;
- doctor distinguishes `PASS metadata-only` from `PASS pixels+metadata`;
- pixels remain outside Git and release assets.

`ATLAS_PUBLIC_STRATEGY=METADATA_ONLY_WITH_OPTIONAL_USER_GENERATED_OR_AUTHORIZED_PACK`

`ATLAS_PIXEL_PUBLICATION=PROHIBITED_UNTIL_RIGHTS_DOCUMENTED`
