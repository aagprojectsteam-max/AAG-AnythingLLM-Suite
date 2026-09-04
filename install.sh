#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$ROOT/tools/config.sh"
profile=${AAG_PROFILE:-full}; dry=0; skip_tests=0
while (($#)); do case "$1" in
 --profile) profile=$2; shift 2;; --anythingllm-root) ANYTHINGLLM_ROOT=$2; export ANYTHINGLLM_ROOT; shift 2;; --storage) ANYTHINGLLM_STORAGE=$2; export ANYTHINGLLM_STORAGE; shift 2;; --install-root) AAG_INSTALL_ROOT=$2; export AAG_INSTALL_ROOT; shift 2;; --atlas-source) AAG_ATLAS_SOURCE=$2; shift 2;; --dry-run) dry=1; shift;; --skip-tests) skip_tests=1; shift;; -h|--help) echo 'Usage: ./install.sh [--profile core|pdf|chess|image|local-llm|full] [--anythingllm-root PATH] [--storage PATH] [--install-root PATH] [--atlas-source PATH] [--dry-run]'; exit 0;; *) echo "Unknown argument: $1" >&2; exit 2;; esac; done
case "$profile" in core|pdf|chess|image|local-llm|full) ;; *) echo "Invalid profile: $profile" >&2; exit 2;; esac
aag_detect_anythingllm || { echo 'AnythingLLM source checkout not found. Pass --anythingllm-root.' >&2; exit 1; }; aag_resolve_storage
expected=07bd65f80b3d9ba3031ed7afb8786627326bd134; actual=$(git -C "$ANYTHINGLLM_ROOT" rev-parse HEAD 2>/dev/null || true)
[[ $actual == "$expected" ]] || { echo "INCOMPATIBLE_ANYTHINGLLM expected=$expected actual=${actual:-unknown}" >&2; exit 1; }
python3 "$ROOT/tools/verify-upstream.py" "$ANYTHINGLLM_ROOT" "$ROOT/config/compatibility.json"
(( skip_tests )) || "$ROOT/doctor.sh" --package --profile "$profile"
backup_root=${AAG_BACKUP_ROOT:-$AAG_STATE_ROOT/backups}; stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$; backup=$backup_root/$stamp; manifest=$backup/manifest.tsv
SYSTEMD_USER_DIR=${SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}; unit_stage=$AAG_STATE_ROOT/unit-stage-$stamp
declare -a maps=(); add(){ maps+=("$1|$2"); }
add_common(){ add "$ROOT/patches/anythingllm/aagArtifactExport.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagArtifactExport.js"; add "$ROOT/patches/anythingllm/aagPdfAssembler.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagPdfAssembler.js"; add "$ROOT/patches/anythingllm/server-index.js" "$ANYTHINGLLM_ROOT/server/index.js"; }
if [[ $profile == core || $profile == pdf || $profile == image || $profile == full ]]; then add_common; fi
if [[ $profile == image || $profile == full ]]; then
 for s in task batch job; do add "$ROOT/image-system/skills/aag-image-$s" "$ANYTHINGLLM_STORAGE/plugins/agent-skills/aag-image-$s"; done
 add "$ROOT/patches/anythingllm/aagIdentity.js" "$ANYTHINGLLM_ROOT/server/utils/chats/commands/aagIdentity.js"; add "$ROOT/patches/anythingllm/aagOrdinary.js" "$ANYTHINGLLM_ROOT/server/utils/chats/commands/aagOrdinary.js"; add "$ROOT/patches/anythingllm/chats-index.js" "$ANYTHINGLLM_ROOT/server/utils/chats/index.js"; add "$ROOT/patches/anythingllm/chat-apiChatHandler.js" "$ANYTHINGLLM_ROOT/server/utils/chats/apiChatHandler.js"; add "$ROOT/patches/anythingllm/context-window-finder.offline.js" "$ANYTHINGLLM_ROOT/server/utils/AiProviders/modelMap/index.js"; add "$ROOT/patches/anythingllm/agents-index.js" "$ANYTHINGLLM_ROOT/server/utils/agents/index.js"; add "$ROOT/patches/anythingllm/agents-ephemeral.js" "$ANYTHINGLLM_ROOT/server/utils/agents/ephemeral.js"; add "$ROOT/patches/anythingllm/toolReranker.js" "$ANYTHINGLLM_ROOT/server/utils/agents/aibitat/utils/toolReranker.js"; add "$ROOT/patches/anythingllm/aagComposerProxy.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagComposerProxy.js"; add "$ROOT/patches/anythingllm/aagImageProgress.js" "$ANYTHINGLLM_ROOT/server/endpoints/aagImageProgress.js"; add "$ROOT/image-system" "$AAG_INSTALL_ROOT/image-system"
 add "$ROOT/patches/anythingllm/frontend/PromptInput-index.jsx" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/index.jsx"
 add "$ROOT/patches/anythingllm/frontend/ChatContainer-index.jsx" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/index.jsx"
 add "$ROOT/image-system/integrations/anythingllm/frontend/ImageGenerationCard/index.jsx" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/ImageGenerationCard/index.jsx"
 add "$ROOT/image-system/integrations/anythingllm/frontend/HistoricalOutputs/index.jsx" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/HistoricalMessage/HistoricalOutputs/index.jsx"
 add "$ROOT/image-system/integrations/anythingllm/frontend/AagImageCollection.jsx" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/AagImageCollection/index.jsx"
 add "$ROOT/image-system/integrations/anythingllm/frontend/aagArtifactExport.js" "$ANYTHINGLLM_ROOT/frontend/src/utils/aagArtifactExport.js"
 add "$ROOT/image-system/integrations/anythingllm/frontend/AagImageComposerPanel" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/AagImageComposerPanel"
 add "$ROOT/image-system/integrations/anythingllm/frontend/AagImageProgress" "$ANYTHINGLLM_ROOT/frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/AagImageProgress"
fi
if [[ $profile == chess || $profile == full ]]; then add "$ROOT/chess" "$AAG_INSTALL_ROOT/chess"; add "$ROOT/chess/integrations/anythingllm/skill/aag-chess-puzzle" "$ANYTHINGLLM_STORAGE/plugins/agent-skills/aag-chess-puzzle"; fi
if [[ $profile == local-llm || $profile == full ]]; then add "$ROOT/integrations/llamacpp" "$AAG_INSTALL_ROOT/llamacpp"; add "$ROOT/integrations/anythingllm/model-neutral-compatibility" "$AAG_INSTALL_ROOT/model-neutral-compatibility"; fi
if [[ $profile == image || $profile == local-llm || $profile == full ]]; then add "$ROOT/visual-atlas" "$AAG_INSTALL_ROOT/visual-atlas"; fi
# The historical Ubuntu Agent capture intentionally remains outside every
# public profile until its machine-specific knowledge/configuration is replaced
# by a separately releasable portable component.
if (( dry )); then for e in "${maps[@]}"; do echo "WOULD_INSTALL ${e%%|*} -> ${e#*|}"; done; echo "WOULD_RENDER_SYSTEMD -> $SYSTEMD_USER_DIR"; echo 'DRY_RUN=PASS'; exit 0; fi
python3 "$ROOT/tools/render-units.py" --install-root "$AAG_INSTALL_ROOT" --storage "$ANYTHINGLLM_STORAGE" --output "$unit_stage" --compat-port "$AAG_COMPATIBILITY_PORT"
if [[ $profile == image || $profile == full ]]; then add "$unit_stage/aag-human-identity-bridge.service" "$SYSTEMD_USER_DIR/aag-human-identity-bridge.service"; add "$unit_stage/aag-human-identity-scene-bridge.service" "$SYSTEMD_USER_DIR/aag-human-identity-scene-bridge.service"; fi
if [[ $profile == chess || $profile == full ]]; then add "$unit_stage/aag-chess-anythingllm-bridge.service" "$SYSTEMD_USER_DIR/aag-chess-anythingllm-bridge.service"; fi
if [[ $profile == local-llm || $profile == full ]]; then add "$unit_stage/aag-model-compatibility.service" "$SYSTEMD_USER_DIR/aag-model-compatibility.service"; fi
mkdir -p "$backup" "$AAG_INSTALL_ROOT" "$AAG_STATE_ROOT" "$AAG_CONFIG_ROOT" "$ANYTHINGLLM_STORAGE/plugins/agent-skills"; : > "$manifest"
rollback(){ "$ROOT/rollback.sh" "$backup" || true; }; trap rollback ERR INT TERM
for e in "${maps[@]}"; do src=${e%%|*}; dst=${e#*|}; mkdir -p "$(dirname "$dst")"; if [[ -e $dst || -L $dst ]]; then rel=${dst#/}; mkdir -p "$backup/files/$(dirname "$rel")"; cp -a "$dst" "$backup/files/$rel"; printf 'RESTORE\t%s\n' "$dst" >> "$manifest"; else printf 'REMOVE\t%s\n' "$dst" >> "$manifest"; fi; stage=${dst}.aag-stage-$stamp; cp -a "$src" "$stage"; mv "$stage" "$dst"; done
if [[ -n ${AAG_ATLAS_SOURCE:-} && ($profile == image || $profile == full) ]]; then "$ROOT/tools/atlas-assets.py" install --source "$AAG_ATLAS_SOURCE" --target "$AAG_ATLAS_ROOT"; fi
[[ -f $AAG_USER_CONFIG ]] || { mkdir -p "$(dirname "$AAG_USER_CONFIG")"; cp "$ROOT/config/defaults.env" "$AAG_USER_CONFIG"; }
cat > "$AAG_STATE_ROOT/install.env" <<EOF
AAG_SUITE_VERSION=1.0.0
AAG_PROFILE=$profile
ANYTHINGLLM_ROOT=$ANYTHINGLLM_ROOT
ANYTHINGLLM_STORAGE=$ANYTHINGLLM_STORAGE
AAG_INSTALL_ROOT=$AAG_INSTALL_ROOT
AAG_BACKUP=$backup
EOF
find "$AAG_INSTALL_ROOT" -type f -print0 | sort -z | xargs -0 -r sha256sum > "$AAG_STATE_ROOT/installed.sha256"; echo "$backup" > "$AAG_STATE_ROOT/last-backup"; trap - ERR INT TERM
if [[ ${AAG_ENABLE_SYSTEMD:-auto} != 0 && $SYSTEMD_USER_DIR == "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user" ]] && command -v systemctl >/dev/null; then systemctl --user daemon-reload || true; fi
"$ROOT/doctor.sh" --installed --profile "$profile" || { rollback; exit 1; }
echo "INSTALL=PASS profile=$profile root=$AAG_INSTALL_ROOT backup=$backup"
