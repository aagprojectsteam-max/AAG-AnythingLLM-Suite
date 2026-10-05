#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");
const atlas = require("../src/visual-atlas");

const project = path.resolve(__dirname, "../..");
const backupArg = process.argv.find(value => value.startsWith("--backup="));
const outputArg = process.argv.find(value => value.startsWith("--output="));
if (!backupArg || !outputArg) throw new Error("Use --backup=<pre-state directory> --output=<evidence JSON>.");
const backup = path.resolve(backupArg.slice("--backup=".length));
const output = path.resolve(outputArg.slice("--output=".length));
const tokenizer = "/mnt/data/AI/Apps/llama.cpp/build-sycl/bin/llama-tokenize";
const model = "/mnt/data/AI/Models/lmstudio-community/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf";
const currentCompatibility = path.join(project, "image-agent/integrations/model-neutral-compatibility");
const previousCompatibility = path.join(backup, "files/project");

function sha(value) { return crypto.createHash("sha256").update(value).digest("hex"); }

function composerMessage(moduleDir, payload) {
  const program = [
    "import json,sys",
    `sys.path.insert(0, ${JSON.stringify(moduleDir)})`,
    "from compatibility import compose_request",
    `message,_ = compose_request(json.loads(${JSON.stringify(JSON.stringify(payload))}))`,
    "sys.stdout.write(message)",
  ].join("\n");
  const result = spawnSync("python3", ["-c", program], { encoding: "utf8", maxBuffer: 4 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(result.stderr || "Composer measurement failed.");
  return result.stdout;
}

function tokens(content) {
  const result = spawnSync(tokenizer, ["-m", model, "--stdin", "--show-count", "--offline", "--no-bos", "-c", "8192"], {
    input: content,
    encoding: "utf8",
    maxBuffer: 8 * 1024 * 1024,
  });
  if (result.status !== 0) throw new Error(result.stderr || "Tokenizer failed.");
  const match = `${result.stdout}\n${result.stderr}`.match(/Total number of tokens:\s*(\d+)/);
  if (!match) throw new Error("Tokenizer count is missing.");
  return Number(match[1]);
}

function measured(before, after) {
  const beforeTokens = tokens(before);
  const afterTokens = tokens(after);
  return {
    before_characters: before.length,
    after_characters: after.length,
    additional_characters: after.length - before.length,
    before_tokens: beforeTokens,
    after_tokens: afterTokens,
    additional_tokens: afterTokens - beforeTokens,
    before_sha256: sha(before),
    after_sha256: sha(after),
  };
}

const manualPayload = {
  mode: "advanced",
  free_text: "Jerusalem street at sunset, very soft pastel colors",
  operation: "generate",
  visual_family: "fine-art-traditional-media",
  visual_subfamily: "watercolor",
};
const plainPayload = {
  mode: "advanced",
  free_text: "A stone house on a quiet Jerusalem street",
  operation: "generate",
};
const manualBefore = composerMessage(previousCompatibility, manualPayload);
const manualAfter = composerMessage(currentCompatibility, { ...manualPayload, atlas_selection_mode: "manual_browse" });
const plainBefore = composerMessage(previousCompatibility, plainPayload);
const plainAfter = composerMessage(currentCompatibility, plainPayload);

const imagePromptBefore = "A Jerusalem street at sunset with soft pastel colors, balanced composition, coherent architecture, atmospheric depth, scene-appropriate lighting, controlled shadows, refined surface detail, and a polished professional finish.";
const task = {
  operation: "generate",
  request: manualPayload.free_text,
  prompt: imagePromptBefore,
  preservation: "none",
  _aag_authoritative_request: manualPayload.free_text,
  _aag_upstream_request: atlas.marker("manual_browse", "fine-art-traditional-media", "watercolor"),
  _aag_prompt_contract: { final_prompt_sha256: sha(imagePromptBefore) },
};
atlas.applyToTask(task);
const topTwoTask = {
  operation: "generate",
  request: "a cinematic realistic photograph",
  prompt: imagePromptBefore,
  preservation: "none",
  _aag_authoritative_request: "a cinematic realistic photograph",
  _aag_upstream_request: "",
};
atlas.applyToTask(topTwoTask);

const result = {
  schema: "aag.visual-atlas.token-accounting.v1",
  captured_at: new Date().toISOString(),
  release: fs.readFileSync(path.join(project, "image-agent/VERSION"), "utf8").trim(),
  method: "Exact Qwen3.5-4B-Q4_K_M llama-tokenize counts over complete before/after Composer messages and complete before/after image prompts. Static V5.2 workspace prompt and public tool schemas are unchanged.",
  tokenizer_model: model,
  established_v52_full_model_input_baseline_tokens: 15998,
  established_v52_workspace_prompt_tokens: 11397,
  established_v52_tool_schema_tokens: 4364,
  manual_advanced_composer_message: measured(manualBefore, manualAfter),
  no_style_advanced_composer_message: measured(plainBefore, plainAfter),
  manual_runtime_image_prompt: measured(imagePromptBefore, task.prompt),
  maximum_top_k_runtime_image_prompt: measured(imagePromptBefore, topTwoTask.prompt),
  runtime_top_k_limit: 2,
  runtime_context_char_limit: atlas.MAX_CONTEXT_CHARS,
  full_atlas_injected: false,
  additional_llm_call: false,
};
fs.mkdirSync(path.dirname(output), { recursive: true, mode: 0o700 });
fs.writeFileSync(output, JSON.stringify(result, null, 2) + "\n", { mode: 0o600 });
console.log(JSON.stringify(result, null, 2));

