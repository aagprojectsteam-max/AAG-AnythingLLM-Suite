#!/usr/bin/env bash
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); source "$ROOT/tools/config.sh"
mode=--auto; profile=${AAG_PROFILE:-full}
while (($#)); do case "$1" in --package|--installed|--auto) mode=$1; shift;; --profile) profile=$2; shift 2;; *) echo "Unknown argument: $1" >&2; exit 2;; esac; done
[[ $mode == --auto ]] && { [[ -f $AAG_STATE_ROOT/install.env ]] && mode=--installed || mode=--package; }
if [[ $mode == --installed && -f $AAG_STATE_ROOT/install.env ]]; then source "$AAG_STATE_ROOT/install.env"; export ANYTHINGLLM_ROOT ANYTHINGLLM_STORAGE AAG_INSTALL_ROOT; fi
fail=0; line(){ printf '%-24s %s\n' "$1:" "$2"; }; need(){ command -v "$1" >/dev/null && line "$1" PASS || { line "$1" FAIL; fail=1; }; }
echo 'AAG AnythingLLM Suite Doctor'; echo '============================'
os=$(awk -F= '/^ID=/{gsub(/"/,"",$2);print $2}' /etc/os-release 2>/dev/null || echo unknown); arch=$(uname -m); [[ $os == ubuntu ]] && line 'Operating system' "PASS Ubuntu ($arch)" || line 'Operating system' "WARN tested on Ubuntu; found $os ($arch)"
for c in bash find sha256sum python3 node git; do need "$c"; done
if command -v docker >/dev/null 2>&1; then
 if docker info >/dev/null 2>&1; then line Docker PASS; else line Docker 'WARN installed; daemon or user access unavailable'; fi
else
 line Docker 'OPTIONAL/MISSING — required only by Docker-backed image services'
fi
if aag_detect_anythingllm; then aag_resolve_storage; rev=$(git -C "$ANYTHINGLLM_ROOT" rev-parse HEAD 2>/dev/null || echo unknown); [[ $rev == 07bd65f80b3d9ba3031ed7afb8786627326bd134 ]] && line AnythingLLM 'PASS compatible commit' || { line AnythingLLM "FAIL incompatible commit $rev"; fail=1; }; else line AnythingLLM 'FAIL source checkout not detected'; [[ $mode == --installed ]] && fail=1; fi
if [[ $mode == --package ]]; then
 find "$ROOT" -type f -name '*.json' -not -path '*/.git/*' -print0 | xargs -0 -r -n1 python3 -m json.tool >/dev/null 2>&1 && line 'JSON/config' PASS || { line 'JSON/config' FAIL; fail=1; }
 find "$ROOT" -type f -name '*.js' -not -path '*/.git/*' -print0 | xargs -0 -r -n1 node --check >/dev/null 2>&1 && line 'JavaScript' PASS || { line JavaScript FAIL; fail=1; }
 "$ROOT/tools/sanitize.sh" "$ROOT" >/dev/null && line 'Secrets/config' PASS || { line 'Secrets/config' FAIL; fail=1; }
 [[ -d $ROOT/image-system/skills ]] && line 'AAG core' PASS || { line 'AAG core' FAIL; fail=1; }
 line 'Patches' "PASS exact baseline hashes in config/compatibility.json"
else
 source "$AAG_STATE_ROOT/install.env"
 [[ -d $AAG_INSTALL_ROOT ]] && line 'AAG core' PASS || { line 'AAG core' FAIL; fail=1; }
 skills=PASS; for s in aag-image-task aag-image-batch aag-image-job; do [[ $profile != image && $profile != full ]] || [[ -f $ANYTHINGLLM_STORAGE/plugins/agent-skills/$s/plugin.json ]] || skills=FAIL; done; line 'Agent Skills' "$skills"; [[ $skills == PASS ]] || fail=1
 [[ -f $ANYTHINGLLM_ROOT/server/endpoints/aagArtifactExport.js && -f $ANYTHINGLLM_ROOT/server/endpoints/aagPdfAssembler.js ]] && line 'PDF/artifacts' PASS || line 'PDF/artifacts' 'OPTIONAL/NOT INSTALLED'
 [[ -f $AAG_INSTALL_ROOT/chess/src/aag_chess/verifier.py ]] && line Chess PASS || line Chess 'OPTIONAL/NOT INSTALLED'
 [[ -d $AAG_INSTALL_ROOT/image-system/src && -d $AAG_INSTALL_ROOT/image-system/skills ]] && line 'Image System' PASS || line 'Image System' 'OPTIONAL/NOT INSTALLED'
 [[ -f $AAG_INSTALL_ROOT/image-system/integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx ]] && line Composer PASS || line Composer 'OPTIONAL/NOT INSTALLED'
 [[ -f $ANYTHINGLLM_ROOT/server/endpoints/aagImageProgress.js ]] && line 'Progress/status/cancel' PASS || line 'Progress/status/cancel' 'OPTIONAL/NOT INSTALLED'
 unit_dir=${SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}; unit_count=0
 for unit in aag-chess-anythingllm-bridge.service aag-model-compatibility.service aag-human-identity-bridge.service aag-human-identity-scene-bridge.service; do [[ -f $unit_dir/$unit ]] && ((unit_count+=1)); done
 if [[ $unit_count -gt 0 ]]; then line 'systemd user units' "PASS installed $unit_count/4 (profile-dependent)"; else line 'systemd user units' 'OPTIONAL/NOT INSTALLED'; fi
 if [[ -d $AAG_ATLAS_ROOT/images && -d $AAG_ATLAS_ROOT/thumbs ]]; then "$ROOT/tools/atlas-assets.py" verify --source "$AAG_ATLAS_ROOT" >/dev/null && line 'Visual Atlas' 'PASS pixels+metadata' || line 'Visual Atlas' FAIL; elif [[ -f $AAG_INSTALL_ROOT/visual-atlas/manifest/atlas-manifest.json ]]; then line 'Visual Atlas' 'PASS metadata-only; pixels optional'; else line 'Visual Atlas' 'OPTIONAL/MISSING — metadata unavailable'; fi
 line 'Ubuntu Agent' 'OPTIONAL/NOT INSTALLED — historical private capture excluded from public profiles'
 [[ -n ${COMFYUI_ROOT:-} && -f $COMFYUI_ROOT/main.py ]] && line ComfyUI PASS || line ComfyUI 'NOT INSTALLED/CONFIGURED'
 [[ -n ${LLAMACPP_ROOT:-} && -x $LLAMACPP_ROOT/build/bin/llama-server ]] && line 'Local LLM' PASS || line 'Local LLM' OPTIONAL
 [[ -n ${STOCKFISH_BIN:-} && -x $STOCKFISH_BIN ]] || command -v stockfish >/dev/null && line Stockfish PASS || line Stockfish 'OPTIONAL/MISSING'
 [[ -f $AAG_STATE_ROOT/installed.sha256 ]] && (cd / && sha256sum -c "$AAG_STATE_ROOT/installed.sha256" >/dev/null 2>&1) && line 'Canonical hashes' PASS || line 'Canonical hashes' 'WARN check installed manifest'
 patch_status=PASS
 patch_pairs=("patches/anythingllm/server-index.js|server/index.js")
 if [[ $profile == image || $profile == full ]]; then patch_pairs+=("patches/anythingllm/chats-index.js|server/utils/chats/index.js" "patches/anythingllm/chat-apiChatHandler.js|server/utils/chats/apiChatHandler.js" "patches/anythingllm/frontend/PromptInput-index.jsx|frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/index.jsx" "patches/anythingllm/frontend/ChatContainer-index.jsx|frontend/src/components/WorkspaceChat/ChatContainer/index.jsx"); fi
 for pair in "${patch_pairs[@]}"; do
  src=${pair%%|*}; dst=${pair#*|}; [[ -f $ANYTHINGLLM_ROOT/$dst ]] || continue
  cmp -s "$ROOT/$src" "$ANYTHINGLLM_ROOT/$dst" || patch_status=FAIL
 done
 line Patches "$patch_status"; [[ $patch_status == PASS ]] || fail=1
 [[ -f $AAG_USER_CONFIG && -f $AAG_STATE_ROOT/install.env ]] && line 'Secrets/config' PASS || { line 'Secrets/config' FAIL; fail=1; }
fi
"$ROOT/tools/model-check.py" --models "$AAG_MODEL_ROOT" --comfyui "${COMFYUI_ROOT:-}" --llamacpp "${LLAMACPP_ROOT:-}" 2>/dev/null || true
echo; "$ROOT/tools/hardware-detect.sh" "$ROOT"
echo; (( fail == 0 )) && { echo 'OVERALL=PASS'; exit 0; }; echo 'OVERALL=FAIL — follow the remediation above'; exit 1
