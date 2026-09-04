#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C LANG=C

CONTAINER="${1:-anythingllm}"

echo "============================================================"
echo " AAG — Refresh AnythingLLM / ComfyUI Docker network"
echo "============================================================"

docker inspect "$CONTAINER" >/dev/null 2>&1 || {
    echo "ERROR: container not found: $CONTAINER"
    exit 1
}

STORAGE="$(
docker inspect "$CONTAINER" \
  --format '{{range .Mounts}}{{if eq .Destination "/app/server/storage"}}{{println .Source}}{{end}}{{end}}' |
head -1
)"

[[ -n "$STORAGE" ]] || {
    echo "ERROR: Could not determine AnythingLLM storage."
    exit 1
}

GATEWAY="$(
docker inspect "$CONTAINER" \
  --format '{{range $name,$network := .NetworkSettings.Networks}}{{println $network.Gateway}}{{end}}' |
head -1
)"

[[ -n "$GATEWAY" ]] || {
    echo "ERROR: Could not determine Docker gateway."
    exit 1
}

SCHEME="http"
BASE="${SCHEME}://${GATEWAY}:18188"

PLUGIN="$STORAGE/plugins/agent-skills/aag-comfyui-image-generator/plugin.json"

[[ -f "$PLUGIN" ]] || {
    echo "ERROR: Image Skill missing: $PLUGIN"
    exit 1
}

python3 - "$PLUGIN" "$BASE" <<'PY'
import json
import sys
from pathlib import Path

path=Path(sys.argv[1])
url=sys.argv[2]

with path.open(encoding="utf-8") as f:
    d=json.load(f)

item=d["setup_args"]["COMFYUI_BASE_URL"]

item["value"]=url
item.setdefault("input",{})["default"]=url
item["input"]["placeholder"]=url

with path.open("w",encoding="utf-8") as f:
    json.dump(
        d,
        f,
        indent=2,
        ensure_ascii=False
    )
    f.write("\n")
PY

python3 -m json.tool "$PLUGIN" >/dev/null

echo "Storage=$STORAGE"
echo "DockerGateway=$GATEWAY"
echo "ComfyUIBase=$BASE"

systemctl --user restart \
    aag-comfyui-docker-bridge.service

sleep 2

systemctl --user is-active --quiet \
    aag-comfyui-docker-bridge.service || {
        echo "ERROR: bridge failed."
        exit 1
    }

# IMPORTANT:
# Never restart AnythingLLM just because ComfyUI or its bridge is unavailable.
# AnythingLLM must remain independent and continue serving normal chat.
#
# The plugin configuration is stored on the mounted persistent storage and
# will be used by subsequent agent executions without killing the container.

echo "AnythingLLMRestart=SKIPPED"
echo "NetworkRefresh=PASS"
