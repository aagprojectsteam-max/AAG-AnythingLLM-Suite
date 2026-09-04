#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); source "$ROOT/tools/config.sh"
purge=0; [[ ${1:-} == --purge-config ]] && purge=1
[[ -f $AAG_STATE_ROOT/install.env ]] || { echo 'No managed installation found.' >&2; exit 1; }
source "$AAG_STATE_ROOT/install.env"; "$ROOT/rollback.sh" "$AAG_BACKUP"
if command -v systemctl >/dev/null; then systemctl --user daemon-reload >/dev/null 2>&1 || true; fi
rm -f "$AAG_STATE_ROOT/install.env" "$AAG_STATE_ROOT/installed.sha256" "$AAG_STATE_ROOT/last-backup"
if (( purge )); then rm -f "$AAG_USER_CONFIG"; fi
echo "UNINSTALL=PASS conversations=preserved models=preserved atlas=preserved config=$([[ $purge == 1 ]] && echo removed || echo preserved)"
