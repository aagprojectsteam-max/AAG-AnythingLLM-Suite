"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const adapters = require("../src/adapters");
const image = require("../src/image");
const { ensureDirectory, atomicJson, readJson, stateRoot } = require("../src/util");

function temporary() { return fs.mkdtempSync(path.join(os.tmpdir(), "aag-security-")); }
function png() { const value = Buffer.alloc(160); Buffer.from([137,80,78,71,13,10,26,10]).copy(value); value.writeUInt32BE(8,16); value.writeUInt32BE(8,20); return value; }

test("publisher policy permits only exact loopback hub URL and safe filename", () => {
  assert.deepEqual(adapters.extractCanonicalFilenames("ok http://127.0.0.1:18190/files/a.png"), ["a.png"]);
  for (const value of [
    "http://evil.example/files/a.png", "http://192.168.1.2:18190/files/a.png", "http://127.0.0.1:22/files/a.png",
    "http://127.0.0.1:18190/admin/a.png", "http://127.0.0.1:18190/files/../secret.png",
    "http://127.0.0.1:18190/files/a.png?token=secret", "file:///mnt/data/x.png",
    "http://user:pass@127.0.0.1:18190/files/a.png", "http://127.0.0.1:18190/files/a%2Fb.png",
    "http://127.0.0.1:18190/files/%E0%A4%A.png",
  ]) assert.throws(() => adapters.extractCanonicalFilenames(`IMAGE ${value}`), error => error.code === "OUTPUT_POLICY_VIOLATION");
});

test("artifact verifier rejects arbitrary network target before fetch", async () => {
  let fetched = false;
  assert.throws(() => adapters.extractCanonicalFilenames("http://10.0.0.9:18190/files/x.png"), error => error.code === "OUTPUT_POLICY_VIOLATION");
  assert.equal(fetched, false);
});

test("state directory and atomic writer reject symlink escape", () => {
  const base = temporary(), outside = temporary();
  fs.symlinkSync(outside, path.join(base, "state"));
  assert.throws(() => ensureDirectory(path.join(base, "state", "jobs")), error => error.code === "OUTPUT_POLICY_VIOLATION");
});

test("atomic JSON has private permissions and leaves no unsafe temporary files", () => {
  const base = temporary(), file = path.join(base, "state", "record.json");
  atomicJson(file, { ok: true });
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ["record.json"]);
});

test("state JSON reads reject a symlink file and state root rejects filesystem root", () => {
  const base = temporary(), outside = path.join(temporary(), "outside.json");
  fs.writeFileSync(outside, JSON.stringify({ secret: true }));
  const linked = path.join(base, "linked.json");
  fs.symlinkSync(outside, linked);
  assert.throws(() => readJson(linked), error => error.code === "OUTPUT_POLICY_VIOLATION");
  assert.throws(() => stateRoot({ AAG_IMAGE_AGENT_STATE_ROOT: path.parse(base).root }), error => error.code === "OUTPUT_POLICY_VIOLATION");
});

test("image parser rejects unsupported/deceptive formats and bounded oversized headers", () => {
  const buffer = png();
  assert.throws(() => image.parseAttachment({ name: "payload.svg", mime: "image/png", contentString: `data:image/png;base64,${buffer.toString("base64")}` }), error => error.code === "SOURCE_FORMAT_UNSUPPORTED");
  assert.throws(() => image.parseAttachment({ name: "payload.png", mime: "image/gif", contentString: `data:image/gif;base64,${buffer.toString("base64")}` }), error => error.code === "SOURCE_FORMAT_UNSUPPORTED");
  const huge = png(); huge.writeUInt32BE(10000, 16); huge.writeUInt32BE(10000, 20);
  const parsed = image.parseAttachment({ name: "huge.png", mime: "image/png", contentString: `data:image/png;base64,${huge.toString("base64")}` });
  assert.equal(parsed.actual, "png");
  assert.throws(() => image.parseAttachment({ name: "payload.png", mime: "image/jpeg", contentString: `data:image/png;base64,${buffer.toString("base64")}` }), error => error.code === "SOURCE_FORMAT_UNSUPPORTED");
  assert.throws(() => image.parseAttachment({ name: "../payload.png", mime: "image/png", contentString: `data:image/png;base64,${buffer.toString("base64")}` }), error => error.code === "SOURCE_UNAUTHORIZED");
  assert.throws(() => image.strictBase64("AAAAA"), error => error.code === "SOURCE_CORRUPT");
});

test("active core has no legacy-handler or global-fetch execution path", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "adapters.js"), "utf8");
  assert.doesNotMatch(source, /AAG_LEGACY|global\.fetch\s*=|aag-comfyui-(?:image-generator|reference-image)/);
  assert.equal(adapters.allowedLegacyPath, undefined);
});

test("direct Comfy graphs use only fixed workflow classes and fixed models", () => {
  const comfy = adapters.comfy;
  const model = comfy.PROFILES.fast;
  const generation = comfy.generationGraph({ model, prompt: "safe", seed: 1, width: 512, height: 512, prefix: "GEN-safe" });
  const subject = comfy.generalGraph({ model, referenceName: "AAG-Image-Agent-Source/safe.png", referenceMegapixels: 0.25, prompt: "safe", seed: 1, width: 512, height: 512, prefix: "REF-safe" });
  assert.ok(Object.values(generation).every(node => comfy.COMMON_NODES.includes(node.class_type)));
  assert.ok(Object.values(subject).every(node => [...comfy.COMMON_NODES, ...comfy.GENERAL_NODES].includes(node.class_type)));
  assert.equal(generation["1"].inputs.unet_name, "flux-2-klein-4b-fp8.safetensors");
  assert.equal(subject["4"].inputs.image, "AAG-Image-Agent-Source/safe.png");
});

test("ComfyUI launcher freezes offline and telemetry guards at process birth", () => {
  const launcher = fs.readFileSync(path.join(__dirname, "../integrations/launchers/aag-ai-start"), "utf8");
  for (const key of ["ORT_DISABLE_TELEMETRY", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"]) {
    assert.match(launcher, new RegExp(`export ${key}=1`));
  }
  assert.match(launcher, /python main[.]py/);
});
