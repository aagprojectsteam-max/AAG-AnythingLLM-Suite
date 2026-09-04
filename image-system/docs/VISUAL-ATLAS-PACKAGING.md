# Visual Atlas 1.0.0 product packaging and recovery

Status: mandatory immutable AAG Image System product asset  
Atlas: 28 families, 493 styles, 493 reference PNGs, 493 display thumbnails

## Canonical product locations

The installed AAG Image System root is the package boundary. There is one
canonical live corpus; release staging does not create another 226+ MB copy.

| Product content | Canonical path relative to AAG Image System root |
|---|---|
| Taxonomy | `image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json` |
| Entry/reference manifest | `visual-atlas/manifest/atlas-manifest.json` |
| Sealed product asset manifest | `visual-atlas/manifest/product-assets.json` |
| Browser preview index | `visual-atlas/manifest/preview-index.json` |
| Retrieval aliases | `visual-atlas/manifest/retrieval-aliases.json` |
| Completion/runtime metadata | `visual-atlas/state/atlas-state.json` |
| Completion report | `visual-atlas/reports/completeness-audit.json` |
| 493 canonical references | `visual-atlas/images/<family>/<subfamily>/preview.png` |
| 493 display thumbnails | `visual-atlas/thumbs/<family>/<subfamily>.webp` |

`product-assets.json` binds every reference and thumbnail path, byte length and
SHA-256. It also binds all small metadata files. The older Atlas manifest
remains byte-for-byte unchanged and remains the authority for the 493 reference
PNG SHA-256 values. The taxonomy remains in its established canonical Composer
location, so the runtime, UI and provider build snapshots do not acquire a
second maintained taxonomy.

## Release and fresh-install contract

An AAG Image System release is the complete product root, including the
top-level `visual-atlas` directory. `image-agent/releases/staged/<release>` is a
code activation payload inside that product, not a standalone Atlas-free
installer. Every code build runs the full Atlas gate before writing a release
and records the required version and set hashes in both `STAGED-MANIFEST.json`
and `VISUAL-ATLAS-ASSET-REQUIREMENT.json`. A missing or corrupt Atlas therefore
fails the build.

For a normal installation or migration, copy/restore the complete product root
and run:

```bash
node image-agent/tools/visual-atlas-product.js
```

If an operator intentionally transports large immutable assets separately,
create the mandatory versioned bundle at the source installation:

```bash
node image-agent/tools/visual-atlas-product.js --export /backup/aag-visual-atlas-1.0.0
```

The destination must not exist, preventing a partial old bundle from being
silently reused. The exporter copies the canonical taxonomy and required Atlas
subset, then verifies the exported bytes. On a fresh AAG code installation
whose `visual-atlas` directory is absent, restore atomically with:

```bash
node image-agent/tools/visual-atlas-product.js \
  --project-root /installed/AAG-Image-System \
  --install-from /backup/aag-visual-atlas-1.0.0
```

The installer first verifies the bundle, requires the installed code taxonomy
to match, stages the directory, renames it into place, and verifies the result.
It does not replace an existing Atlas. A failed post-copy verification is moved
aside as `visual-atlas.failed-<timestamp>` for diagnosis instead of becoming
active. This is the only supported Atlas-free source/code transport mode; the
versioned asset bundle is mandatory.

## Integrity gate

Successful verification ends with these operator-readable lines:

```text
EXPECTED_ATLAS_STYLES=493
REFERENCES_VALID=493/493
THUMBNAILS_VALID=493/493
MANIFEST_INTEGRITY=PASS
```

The gate validates version 1.0.0, 28/493 taxonomy cardinality, taxonomy ↔
manifest bijection, all 493 manifest SHA-256 values and PNG dimensions, all 493
sealed thumbnail SHA-256 values and WebP signatures, metadata hashes, complete
healthy/idle state, and unchanged thermal thresholds. Tests also prove that a
missing thumbnail and a corrupt reference make installation verification fail.
Do not use `--seal` during ordinary operation; it is a product-maintainer action
for an intentional Atlas version, never a repair mechanism.

## Runtime and browser delivery

All runtime resolvers use the installed product root or the read-only container
mount. In Production the host canonical `visual-atlas` directory is mounted
read-only at `/app/server/storage/aag-visual-atlas`. AnythingLLM never exposes a
host path. Its controlled workspace route obtains only a validated canonical
family/subfamily asset:

```text
/api/aag-composer/image-generator/atlas-thumbnail/:family/:subfamily
/api/aag-composer/image-generator/atlas-preview/:family/:subfamily
```

The browser uses an authenticated same-origin fetch with explicit Image
Generator workspace context, validates status and image content type, then
displays an in-memory blob URL. Gallery retrieval is viewport-lazy. Normal
production access to the mounted Atlas is read-only; the completed runner is not
started by installation, browsing or generation.

## Backup and restore

A complete system backup must include both:

1. the AAG source/code product root; and
2. the entire canonical `visual-atlas` directory plus the canonical taxonomy.

Use `--export` for a self-verifying Atlas-only backup, or preserve the entire
product root with permissions and relative layout. After any restore, run the
integrity gate before starting AnythingLLM. Then verify the Docker bind is
read-only and run the production endpoint regression and browser acceptance.

Never restore only `images/`, reconstruct thumbnails, regenerate references, or
edit `atlas-manifest.json` to make mismatched files appear valid. Restore the
sealed 1.0.0 set as a unit. The rollback for the Preview 13 browser/code change
does not delete or roll back the canonical Atlas; its hashes are validated both
before and after rollback.
