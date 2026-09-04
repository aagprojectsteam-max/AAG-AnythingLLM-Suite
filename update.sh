#!/usr/bin/env bash
set -euo pipefail
: "${ANYTHINGLLM_STORAGE:?Set ANYTHINGLLM_STORAGE}"
[[ -f "$ANYTHINGLLM_STORAGE/.aag-suite-last-backup" ]] || { echo 'No managed AAG installation found' >&2; exit 1; }
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" "$@"

