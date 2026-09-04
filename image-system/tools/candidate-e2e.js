"use strict";

const fs = require("fs");
const runtime = require("../src/runtime");

function invocation(id, attachmentPath) {
  const value = {
    AAG_WORKSPACE_ID: "hardening-pass-2-staging",
    AAG_THREAD_ID: `candidate-${id}`,
    AAG_USER_ID: "authorized-tester",
    AAG_INVOCATION_UUID: `h2-${id}-${Date.now()}`,
    AAG_IMAGE_QUEUE_TIMEOUT_MS: 30 * 60 * 1000,
    AAG_IMAGE_LEASE_STALE_MS: 2 * 60 * 1000,
  };
  if (attachmentPath) {
    const bytes = fs.readFileSync(attachmentPath);
    value.AAG_INVOCATION_ATTACHMENTS = [{ name: "authorized-source.png", mime: "image/png", contentString: `data:image/png;base64,${bytes.toString("base64")}` }];
  }
  return value;
}

const cases = {
  "generate-count2": {
    args: { operation: "generate", request: "Create two distinct images of a translucent blue glass bird on a pale stone, soft morning light.", prompt: "A translucent blue glass bird on a pale stone, soft morning light, clean photographic composition", quality: "fast", count: 2, seed: 24082620, aspect_ratio: "1:1" },
  },
  "generate-quality": {
    args: { operation: "generate", request: "צור תמונה איכותית של בית אבן קטן ליד עץ זית בשעת שקיעה.", prompt: "A detailed small stone house beside an old olive tree at sunset, warm cinematic light", quality: "quality", count: 1, seed: 24082630, aspect_ratio: "4:3" },
  },
  "transform-subject": {
    attachment: "/tmp/aag-h2-subject.png",
    args: { operation: "transform", request: "Place the exact blue cube from the reference on green moss in a misty forest.", prompt: "Place the exact blue cube from the reference on green moss in a misty forest", preservation: "subject", source_policy: "current_attachment", quality: "fast", count: 1, seed: 24082640, aspect_ratio: "1:1" },
  },
  "upscale-2x": {
    attachment: "/tmp/aag-h2-subject.png",
    args: { operation: "upscale", request: "Upscale this exact image two times without changing its content.", source_policy: "current_attachment", scale: 2, count: 1 },
  },
};

async function main() {
  const id = process.argv[2];
  const selected = cases[id];
  if (!selected) throw new Error(`Unknown candidate case: ${id}`);
  const started = Date.now();
  const result = await runtime.createTask(selected.args, invocation(id, selected.attachment), { logger: message => process.stderr.write(`${message}\n`) });
  process.stdout.write(JSON.stringify({ case: id, started_at: new Date(started).toISOString(), elapsed_seconds: (Date.now() - started) / 1000, result }, null, 2) + "\n");
}

main().catch(error => { process.stderr.write(`${error?.stack || error}\n`); process.exitCode = 1; });
