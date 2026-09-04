#!/usr/bin/env bash
set -euo pipefail
: "${ANYTHINGLLM_STORAGE:?Set ANYTHINGLLM_STORAGE}"
marker=$ANYTHINGLLM_STORAGE/.aag-suite-last-backup
[[ -f "$marker" ]] || { echo 'No managed AAG installation found' >&2; exit 1; }
backup=$(<"$marker")
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rollback.sh" "$backup"
rm -f -- "$marker"
echo 'UNINSTALL=PASS user-data-and-models-preserved=true'

