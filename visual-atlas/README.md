# Visual Atlas — current V2 image set

This directory contains public metadata for the 493 current V2 images, using the existing Atlas integration and catalog contract. Taxonomy, canonical IDs, ordering and normal generation style descriptors are unchanged.

`manifest/atlas-manifest.json` binds each canonical ID to its exact PNG hash, historical generation prompt and prompt hash. Public provenance includes the original workflow/model hashes and seed. Private job state, machine paths, backups and review evidence are excluded.

The 493 PNG images and 493 WebP derivatives remain outside Git under the repository's existing asset publication policy. An authorized external pack must match `atlas-assets-manifest.json`; the installer uses the public metadata shipped here. Older V1 pixels do not match the new asset hashes.

The browser supports exact Show/Hide/Copy Prompt, compact accessible card icons, viewport-bounded prompt scrolling and Small/Medium/Large/XLarge sizes. See [current implementation and reproduction](../docs/VISUAL-ATLAS-V2.md) and [asset distribution policy](../ATLAS-ASSET-DISTRIBUTION.md).

The `1.0.0` catalog identifier remains compatible with existing consumers. `image_set: visual-atlas-v2` identifies the current image content. Asset verification does not require private scheduler state or thermal telemetry.
