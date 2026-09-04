#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
profile=full; dry=0
while (($#)); do case "$1" in --profile) profile=$2; shift 2;; --dry-run) dry=1; shift;; *) echo "Unknown argument: $1" >&2; exit 2;; esac; done
case "$profile" in core|image|chess|pdf|local-llm|full) ;; *) echo "Invalid profile" >&2; exit 2;; esac
: "${ANYTHINGLLM_ROOT:?Set ANYTHINGLLM_ROOT to an AnythingLLM source checkout}"
: "${ANYTHINGLLM_STORAGE:?Set ANYTHINGLLM_STORAGE}"
SYSTEMD_USER_DIR=${SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}
AAG_COMPONENT_ROOT=${AAG_COMPONENT_ROOT:-$ANYTHINGLLM_STORAGE/aag-suite}
[[ -d "$ANYTHINGLLM_ROOT/server" ]] || { echo 'AnythingLLM source root invalid' >&2; exit 1; }
revision=$(git -C "$ANYTHINGLLM_ROOT" rev-parse HEAD 2>/dev/null || true)
expected=07bd65f80b3d9ba3031ed7afb8786627326bd134
[[ "$revision" == "$expected" ]] || { echo "UPSTREAM_MISMATCH expected=$expected actual=${revision:-unknown}" >&2; exit 1; }
"$ROOT/doctor.sh" --preflight
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=${AAG_BACKUP_ROOT:-$ANYTHINGLLM_STORAGE/aag-suite-backups}/$timestamp
manifest=$backup/manifest.tsv
declare -a mappings=()
add(){ mappings+=("$1|$2"); }
if [[ "$profile" == core || "$profile" == image || "$profile" == full ]]; then
  for skill in aag-image-task aag-image-batch aag-image-job; do add "$ROOT/image-system/skills/$skill" "$ANYTHINGLLM_STORAGE/plugins/agent-skills/$skill"; done
  add "$ROOT/patches/anythingllm/aagIdentity.js" "$ANYTHINGLLM_ROOT/server/utils/chats/commands/aagIdentity.js"
  add "$ROOT/patches/anythingllm/aagOrdinary.js" "$ANYTHINGLLM_ROOT/server/utils/chats/commands/aagOrdinary.js"
  add "$ROOT/patches/anythingllm/chats-index.js" "$ANYTHINGLLM_ROOT/server/utils/chats/index.js"
  add "$ROOT/patches/anythingllm/chat-apiChatHandler.js" "$ANYTHINGLLM_ROOT/server/utils/chats/apiChatHandler.js"
  add "$ROOT/patches/anythingllm/context-window-finder.offline.js" "$ANYTHINGLLM_ROOT/server/utils/AiProviders/modelMap/index.js"
  add "$ROOT/patches/anythingllm/agents-index.js" "$ANYTHINGLLM_ROOT/server/utils/agents/index.js"
  add "$ROOT/patches/anythingllm/agents-ephemeral.js" "$ANYTHINGLLM_ROOT/server/utils/agents/ephemeral.js"
  add "$ROOT/patches/anythingllm/toolReranker.js" "$ANYTHINGLLM_ROOT/server/utils/agents/aibitat/utils/toolReranker.js"
  add "$ROOT/patches/anythingllm/aagComposerProxy.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagComposerProxy.js"
  add "$ROOT/patches/anythingllm/aagImageProgress.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagImageProgress.js"
  add "$ROOT/image-system" "$AAG_COMPONENT_ROOT/image-system"
fi
if [[ "$profile" == chess || "$profile" == full ]]; then add "$ROOT/chess/integrations/anythingllm/skill/aag-chess-puzzle" "$ANYTHINGLLM_STORAGE/plugins/agent-skills/aag-chess-puzzle"; add "$ROOT/chess" "$AAG_COMPONENT_ROOT/chess"; fi
if [[ "$profile" == pdf || "$profile" == full ]]; then
  add "$ROOT/patches/anythingllm/aagArtifactExport.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagArtifactExport.js"
  add "$ROOT/patches/anythingllm/server-index.js" "$ANYTHINGLLM_ROOT/server/index.js"
  add "$ROOT/patches/anythingllm/aagPdfAssembler.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagPdfAssembler.js"
fi
if [[ "$profile" == local-llm || "$profile" == full ]]; then add "$ROOT/integrations/llamacpp" "$AAG_COMPONENT_ROOT/llamacpp"; fi
if [[ "$profile" == full ]]; then add "$ROOT/integrations/ubuntu-agent" "$AAG_COMPONENT_ROOT/ubuntu-agent"; fi
if (( dry )); then printf 'WOULD_INSTALL=%s -> %s\n' "${mappings[@]//|/ -> }"; echo "DRY_RUN=PASS profile=$profile"; exit 0; fi
mkdir -p "$backup"; : > "$manifest"
rollback(){ "$ROOT/rollback.sh" "$backup" || true; }
trap rollback ERR
for entry in "${mappings[@]}"; do src=${entry%%|*}; dst=${entry#*|}; mkdir -p "$(dirname "$dst")"; if [[ -e "$dst" ]]; then rel=${dst#/}; mkdir -p "$backup/files/$(dirname "$rel")"; cp -a "$dst" "$backup/files/$rel"; printf 'RESTORE\t%s\n' "$dst" >> "$manifest"; else printf 'REMOVE\t%s\n' "$dst" >> "$manifest"; fi; tmp="${dst}.aag-stage-$timestamp"; cp -a "$src" "$tmp"; mv "$tmp" "$dst"; done
find "$ANYTHINGLLM_STORAGE/plugins/agent-skills" -mindepth 2 -maxdepth 2 -name plugin.json -print0 | xargs -0 -r -n1 python3 -m json.tool >/dev/null
find "$ROOT" -type f -not -path '*/.git/*' -print0 | sort -z | xargs -0 sha256sum > "$backup/package.sha256"
trap - ERR
echo "$backup" > "$ANYTHINGLLM_STORAGE/.aag-suite-last-backup"
echo "INSTALL=PASS profile=$profile backup=$backup"
