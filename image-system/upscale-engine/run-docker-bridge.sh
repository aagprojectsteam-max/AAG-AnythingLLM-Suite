#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${AAG_BRIDGE_CONTAINER:-anythingllm}"
PORT="${AAG_BRIDGE_PORT:?AAG_BRIDGE_PORT is required}"

GATEWAY=""

for ((I=1; I<=120; I++)); do
    GATEWAY="$(
        docker inspect \
          -f '{{range .NetworkSettings.Networks}}{{println .Gateway}}{{end}}' \
          "$CONTAINER" 2>/dev/null |
        awk 'NF {print; exit}'
    )"

    if [[
        "$GATEWAY" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$
    ]] &&
       ip -4 -o addr show |
       grep -qw "$GATEWAY"
    then
        break
    fi

    GATEWAY=""
    sleep 2
done

[[ -n "$GATEWAY" ]] || {
    echo "ERROR: Docker gateway not found"
    exit 1
}

exec /usr/bin/python3 \
  /mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine/service/docker-bridge.py \
  --listen-host "$GATEWAY" \
  --listen-port "$PORT" \
  --target-host 127.0.0.1 \
  --target-port "$PORT"
