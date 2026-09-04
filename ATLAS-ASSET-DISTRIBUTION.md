# Atlas Asset Distribution

The 493 PNG references and 493 WebP thumbnails are technically packable but not included because no explicit redistribution grant was found. RC1 therefore supports an owner-supplied external pack, not Git LFS or a public release asset.

`atlas-assets-manifest.json` records version 1.0.0, 493 entries, 986 paths, exact byte sizes, individual SHA-256 values and aggregate set hashes. Verify/install with `tools/atlas-assets.py`. The expected source contains `images/`, `thumbs/`, and `manifest/`. A missing or altered file fails closed. Once installed, the previously expected four Atlas product gates have a documented resolution.

