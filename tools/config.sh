#!/usr/bin/env bash
set -euo pipefail
AAG_PACKAGE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
set -a
# shellcheck disable=SC1091
source "$AAG_PACKAGE_ROOT/config/defaults.env"
AAG_USER_CONFIG=${AAG_CONFIG_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/aag-anythingllm-suite/config.env}
if [[ -f "$AAG_USER_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$AAG_USER_CONFIG"
fi
set +a
export AAG_PACKAGE_ROOT AAG_USER_CONFIG

aag_detect_anythingllm() {
  [[ -n ${ANYTHINGLLM_ROOT:-} && -d $ANYTHINGLLM_ROOT/server ]] && return 0
  local candidate
  for candidate in "$PWD" "$HOME/anything-llm" "$HOME/docker/anythingllm/anything-llm" /opt/anything-llm; do
    if [[ -d "$candidate/server" && -d "$candidate/frontend" ]]; then ANYTHINGLLM_ROOT=$candidate; export ANYTHINGLLM_ROOT; return 0; fi
  done
  return 1
}

aag_resolve_storage() {
  [[ -n ${ANYTHINGLLM_STORAGE:-} ]] && return 0
  if [[ -d ${ANYTHINGLLM_ROOT:-}/storage ]]; then ANYTHINGLLM_STORAGE=$ANYTHINGLLM_ROOT/storage
  elif [[ -d "$HOME/docker/anythingllm/storage" ]]; then ANYTHINGLLM_STORAGE=$HOME/docker/anythingllm/storage
  else ANYTHINGLLM_STORAGE=${XDG_DATA_HOME:-$HOME/.local/share}/anythingllm/storage
  fi
  export ANYTHINGLLM_STORAGE
}

