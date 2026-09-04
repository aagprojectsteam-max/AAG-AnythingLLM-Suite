#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "../..");
const metadataPath = path.resolve(projectRoot, "image-agent/routing/tool-routing-metadata.json");
const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8")).tools;
const liveRoot = "/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills";
const ubuntuRoot = "/mnt/data/AI/Agents/AAG-Ubuntu-Agent/integrations/anythingllm";
const chessRoot = "/mnt/data/AI/Agents/AAG-Chess-Puzzle-Agent/integrations/anythingllm/skill";

const targets = [
  ...["aag-image-task", "aag-image-batch", "aag-image-job"].flatMap((hubId) => [
    path.resolve(projectRoot, `image-agent/skills/${hubId}/plugin.json`),
    path.resolve(projectRoot, `image-agent/releases/staged/0.9.0-preview.12/${hubId}/plugin.json`),
    path.resolve(projectRoot, `image-agent/releases/0.9.0-preview.12/providers/${hubId}/plugin.json`),
    path.resolve(liveRoot, `${hubId}/plugin.json`),
  ]),
  path.resolve(chessRoot, "aag-chess-puzzle/plugin.json"),
  path.resolve(liveRoot, "aag-chess-puzzle/plugin.json"),
  path.resolve(ubuntuRoot, "aag-context-memory-v1/plugin.json"),
  path.resolve(liveRoot, "aag-context-memory-v1/plugin.json"),
  path.resolve(ubuntuRoot, "aag-governed-orchestration-v1/plugin.json"),
  path.resolve(liveRoot, "aag-governed-orchestration-v1/plugin.json"),
  path.resolve(ubuntuRoot, "aag-maintenance-intelligence/plugin.json"),
  path.resolve(liveRoot, "aag-maintenance-intelligence-v1/plugin.json"),
  path.resolve(ubuntuRoot, "aag-ubuntu-diagnostics/plugin.json"),
  path.resolve(liveRoot, "aag-ubuntu-live-audit/plugin.json"),
];

const changed = [];
for (const target of targets) {
  const stat = fs.statSync(target);
  const plugin = JSON.parse(fs.readFileSync(target, "utf8"));
  const routing = metadata[plugin.hubId];
  if (!routing) throw new Error(`No routing metadata for ${plugin.hubId} (${target})`);
  plugin.routing = routing;
  fs.writeFileSync(target, `${JSON.stringify(plugin, null, 2)}\n`, {
    mode: stat.mode & 0o777,
  });
  fs.chmodSync(target, stat.mode & 0o777);
  changed.push({ target, hubId: plugin.hubId, mode: (stat.mode & 0o777).toString(8) });
}

console.log(JSON.stringify({ changedCount: changed.length, changed }, null, 2));
