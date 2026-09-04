const fs = require("fs");
const http = require("http");

const CONTRACT_FILE =
  "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";

const PROFILES = new Set([
  "general_system",
  "performance",
  "service",
  "application_start",
  "network",
  "storage_mount",
  "docker",
  "package",
  "boot_health",
]);

function classifiedError(error) {
  const code = error && error.code;
  if (code === "ENOENT") return "bridge_endpoint_missing";
  if (code === "ECONNREFUSED") return "bridge_socket_refused";
  if (code === "EACCES" || code === "EPERM") return "bridge_permission_denied";
  if (code === "ETIMEDOUT" || code === "ESOCKETTIMEDOUT") return "bridge_timeout";
  return "bridge_unavailable";
}

function readContract() {
  const contract = JSON.parse(fs.readFileSync(CONTRACT_FILE, "utf8"));
  if (
    contract.schema !== "aag-bridge-endpoint-v1" ||
    contract.api_version !== 2 ||
    contract.container_contract_file !== CONTRACT_FILE ||
    typeof contract.container_socket !== "string" ||
    contract.diagnose_path !== "/diagnose"
  ) {
    const error = new Error("Bridge endpoint contract is incompatible");
    error.code = "AAG_CONTRACT_MISMATCH";
    throw error;
  }
  return contract;
}

function inputsFor(profile, args) {
  const allowed = {
    service: ["service", "manager"],
    application_start: ["service", "manager", "pid"],
    network: ["interface"],
    storage_mount: ["path"],
    docker: ["container"],
    package: ["package"],
  }[profile] || [];
  return Object.fromEntries(
    allowed
      .filter((name) => args[name] !== undefined && args[name] !== null && args[name] !== "")
      .map((name) => [name, name === "pid" ? Number(args[name]) : args[name]])
  );
}

module.exports.runtime = {
  handler: async function (args) {
    const primary = args.profile;
    const secondary = args.secondary_profile || null;
    if (!PROFILES.has(primary) || (secondary && !PROFILES.has(secondary)) || secondary === primary) {
      return JSON.stringify({
        schema: "aag-deployed-diagnostic-error-v1",
        status: "unsupported",
        error: "unsupported_profile_selection",
        read_only: true,
        mutated: false,
      });
    }
    const requests = [
      { profile: primary, inputs: inputsFor(primary, args) },
    ];
    if (secondary) requests.push({ profile: secondary, inputs: {} });
    try {
      const contract = readContract();
      this.introspect(
        `Running bounded read-only Ubuntu diagnosis: ${requests.map((item) => item.profile).join(" + ")}`
      );
      const payload = JSON.stringify({ requests });
      const body = await new Promise((resolve, reject) => {
        const request = http.request(
          {
            socketPath: contract.container_socket,
            path: contract.diagnose_path,
            method: "POST",
            timeout: 35000,
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
            },
          },
          (response) => {
            let data = "";
            response.setEncoding("utf8");
            response.on("data", (chunk) => {
              data += chunk;
              if (Buffer.byteLength(data) > 256000) {
                request.destroy(new Error("Bridge response exceeded limit"));
              }
            });
            response.on("end", () => {
              if (response.statusCode < 200 || response.statusCode >= 300) {
                const error = new Error(`Bridge HTTP ${response.statusCode}`);
                error.code = "AAG_BRIDGE_HTTP_ERROR";
                reject(error);
                return;
              }
              resolve(data);
            });
          }
        );
        request.on("timeout", () => {
          const error = new Error("Bridge diagnosis timed out");
          error.code = "ESOCKETTIMEDOUT";
          request.destroy(error);
        });
        request.on("error", reject);
        request.end(payload);
      });
      const result = JSON.parse(body);
      if (result.schema !== "aag-diagnostic-session-v1") {
        throw new Error("Unexpected diagnostic response schema");
      }
      return body;
    } catch (error) {
      const reason = error.code === "AAG_CONTRACT_MISMATCH" || error instanceof SyntaxError
        ? "integration_misconfigured"
        : classifiedError(error);
      this.logger("AAG Ubuntu diagnosis failed:", reason, error.message);
      this.introspect(`Live Ubuntu diagnosis unavailable: ${reason}`);
      return JSON.stringify({
        schema: "aag-deployed-diagnostic-error-v1",
        status: "unavailable",
        error: reason,
        detail: error.message,
        manual_commands_suggested: false,
        read_only: true,
        mutated: false,
      });
    }
  },
};
