#!/usr/bin/env node
"use strict";

// The single acceptance path for shared compatibility and deployed skill edits.
// It derives hashes from authoritative sources, stages deployment, verifies the
// complete gate, and restores the whole transaction on any failure.
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const version = fs.readFileSync(path.join(root, "VERSION"), "utf8").trim();
const stagedManifest = path.join(root, "releases/staged", version, "STAGED-MANIFEST.json");
const releaseManifest = path.join(root, "releases", version, "STAGED-MANIFEST.json");
const releaseSums = path.join(root, "releases", version, "FILE-SHA256SUMS");
const lock = path.join(root, ".governed-update.lock");
const evidenceRoot = path.resolve(process.env.AAG_GOVERNED_EVIDENCE || path.join(root, "../evaluation/governed-updates"));
const transaction = path.join(evidenceRoot, new Date().toISOString().replace(/[:.]/g, ""));
const backup = path.join(transaction, "backup");
const deployedHandler = "/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-image-task/handler.js";
const deployedLauncher = "/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/bin/aag-image-start";
const sourceHandler = path.join(root, "skills/aag-image-task/handler.js");
const sourceLauncher = path.join(root, "integrations/launchers/aag-image-start");
const sourceProxy = path.join(root, "integrations/anythingllm/aagComposerProxy.js");
const deployedProxy = "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagComposerProxy.js";
const sourceRelayUnit = path.resolve(root, "../systemd/user/aag-composer-loopback-relay.service");
const deployedRelayUnit = "/home/aag-linux/.config/systemd/user/aag-composer-loopback-relay.service";

function sha(file) { return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"); }
function run(command, args, output) {
  const value = execFileSync(command, args, { cwd: root, encoding: "utf8", stdio: output ? ["ignore", "pipe", "pipe"] : "inherit" });
  if (output) fs.writeFileSync(path.join(transaction, output), value);
}
function atomicInstall(source, target, mode) {
  const temporary = `${target}.aag-governed-${process.pid}`;
  fs.copyFileSync(source, temporary);
  fs.chmodSync(temporary, mode);
  fs.renameSync(temporary, target);
}
function sealRelease() {
  const releaseRoot = path.dirname(releaseManifest);
  const files = [];
  function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(file);
      else if (entry.isFile() && file !== releaseSums) files.push(file);
    }
  }
  visit(releaseRoot);
  const base = path.resolve(root, "..");
  fs.writeFileSync(releaseSums, files.sort().map(file => `${sha(file)}  ${path.relative(base, file)}`).join("\n") + "\n", { mode: 0o600 });
}

if (!process.argv.includes("--accept")) {
  console.error("Usage: node tools/governed-update.js --accept");
  process.exit(2);
}
fs.writeFileSync(lock, String(process.pid), { flag: "wx", mode: 0o600 });
fs.mkdirSync(backup, { recursive: true, mode: 0o700 });
const restore = [
  [deployedHandler, path.join(backup, "deployed-handler.js"), 0o600],
  [deployedLauncher, path.join(backup, "deployed-launcher"), 0o755],
  [stagedManifest, path.join(backup, "staged-manifest.json"), 0o600],
  [releaseManifest, path.join(backup, "release-manifest.json"), 0o600],
  [deployedProxy, path.join(backup, "deployed-composer-proxy.js"), 0o755],
  [deployedRelayUnit, path.join(backup, "deployed-relay.service"), 0o644],
  [releaseSums, path.join(backup, "release-sha256.txt"), 0o600],
];
let accepted = false;
try {
  for (const [source, destination] of restore) fs.copyFileSync(source, destination);
  fs.writeFileSync(path.join(transaction, "before.json"), JSON.stringify(Object.fromEntries(restore.map(([file]) => [file, sha(file)])), null, 2) + "\n");
  run("npm", ["test"], "tests.log");
  run("python3", ["-m", "unittest", "discover", "-s", "integrations/model-neutral-compatibility", "-p", "test_*.py", "-q"], "compatibility-tests.log");
  run("node", ["tools/build.js"], "build.log");
  atomicInstall(path.join(root, "releases/staged", version, "aag-image-task/handler.js"), deployedHandler, 0o600);
  atomicInstall(sourceLauncher, deployedLauncher, 0o755);
  atomicInstall(sourceProxy, deployedProxy, 0o755);
  atomicInstall(sourceRelayUnit, deployedRelayUnit, 0o644);
  atomicInstall(stagedManifest, releaseManifest, 0o600);
  sealRelease();
  run("systemctl", ["--user", "daemon-reload"]);
  run("systemctl", ["--user", "restart", "aag-composer-loopback-relay.service"]);
  run("node", ["tools/doctor.js", "--deployed"], "doctor.log");
  const manifest = JSON.parse(fs.readFileSync(stagedManifest));
  const row = manifest.provider_files.find(item => item.source === "skills/aag-image-task/handler.js");
  if (!row || row.sha256 !== sha(sourceHandler) || row.sha256 !== sha(deployedHandler)) throw new Error("handler source/deployed/manifest drift after acceptance");
  fs.writeFileSync(path.join(transaction, "ACCEPTED.json"), JSON.stringify({ schema: "aag.governed-update.v1", version, source_owner: root, source_handler_sha256: sha(sourceHandler), deployed_handler_sha256: sha(deployedHandler), server_sha256: sha(path.join(root, "integrations/model-neutral-compatibility/server.py")), manifest_sha256: sha(stagedManifest) }, null, 2) + "\n");
  accepted = true;
  console.log(transaction);
} finally {
  if (!accepted) for (const [, source, mode] of restore) atomicInstall(source, restore.find(item => item[1] === source)[0], mode);
  fs.rmSync(lock, { force: true });
}
