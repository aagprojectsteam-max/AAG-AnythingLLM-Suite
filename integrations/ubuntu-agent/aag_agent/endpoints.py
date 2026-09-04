"""Single source of truth for the AAG Bridge transport contract."""

from pathlib import Path

BRIDGE_API_VERSION = 2
BRIDGE_SOCKET_HOST = Path(
    "/mnt/data/AI/Apps/AnythingLLM/storage/aag-ubuntu-agent/host-bridge.sock"
)
BRIDGE_SOCKET_CONTAINER = Path(
    "/app/server/storage/aag-ubuntu-agent/host-bridge.sock"
)
BRIDGE_CONTRACT_FILE_HOST = BRIDGE_SOCKET_HOST.with_name("bridge-endpoint.json")
BRIDGE_CONTRACT_FILE_CONTAINER = BRIDGE_SOCKET_CONTAINER.with_name("bridge-endpoint.json")
BRIDGE_HEALTH_PATH = "/health"
BRIDGE_DIAGNOSE_PATH = "/diagnose"
BRIDGE_MAINTENANCE_PATH = "/maintenance"
BRIDGE_CONTEXT_PATH = "/context"
BRIDGE_ORCHESTRATION_PATH = "/orchestrate"
BRIDGE_SERVICE = "aag-ubuntu-agent-bridge.service"


def public_contract() -> dict[str, object]:
    return {
        "schema": "aag-bridge-endpoint-v1",
        "api_version": BRIDGE_API_VERSION,
        "service": BRIDGE_SERVICE,
        "host_socket": str(BRIDGE_SOCKET_HOST),
        "container_socket": str(BRIDGE_SOCKET_CONTAINER),
        "container_contract_file": str(BRIDGE_CONTRACT_FILE_CONTAINER),
        "health_path": BRIDGE_HEALTH_PATH,
        "diagnose_path": BRIDGE_DIAGNOSE_PATH,
        "maintenance_path": BRIDGE_MAINTENANCE_PATH,
        "context_path": BRIDGE_CONTEXT_PATH,
        "orchestration_path": BRIDGE_ORCHESTRATION_PATH,
    }
