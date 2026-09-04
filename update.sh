#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); source "$ROOT/tools/config.sh"
[[ -f $AAG_STATE_ROOT/install.env ]] || { echo 'No managed installation found.' >&2; exit 1; }
source "$AAG_STATE_ROOT/install.env"; old_backup=$AAG_BACKUP
echo "UPDATE_FROM=$AAG_SUITE_VERSION profile=$AAG_PROFILE"
"$ROOT/rollback.sh" "$old_backup"
if ! "$ROOT/install.sh" --profile "$AAG_PROFILE" --anythingllm-root "$ANYTHINGLLM_ROOT" --storage "$ANYTHINGLLM_STORAGE" --install-root "$AAG_INSTALL_ROOT" "$@"; then
  echo 'UPDATE_FAILED; restoring previous installed tree' >&2
  "$ROOT/install.sh" --profile "$AAG_PROFILE" --anythingllm-root "$ANYTHINGLLM_ROOT" --storage "$ANYTHINGLLM_STORAGE" --install-root "$AAG_INSTALL_ROOT" --skip-tests
  exit 1
fi
echo 'UPDATE=PASS'

