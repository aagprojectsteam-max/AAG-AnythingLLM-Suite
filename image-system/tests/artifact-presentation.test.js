"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const presentation = require("../integrations/anythingllm/aagArtifactPresentation");

function png(width = 1152, height = 896) {
  const bytes = Buffer.alloc(128);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes, 0);
  bytes.writeUInt32BE(13, 8);
  bytes.write("IHDR", 12, "ascii");
  bytes.writeUInt32BE(width, 16);
  bytes.writeUInt32BE(height, 20);
  return bytes;
}

function envelope(bytes, overrides = {}) {
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  return [
    "AAG_IMAGE_RESULT",
    `status=${overrides.status || "completed"}`,
    "job_id=aag-83b7f687-7515-4436-8866-e5654c717967",
    "operation=transform",
    "workflow=transform.human.identity.scene.v1",
    "release=0.9.0-preview.8",
    "artifact_count=1",
    "artifact_1_id=artifact-29d58385-2b76-41a7-bc86-3aa88679d252",
    `artifact_1_url=${overrides.url || "http://127.0.0.1:18190/files/REF-1d56edd4-7c3a-467b-b8de-d9c8699f58f7.png"}`,
    `artifact_1_sha256=${overrides.sha256 || sha256}`,
    `artifact_1_dimensions=${overrides.dimensions || "1152x896"}`,
  ].join("\n");
}

function batchEnvelope(first, second, status = "completed") {
  const firstHash = crypto.createHash("sha256").update(first).digest("hex");
  const secondHash = crypto.createHash("sha256").update(second).digest("hex");
  return [
    "AAG_IMAGE_RESULT",
    `status=${status}`,
    "job_id=aag-83b7f687-7515-4436-8866-e5654c717967",
    "operation=multi_generate",
    "workflow=generation.batch.sequential.v1",
    "release=0.9.0-preview.11",
    "collection_id=aag-83b7f687-7515-4436-8866-e5654c717967",
    `plan_sha256=${"b".repeat(64)}`,
    "requested_count=2",
    "completed_count=2",
    "pending_count=0",
    "failed_count=0",
    "cancelled_count=0",
    "artifact_count=2",
    "artifact_1_id=artifact-29d58385-2b76-41a7-bc86-3aa88679d252",
    "artifact_1_url=http://127.0.0.1:18190/files/batch-one.png",
    `artifact_1_sha256=${firstHash}`,
    "artifact_1_dimensions=1152x896",
    "artifact_1_child_job_id=aag-11111111-1111-4111-8111-111111111111",
    "artifact_1_logical_index=1",
    "artifact_2_id=artifact-39d58385-2b76-41a7-bc86-3aa88679d252",
    "artifact_2_url=http://127.0.0.1:18190/files/batch-two.png",
    `artifact_2_sha256=${secondHash}`,
    "artifact_2_dimensions=896x1152",
    "artifact_2_child_job_id=aag-22222222-2222-4222-8222-222222222222",
    "artifact_2_logical_index=2",
    `batch_export_ready=${status === "completed"}`,
    `resume_supported=${status !== "completed"}`,
  ].join("\n");
}

test("canonical completed AAG results parse without provider identity", () => {
  const bytes = png();
  const parsed = presentation.parseAagImageResult(envelope(bytes));
  assert.equal(presentation.AAG_IMAGE_PROVIDER_POLICY, "OPEN_BY_CAPABILITY");
  assert.equal(parsed.status, "completed");
  assert.equal(parsed.artifacts.length, 1);
  assert.equal(parsed.artifacts[0].width, 1152);
  assert.equal(parsed.artifacts[0].height, 896);
});

test("non-canonical and arbitrary artifact URLs remain rejected", () => {
  const bytes = png();
  for (const url of [
    "https://example.com/image.png",
    "http://127.0.0.1:18190/other/image.png",
    "http://127.0.0.1:18190/files/../secret.png",
    "file:///tmp/image.png",
  ]) {
    assert.throws(
      () => presentation.parseAagImageResult(envelope(bytes, { url })),
      /AAG_ARTIFACT_PRESENTATION_FAILED/
    );
  }
});

test("artifact presentation verifies bytes, dimensions, hash, and deduplicates storage", async () => {
  const bytes = png();
  const parsed = presentation.parseAagImageResult(envelope(bytes));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aag-presentation-"));
  const fetchImpl = async () => ({
    ok: true,
    headers: { get: () => "image/png" },
    arrayBuffer: async () => bytes,
  });
  try {
    const first = await presentation.buildPresentationOutputs({
      parsed,
      prompt: "same person riding a camel",
      generatedImagesPath: dir,
      fetchImpl,
    });
    const second = await presentation.buildPresentationOutputs({
      parsed,
      prompt: "same person riding a camel",
      generatedImagesPath: dir,
      fetchImpl,
    });
    assert.deepEqual(second, first);
    assert.equal(fs.readdirSync(dir).length, 1);
    assert.equal(first[0].type, "imageGenerationCard");
    assert.equal(first[0].payload.artifactUrl, parsed.artifacts[0].url);
    assert.equal(first[0].payload.artifactSha256, parsed.artifacts[0].sha256);
    assert.deepEqual(fs.readFileSync(path.join(dir, first[0].payload.storageFilename)), bytes);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("hash or dimensions mismatch fails closed before card registration", async () => {
  const bytes = png();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aag-presentation-"));
  const fetchImpl = async () => ({
    ok: true,
    headers: { get: () => "image/png" },
    arrayBuffer: async () => bytes,
  });
  try {
    await assert.rejects(
      presentation.buildPresentationOutputs({
        parsed: presentation.parseAagImageResult(
          envelope(bytes, { sha256: "0".repeat(64) })
        ),
        prompt: "test",
        generatedImagesPath: dir,
        fetchImpl,
      }),
      /AAG_ARTIFACT_PRESENTATION_FAILED/
    );
    await assert.rejects(
      presentation.buildPresentationOutputs({
        parsed: presentation.parseAagImageResult(
          envelope(bytes, { dimensions: "896x1152" })
        ),
        prompt: "test",
        generatedImagesPath: dir,
        fetchImpl,
      }),
      /AAG_ARTIFACT_PRESENTATION_FAILED/
    );
    assert.deepEqual(fs.readdirSync(dir), []);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("installed adapter emits the native live image-card event and removes model-dependent URL markup", async () => {
  const bytes = png();
  const result = envelope(bytes);
  const task = {
    config: { hubId: "aag-image-task" },
    handler: async () => result,
  };
  const messages = [];
  const aibitat = {
    functions: new Map([["aag-image-task", task]]),
    _pendingOutputs: [],
    newMessage(message) {
      messages.push(message);
      return message;
    },
  };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aag-presentation-"));
  let authorizedOutputs = null;
  try {
    assert.equal(
      presentation.installAagArtifactPresentation({
        aibitat,
        generatedImagesPath: dir,
        fetchImpl: async () => ({
          ok: true,
          headers: { get: () => "image/png" },
          arrayBuffer: async () => bytes,
        }),
        authorizeOutputs: async (outputs) => {
          authorizedOutputs = outputs;
        },
      }),
      true
    );
    assert.equal(await task.handler({ prompt: "girl riding a camel" }), result);
    aibitat.newMessage({
      from: "@agent",
      to: "USER",
      content:
        "![result](http://127.0.0.1:18190/files/REF-1d56edd4-7c3a-467b-b8de-d9c8699f58f7.png)",
    });
    assert.equal(messages[0].type, "imageGenerationCard");
    assert.equal(messages[0].content, "Image generated successfully.");
    assert.equal(messages[0].outputs.length, 1);
    assert.equal(messages[0].outputs[0].type, "imageGenerationCard");
    assert.equal(aibitat._pendingOutputs.length, 1);
    assert.deepEqual(authorizedOutputs, messages[0].outputs);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("batch presentation remains one native response with ordered collection/export metadata", async () => {
  const first = png(1152, 896);
  const second = Buffer.from(png(896, 1152));
  second[40] = 9;
  const result = batchEnvelope(first, second);
  const task = { config: { hubId: "aag-image-batch" }, handler: async () => result };
  const messages = [];
  const aibitat = {
    functions: new Map([["aag-image-batch", task]]),
    _pendingOutputs: [],
    newMessage(message) { messages.push(message); return message; },
  };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aag-presentation-"));
  try {
    assert.equal(presentation.installAagArtifactPresentation({
      aibitat,
      generatedImagesPath: dir,
      fetchImpl: async (url) => ({
        ok: true,
        headers: { get: () => "image/png" },
        arrayBuffer: async () => String(url).includes("batch-two") ? second : first,
      }),
    }), true);
    await task.handler({
      collection_brief: "Two ordered scenes",
      items: [{ prompt: "First planned scene" }, { prompt: "Second planned scene" }],
    });
    aibitat.newMessage({ from: "@agent", to: "USER", content: "Done." });
    assert.equal(messages.length, 1);
    assert.equal(messages[0].type, "imageGenerationCard");
    assert.equal(messages[0].outputs.length, 2);
    assert.deepEqual(messages[0].outputs.map((output) => output.payload.logicalIndex), [1, 2]);
    assert.deepEqual(messages[0].outputs.map((output) => output.payload.prompt), ["First planned scene", "Second planned scene"]);
    assert.ok(messages[0].outputs.every((output) => output.payload.collectionComplete === true));
    assert.ok(messages[0].outputs.every((output) => output.payload.artifactExport.schema === "aag.trusted-artifact-export.v1"));
    assert.deepEqual(fs.readdirSync(dir).length, 2);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("failed and non-AAG tool results do not register presentation outputs", async () => {
  assert.equal(
    presentation.parseAagImageResult("AAG_IMAGE_RESULT\nstatus=failed\nartifact_count=0"),
    null
  );
  assert.equal(presentation.parseAagImageResult("ordinary tool text"), null);
});
