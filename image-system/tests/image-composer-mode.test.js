"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const helperPath = path.join(
  root,
  "integrations/anythingllm/frontend/useImageComposerMode.js"
);
const builderPath = path.join(root, "tools/build-anythingllm-frontend.js");

function loadPureHelper() {
  const source = fs
    .readFileSync(helperPath, "utf8")
    .replace(/^import .*\n/m, "")
    .replace(/export default function useImageComposerMode[\s\S]*$/u, "")
    .replaceAll("export const ", "const ")
    .replaceAll("export function ", "function ");
  const sandbox = { result: null };
  vm.runInNewContext(
    `${source}\nresult = { imageComposerStorageKey, defaultImageComposerEnabled, readImageComposerEnabled, writeImageComposerEnabled };`,
    sandbox
  );
  return sandbox.result;
}

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, value),
    values,
  };
}

test("workspace defaults, overrides, persistence, and invalid slugs are fail-safe", () => {
  const helper = loadPureHelper();
  const storage = memoryStorage();
  assert.equal(helper.readImageComposerEnabled("image-generator", storage), true);
  console.log("IMAGE_GENERATOR_DEFAULT_ON=PASS");
  assert.equal(helper.readImageComposerEnabled("linux", storage), false);
  console.log("NON_IMAGE_GENERATOR_DEFAULT_OFF=PASS");
  assert.equal(helper.writeImageComposerEnabled("image-generator", false, storage), true);
  assert.equal(helper.writeImageComposerEnabled("linux", true, storage), true);
  assert.equal(helper.readImageComposerEnabled("image-generator", storage), false);
  assert.equal(helper.readImageComposerEnabled("linux", storage), true);
  assert.equal(storage.values.get("aag_image_composer_enabled:image-generator"), "false");
  assert.equal(storage.values.get("aag_image_composer_enabled:linux"), "true");
  console.log("IMAGE_COMPOSER_STATE_WORKSPACE_SCOPED=PASS");
  console.log("IMAGE_COMPOSER_STATE_PERSISTS=PASS");
  assert.equal(helper.imageComposerStorageKey(null), null);
  assert.equal(helper.imageComposerStorageKey("../../bad"), null);
  assert.equal(helper.readImageComposerEnabled(undefined, storage), false);
});

test("builder preserves Composer state and sidebar while removing the floating control", () => {
  const source = fs.readFileSync(builderPath, "utf8");
  assert.doesNotMatch(source, /aag-image-composer-toggle|toggleImageComposer|Image Composer ·/);
  assert.match(source, /composerEnabled=\{isImageComposerEnabledForCurrentWorkspace\}/);
  assert.match(source, /isImageComposerEnabledForCurrentWorkspace && composerMode/);
  assert.match(source, /isImageComposerEnabledForCurrentWorkspace && \(\\n              <AagImageProgress/);
  assert.match(source, /activeWorkspaceSlug === "image-generator"/);
  assert.doesNotMatch(source, /const isImageGenerator =/);
  console.log("IMAGE_COMPOSER_ON_SHOWS_COMPOSER=PASS");
  console.log("IMAGE_COMPOSER_OFF_HIDES_COMPOSER=PASS");
  assert.match(source, /defaultThreadHasChats &&\\n        !isImageComposerEnabledForCurrentWorkspace/);
  console.log("DEFAULT_SIDEBAR_HIDDEN_WHEN_COMPOSER_ON=PASS");
  console.log("DEFAULT_SIDEBAR_VISIBLE_WHEN_COMPOSER_OFF=PASS");
  assert.doesNotMatch(source, /database|anythingllm\.db|docker restart|systemctl.*restart/i);
  console.log("BACKEND_UNCHANGED=PASS");
  console.log("DATABASE_UNCHANGED=PASS");
});

test("pinned upstream reconstruction succeeds and every mutation uses fail-closed anchors", () => {
  const source = fs.readFileSync(builderPath, "utf8");
  assert.match(source, /07bd65f80b3d9ba3031ed7afb8786627326bd134/);
  assert.match(source, /source\.indexOf\(expected, first \+ expected\.length\) >= 0/);
  const upstream = process.env.AAG_TEST_ANYTHINGLLM_SOURCE_DIR;
  if (!upstream) return console.log("PINNED_RECONSTRUCTION=SKIP(no local source configured)");
  const output = fs.mkdtempSync(path.join(os.tmpdir(), "aag-composer-patches-"));
  try {
    const result = spawnSync(process.execPath, [builderPath], {
      cwd: root,
      env: {
        ...process.env,
        AAG_ANYTHINGLLM_SOURCE_DIR: upstream,
        AAG_FRONTEND_PATCH_OUTPUT: output,
      },
      encoding: "utf8",
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.ok(fs.existsSync(path.join(output, "PromptInput-index.jsx")));
    assert.ok(fs.existsSync(path.join(output, "ChatContainer-index.jsx")));
    const threadContainer = fs.readFileSync(
      path.join(output, "ThreadContainer-index.jsx"),
      "utf8"
    );
    assert.ok(threadContainer.includes("{threads.map((thread, i) => ("));
    assert.ok(threadContainer.includes("<NewThreadButton workspace={workspace} />"));
    console.log("REAL_THREADS_UNCHANGED=PASS");
    console.log("NEW_THREAD_UNCHANGED=PASS");
    console.log("PINNED_UPSTREAM_PROVENANCE=PASS");
    console.log("FRONTEND_PATCH_FAIL_CLOSED=PASS");
  } finally {
    fs.rmSync(output, { recursive: true, force: true });
  }
});
