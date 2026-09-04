# AnythingLLM Compatibility

| AnythingLLM commit | AAG Suite | Patch mode | Status |
|---|---|---|---|
| `07bd65f80b3d9ba3031ed7afb8786627326bd134` | `1.0.0-rc1` | exact commit + seven stock SHA-256 gates | Tested PASS |
| Any other commit/tag/image | `1.0.0-rc1` | none | Unsupported; installer fails closed |

Compatibility data is machine-readable in `config/compatibility.json`. Migration to a later AnythingLLM release requires a reviewed rebase, new stock hashes and clean-install acceptance; fuzzy patching is prohibited.

